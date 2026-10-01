//! Cancellation, signal handling, and bounded subprocess lifecycle.

use super::{MeasureError, Result};
use std::collections::HashSet;
use std::io::{self, Read};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, Weak};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

#[derive(Default, Debug)]
struct Cancellation {
    cancelled: AtomicBool,
    processes: Mutex<HashSet<u32>>,
    children: Mutex<Vec<Weak<Cancellation>>>,
}

/// A shared stop signal. cancel() kills active process groups synchronously,
/// including on quit when the application can exit before a worker next polls.
#[derive(Clone, Default, Debug)]
pub struct CancelToken(Arc<Cancellation>);

impl CancelToken {
    pub fn cancel(&self) {
        self.0.cancelled.store(true, Ordering::Release);
        let processes = self.processes();
        #[cfg(unix)]
        for process in processes.iter() {
            // SAFETY: registered children own dedicated process groups. The
            // registry lock also guards spawning and reaping against PID reuse.
            unsafe {
                libc::kill(-(*process as libc::pid_t), libc::SIGKILL);
            }
        }
        drop(processes);
        let children: Vec<_> = self
            .0
            .children
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .iter()
            .filter_map(Weak::upgrade)
            .collect();
        for child in children {
            Self(child).cancel();
        }
    }

    pub fn is_cancelled(&self) -> bool {
        self.0.cancelled.load(Ordering::Acquire)
    }

    /// Cancel a worker independently while letting the parent stop all of its
    /// workers synchronously when the terminal exits.
    pub fn child(&self) -> Self {
        let child = Self::default();
        self.0
            .children
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .push(Arc::downgrade(&child.0));
        if self.is_cancelled() {
            child.cancel();
        }
        child
    }

    fn processes(&self) -> MutexGuard<'_, HashSet<u32>> {
        self.0
            .processes
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    pub(super) fn check(&self) -> Result<()> {
        if self.is_cancelled() {
            Err(MeasureError::Cancelled)
        } else {
            Ok(())
        }
    }

    pub(super) fn wait(&self, duration: Duration) -> bool {
        let start = Instant::now();
        while !self.is_cancelled() && start.elapsed() < duration {
            thread::sleep(Duration::from_millis(20).min(duration - start.elapsed().min(duration)));
        }
        !self.is_cancelled()
    }
}

/// Own the signal listener so a completed CLI command can unregister it.
pub struct InterruptGuard {
    #[cfg(unix)]
    handle: signal_hook::iterator::Handle,
    thread: Option<JoinHandle<()>>,
}

impl Drop for InterruptGuard {
    fn drop(&mut self) {
        #[cfg(unix)]
        self.handle.close();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

/// Deliver SIGINT/SIGTERM through signal-hook's self-pipe on an ordinary
/// thread. Registry locking and subprocess cancellation never run inside a
/// signal handler. Source: signal-hook 0.3 iterator module documentation.
pub fn install_interrupt_handler(cancel: &CancelToken) -> Result<InterruptGuard> {
    #[cfg(unix)]
    {
        use signal_hook::{
            consts::signal::{SIGINT, SIGTERM},
            iterator::Signals,
        };
        let mut signals = Signals::new([SIGINT, SIGTERM])
            .map_err(|error| MeasureError::Backend(format!("signal handler: {error}")))?;
        let handle = signals.handle();
        let cancel = cancel.clone();
        let thread = thread::spawn(move || {
            if signals.forever().next().is_some() {
                cancel.cancel();
            }
        });
        Ok(InterruptGuard {
            handle,
            thread: Some(thread),
        })
    }
    #[cfg(not(unix))]
    {
        let _ = cancel;
        Ok(InterruptGuard { thread: None })
    }
}

pub(super) struct ProcessOutput {
    pub(super) status: ExitStatus,
    pub(super) stdout: String,
    pub(super) stderr: String,
}

/// Own the process group so cancellation also stops descendants that inherited
/// stdout/stderr. Rust's Child has no automatic kill/wait behavior on Drop.
struct ChildGuard {
    child: Child,
    finished: bool,
    cancel: CancelToken,
}

impl ChildGuard {
    fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
        let mut processes = self.cancel.processes();
        let status = self.child.try_wait()?;
        if status.is_some() {
            processes.remove(&self.child.id());
            self.finished = true;
        }
        Ok(status)
    }
}

impl Drop for ChildGuard {
    fn drop(&mut self) {
        if self.finished {
            return;
        }
        let mut processes = self.cancel.processes();
        processes.remove(&self.child.id());
        #[cfg(unix)]
        // SAFETY: Command::process_group(0) gives this child its own group;
        // a negative PID targets that group, never the application's group.
        unsafe {
            libc::kill(-(self.child.id() as libc::pid_t), libc::SIGKILL);
        }
        // Release before waiting; terminal cancellation only sends signals and
        // never waits for a blocked backend to finish reaping.
        drop(processes);
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[cfg(unix)]
fn nonblocking<T: std::os::fd::AsRawFd>(pipe: &T) -> io::Result<()> {
    use std::os::fd::AsRawFd;
    let fd = AsRawFd::as_raw_fd(pipe);
    // SAFETY: fd belongs to the live pipe and fcntl does not take ownership.
    unsafe {
        let flags = libc::fcntl(fd, libc::F_GETFL);
        if flags < 0 || libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) < 0 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

fn drain(pipe: &mut impl Read, bytes: &mut Vec<u8>) -> io::Result<()> {
    let mut buf = [0; 8192];
    loop {
        match pipe.read(&mut buf) {
            Ok(0) => return Ok(()),
            Ok(count) => {
                if bytes.len() + count > 4 * 1024 * 1024 {
                    return Err(io::Error::other("probe output exceeded 4 MiB"));
                }
                bytes.extend_from_slice(&buf[..count]);
            }
            Err(err) if err.kind() == io::ErrorKind::WouldBlock => return Ok(()),
            Err(err) if err.kind() == io::ErrorKind::Interrupted => continue,
            Err(err) => return Err(err),
        }
    }
}

pub(super) fn run_command(
    program: &str,
    args: &[&str],
    timeout: Duration,
    cancel: &CancelToken,
) -> Result<ProcessOutput> {
    cancel.check()?;
    let mut command = Command::new(program);
    command
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    let mut processes = cancel.processes();
    cancel.check()?;
    let child = command.spawn().map_err(|err| {
        if program == "speedtest" && err.kind() == io::ErrorKind::NotFound {
            MeasureError::SpeedtestUnavailable
        } else {
            MeasureError::Backend(format!("{program}: {err}"))
        }
    })?;
    processes.insert(child.id());
    drop(processes);
    let mut child = ChildGuard {
        child,
        finished: false,
        cancel: cancel.clone(),
    };
    let mut stdout = child
        .child
        .stdout
        .take()
        .ok_or_else(|| MeasureError::Backend("missing stdout pipe".into()))?;
    let mut stderr = child
        .child
        .stderr
        .take()
        .ok_or_else(|| MeasureError::Backend("missing stderr pipe".into()))?;
    #[cfg(unix)]
    {
        nonblocking(&stdout).map_err(|err| MeasureError::Backend(err.to_string()))?;
        nonblocking(&stderr).map_err(|err| MeasureError::Backend(err.to_string()))?;
    }
    let start = Instant::now();
    let (mut out, mut err) = (Vec::new(), Vec::new());
    loop {
        cancel.check()?;
        if start.elapsed() >= timeout {
            return Err(MeasureError::Backend(format!(
                "{program} timed out after {:.1}s",
                timeout.as_secs_f64()
            )));
        }
        drain(&mut stdout, &mut out).map_err(|err| MeasureError::Backend(err.to_string()))?;
        drain(&mut stderr, &mut err).map_err(|err| MeasureError::Backend(err.to_string()))?;
        if let Some(status) = child
            .try_wait()
            .map_err(|err| MeasureError::Backend(err.to_string()))?
        {
            cancel.check()?;
            drain(&mut stdout, &mut out).map_err(|err| MeasureError::Backend(err.to_string()))?;
            drain(&mut stderr, &mut err).map_err(|err| MeasureError::Backend(err.to_string()))?;
            return Ok(ProcessOutput {
                status,
                stdout: String::from_utf8_lossy(&out).into_owned(),
                stderr: String::from_utf8_lossy(&err).into_owned(),
            });
        }
        thread::sleep(Duration::from_millis(20));
    }
}

#[cfg(test)]
#[path = "tests/process.rs"]
mod tests;
