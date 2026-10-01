//! Native WiFi, throughput, and path-quality probes.

use crate::model::{PathFields, Signal, Speed};
use crate::path_diagnostics::{LatencyPhase, LoadLatency, PathDiagnostics};
use std::collections::{HashSet, VecDeque};
use std::io::{self, Read};
use std::net::IpAddr;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock, Weak, mpsc};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

pub type Result<T> = std::result::Result<T, MeasureError>;

#[derive(Debug, thiserror::Error)]
pub enum MeasureError {
    #[error("{0}")]
    NoWifi(String),
    #[error("measurement backend unavailable: {0}")]
    Backend(String),
    #[error("Ookla speedtest is unavailable; install speedtest or use --no-speedtest")]
    SpeedtestUnavailable,
    #[error("speedtest failed: {0}")]
    SpeedtestFailed(String),
    #[error("measurement cancelled")]
    Cancelled,
}

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

    fn check(&self) -> Result<()> {
        if self.is_cancelled() {
            Err(MeasureError::Cancelled)
        } else {
            Ok(())
        }
    }

    fn wait(&self, duration: Duration) -> bool {
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

struct ProcessOutput {
    status: ExitStatus,
    stdout: String,
    stderr: String,
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

fn run_command(
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

fn clean_ssid(value: &str) -> Option<String> {
    let value = value.trim().trim_matches(['\'', '"']);
    if value.is_empty() || ["<hidden>", "<redacted>"].contains(&value.to_lowercase().as_str()) {
        None
    } else {
        Some(value.to_string())
    }
}

fn clean_bssid(value: &str) -> Option<String> {
    let value = value.trim().replace('-', ":").to_lowercase();
    let parts: Vec<_> = value.split(':').collect();
    if parts.len() == 6
        && value != "00:00:00:00:00:00"
        && value != "ff:ff:ff:ff:ff:ff"
        && parts
            .iter()
            .all(|p| p.len() == 2 && p.bytes().all(|b| b.is_ascii_hexdigit()))
    {
        Some(value)
    } else {
        None
    }
}

fn parse_helper_identity(text: &str) -> Option<(String, Option<String>)> {
    let value: serde_json::Value = serde_json::from_str(text).ok()?;
    if !matches!(value["status"].as_str(), Some("connected" | "ok")) {
        return None;
    }
    let ssid = clean_ssid(value["ssid"].as_str()?)?;
    let bssid = value["bssid"].as_str().and_then(clean_bssid);
    Some((ssid, bssid))
}

fn executable(path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        path.is_file()
            && path
                .metadata()
                .is_ok_and(|m| m.permissions().mode() & 0o111 != 0)
    }
    #[cfg(not(unix))]
    path.is_file()
}

fn version_key(version: &str) -> (Vec<u64>, bool, String) {
    let end = version
        .find(|c: char| !c.is_ascii_digit() && c != '.')
        .unwrap_or(version.len());
    let mut numbers: Vec<_> = version[..end]
        .trim_end_matches('.')
        .split('.')
        .filter_map(|v| v.parse().ok())
        .collect();
    numbers.resize(numbers.len().max(4), 0);
    (numbers, end == version.len(), version.to_string())
}

fn find_helper() -> Option<PathBuf> {
    if let Some(explicit) = std::env::var_os("WIFIMAP_WIFIWAND_HELPER") {
        let path = PathBuf::from(explicit);
        let path = if let Ok(suffix) = path.strip_prefix("~") {
            PathBuf::from(std::env::var_os("HOME")?).join(suffix)
        } else {
            path
        };
        return executable(&path).then_some(path);
    }
    if let Some(path) = std::env::var_os("PATH") {
        for directory in std::env::split_paths(&path) {
            let helper = directory.join("wifiwand-helper");
            if executable(&helper) {
                return Some(helper);
            }
        }
    }
    let root =
        PathBuf::from(std::env::var_os("HOME")?).join("Library/Application Support/WifiWand");
    std::fs::read_dir(root)
        .ok()?
        .filter_map(|entry| entry.ok())
        .filter_map(|entry| {
            let helper = entry
                .path()
                .join("wifiwand-helper.app/Contents/MacOS/wifiwand-helper");
            executable(&helper).then(|| (version_key(&entry.file_name().to_string_lossy()), helper))
        })
        .max_by(|a, b| a.0.cmp(&b.0))
        .map(|(_, path)| path)
}

type Identity = (String, Option<String>);

#[derive(Default)]
struct IdentityCache {
    checked_at: Option<Instant>,
    identity: Option<Identity>,
}

fn helper_identity(max_age: Duration, force: bool, cancel: &CancelToken) -> Option<Identity> {
    static CACHE: OnceLock<Mutex<IdentityCache>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(IdentityCache::default()));
    if let Ok(cached) = cache.lock() {
        let ttl = if cached.identity.is_some() {
            max_age
        } else {
            Duration::from_secs(1)
        };
        if !force && cached.checked_at.is_some_and(|time| time.elapsed() < ttl) {
            return cached.identity.clone();
        }
    }
    let identity = find_helper().and_then(|path| {
        let output = run_command(
            path.to_str()?,
            &["current-network"],
            Duration::from_secs(3),
            cancel,
        )
        .ok()?;
        output
            .status
            .success()
            .then(|| parse_helper_identity(&output.stdout))
            .flatten()
    });
    if !cancel.is_cancelled()
        && let Ok(mut cached) = cache.lock()
    {
        cached.checked_at = Some(Instant::now());
        cached.identity = identity.clone();
    }
    identity
}

/// Read a single instantaneous radio sample. No command interpreters or runtime
/// language helpers are used; WifiWand, when installed, is a signed executable.
pub fn sample_signal() -> Result<Signal> {
    sample_signal_with_cancel(&CancelToken::default())
}

pub fn sample_signal_with_cancel(cancel: &CancelToken) -> Result<Signal> {
    cancel.check()?;
    let mut signal = match corewlan::signal() {
        Ok(signal) => signal,
        Err(error @ MeasureError::NoWifi(_)) => return Err(error),
        Err(_) => {
            let output = run_command(
                "/usr/sbin/system_profiler",
                &["SPAirPortDataType"],
                Duration::from_secs(30),
                cancel,
            )?;
            if !output.status.success() {
                return Err(MeasureError::Backend(format!(
                    "system_profiler failed: {}",
                    output.stderr.trim()
                )));
            }
            parse_profiler(&output.stdout)?
        }
    };
    if (signal.ssid.is_none() || signal.bssid.is_none())
        && let Some(identity) = helper_identity(Duration::from_secs(1), false, cancel)
    {
        apply_identity(&mut signal, identity, false);
    }
    cancel.check()?;
    Ok(signal)
}

/// Average RSSI and noise; retain the last association's other properties.
pub fn sample_signal_for(duration: Duration, cancel: &CancelToken) -> Result<Signal> {
    let start = Instant::now();
    let mut samples = vec![sample_signal_with_cancel(cancel)?];
    while start.elapsed() < duration {
        cancel.wait(Duration::from_millis(500).min(duration - start.elapsed().min(duration)));
        cancel.check()?;
        samples.push(sample_signal_with_cancel(cancel)?);
    }
    let mut signal = average_signal_samples(&samples);
    refresh_signal_identity(&mut signal, cancel)?;
    Ok(signal)
}

fn average_signal_samples(samples: &[Signal]) -> Signal {
    let average = |values: Vec<f64>| -> Option<f64> {
        (!values.is_empty())
            .then(|| (values.iter().sum::<f64>() / values.len() as f64).round_ties_even())
    };
    let rssi = average(samples.iter().filter_map(|s| s.rssi).collect());
    let noise = average(samples.iter().filter_map(|s| s.noise).collect());
    let mut signal = samples.last().cloned().unwrap_or_default();
    signal.rssi = rssi;
    signal.noise = noise;
    signal.snr = rssi.zip(noise).map(|(rssi, noise)| rssi - noise);
    signal
}

fn apply_identity(signal: &mut Signal, (ssid, bssid): Identity, refresh_bssid: bool) {
    if signal.ssid.is_none() || signal.ssid.as_ref() == Some(&ssid) {
        signal.ssid.get_or_insert(ssid);
        if bssid.is_some() && (refresh_bssid || signal.bssid.is_none()) {
            signal.bssid = bssid;
        }
    }
}

/// Force a helper refresh before saving, so live display caching cannot
/// attribute a reading to an access point used before a roam. Run on a worker.
pub fn refresh_signal_identity(signal: &mut Signal, cancel: &CancelToken) -> Result<()> {
    cancel.check()?;
    if let Some(identity) = helper_identity(Duration::ZERO, true, cancel) {
        apply_identity(signal, identity, true);
    }
    cancel.check()
}

pub fn read_network_identity() -> Result<(Option<String>, Option<String>)> {
    read_network_identity_with_cancel(&CancelToken::default())
}

pub fn read_network_identity_with_cancel(
    cancel: &CancelToken,
) -> Result<(Option<String>, Option<String>)> {
    cancel.check()?;
    let (mut ssid, mut bssid, interface) = corewlan::identity().unwrap_or_default();
    if (ssid.is_none() || bssid.is_none())
        && let Some((helper_ssid, helper_bssid)) =
            helper_identity(Duration::from_secs(1), false, cancel)
        && (ssid.is_none() || ssid.as_ref() == Some(&helper_ssid))
    {
        ssid = Some(helper_ssid);
        bssid = helper_bssid.or(bssid);
    }
    if ssid.is_none() {
        let interface = interface.or_else(|| wifi_interface(cancel));
        if let Some(interface) = interface
            && let Ok(output) = run_command(
                "/usr/sbin/networksetup",
                &["-getairportnetwork", &interface],
                Duration::from_secs(5),
                cancel,
            )
            && output.status.success()
        {
            ssid = parse_networksetup_ssid(&output.stdout);
        }
    }
    cancel.check()?;
    Ok((ssid, bssid))
}

fn wifi_interface(cancel: &CancelToken) -> Option<String> {
    if let Ok((_, _, Some(interface))) = corewlan::identity() {
        return Some(interface);
    }
    let output = run_command(
        "/usr/sbin/networksetup",
        &["-listallhardwareports"],
        Duration::from_secs(5),
        cancel,
    )
    .ok()?;
    let mut wifi = false;
    for line in output.stdout.lines() {
        if let Some(port) = line.trim().strip_prefix("Hardware Port:") {
            wifi = matches!(port.trim(), "Wi-Fi" | "AirPort");
        } else if wifi && let Some(device) = line.trim().strip_prefix("Device:") {
            return Some(device.trim().into());
        }
    }
    None
}

fn parse_networksetup_ssid(text: &str) -> Option<String> {
    text.lines().find_map(|line| {
        let (label, value) = line.trim().split_once(':')?;
        matches!(
            label.trim(),
            "Current Wi-Fi Network" | "Current AirPort Network"
        )
        .then(|| clean_ssid(value))
        .flatten()
    })
}

fn parse_profiler(text: &str) -> Result<Signal> {
    let mut sample = Signal::default();
    let current = text
        .find("Current Network Information:")
        .or_else(|| text.find("Current Wireless Network:"));
    if current.is_none() && text.contains("Other Local Wi-Fi Networks:") {
        return Err(MeasureError::NoWifi("WiFi is not associated".into()));
    }
    let text = current.map_or(text, |index| &text[index..]);
    for line in text.lines() {
        let line = line.trim();
        if line.starts_with("Other Local Wi-Fi Networks:") {
            break;
        }
        if let Some(value) = line.strip_prefix("Signal / Noise:") {
            if let Some((rssi, noise)) = value.split_once('/') {
                sample.rssi = rssi.split_whitespace().next().and_then(|v| v.parse().ok());
                sample.noise = noise.split_whitespace().next().and_then(|v| v.parse().ok());
            }
        } else if let Some(value) = line.strip_prefix("Channel:") {
            sample.channel = Some(value.trim().into());
        } else if let Some(value) = line.strip_prefix("PHY Mode:") {
            sample.phy = Some(value.trim().into());
        } else if let Some(value) = line.strip_prefix("Transmit Rate:") {
            sample.tx_rate = value.split_whitespace().next().map(str::to_string);
        }
    }
    if sample
        .rssi
        .is_none_or(|rssi| !rssi.is_finite() || rssi >= 0.)
    {
        return Err(MeasureError::NoWifi(
            "no associated network in system_profiler output".into(),
        ));
    }
    sample.snr = sample
        .rssi
        .zip(sample.noise)
        .map(|(rssi, noise)| rssi - noise);
    Ok(sample)
}

pub fn demo_signal(tick: u64) -> Signal {
    let rssi = -55. - ((tick as f64 / 5.).sin() * 12.).round();
    Signal {
        ssid: Some("DEMO · Survey WiFi".into()),
        bssid: Some(
            if tick % 40 < 20 {
                "02:00:00:00:00:01"
            } else {
                "02:00:00:00:00:02"
            }
            .into(),
        ),
        rssi: Some(rssi),
        noise: Some(-92.),
        snr: Some(rssi + 92.),
        channel: Some("36 (80MHz)".into()),
        phy: Some("802.11ax".into()),
        tx_rate: Some("866.7".into()),
        mcs: Some(9),
        band: Some("5 GHz".into()),
        security: Some("WPA3 Personal".into()),
    }
}

pub fn run_speedtest() -> Result<Speed> {
    run_speedtest_with_cancel(&CancelToken::default())
}

pub fn run_speedtest_with_cancel(cancel: &CancelToken) -> Result<Speed> {
    cancel.check()?;
    let interface = wifi_interface(cancel)
        .ok_or_else(|| MeasureError::NoWifi("no WiFi interface available".into()))?;
    run_speedtest_on_interface(&interface, cancel)
}

fn run_speedtest_on_interface(interface: &str, cancel: &CancelToken) -> Result<Speed> {
    let output = run_command(
        "speedtest",
        &[
            "--format=json",
            "--accept-license",
            "--accept-gdpr",
            "--interface",
            interface,
        ],
        Duration::from_secs(120),
        cancel,
    )
    .map_err(|error| match error {
        MeasureError::Cancelled | MeasureError::SpeedtestUnavailable => error,
        _ => MeasureError::SpeedtestFailed(error.to_string()),
    })?;
    if !output.status.success() {
        return Err(MeasureError::SpeedtestFailed(
            output.stderr.trim().to_string(),
        ));
    }
    parse_speedtest(&output.stdout)
}

/// Compare independent idle, speedtest-running, and recovery probe windows.
/// The load interval spans Ookla's complete run, including server selection.
pub fn run_speedtest_diagnostics(cancel: &CancelToken) -> (Result<Speed>, LoadLatency) {
    if let Err(error) = cancel.check() {
        return (Err(error), LoadLatency::default());
    }
    let Some(interface) = wifi_interface(cancel) else {
        let error = if cancel.is_cancelled() {
            MeasureError::Cancelled
        } else {
            MeasureError::NoWifi("no WiFi interface available".into())
        };
        return (Err(error), LoadLatency::default());
    };
    let epoch = Instant::now();
    let mut internet_target = "1.1.1.1".to_string();
    let mut probe_count = 0;
    let mut previous_gateway = None;
    let mut gateway_changed = false;
    let (result, mut report) = run_connection_load_measurement(
        cancel,
        || run_speedtest_on_interface(&interface, cancel),
        || {
            probe_count += 1;
            let gateway = gateway_on_interface(&interface, cancel);
            if previous_gateway
                .as_ref()
                .is_some_and(|previous| previous != &gateway)
            {
                gateway_changed = true;
            }
            previous_gateway = Some(gateway.clone());
            probe_path(
                Some(&interface),
                gateway,
                &mut internet_target,
                epoch,
                probe_count <= 5,
                cancel,
            )
        },
        || {
            read_network_identity_with_cancel(cancel)
                .ok()
                .and_then(|(ssid, _)| ssid)
        },
        Duration::from_secs(1),
    );
    if gateway_changed && !cancel.is_cancelled() {
        invalidate_gateway_comparison(&mut report);
    }
    (result, report)
}

fn invalidate_gateway_comparison(report: &mut LoadLatency) {
    report.gateway_changed = true;
    for phase in [&mut report.idle, &mut report.loaded, &mut report.recovery] {
        phase.path.gateway_rtt_ms = None;
        phase.path.gateway_p95_ms = None;
        phase.path.gateway_loss_pct = None;
        phase.path.gateway_max_outage_ms = None;
        phase.diagnostics.gateway_jitter_ms = None;
        phase.diagnostics.gateway_latest_ms = None;
    }
}

fn run_connection_load_measurement(
    cancel: &CancelToken,
    speedtest: impl FnOnce() -> Result<Speed> + Send,
    mut probe: impl FnMut() -> PathSample,
    mut identity: impl FnMut() -> Option<String>,
    interval: Duration,
) -> (Result<Speed>, LoadLatency) {
    let mut known_ssid = None;
    let mut connection_changed = false;
    let (result, mut report) = run_load_measurement(
        cancel,
        speedtest,
        || {
            if let Some(ssid) = identity() {
                connection_changed |= known_ssid.as_ref().is_some_and(|known| known != &ssid);
                known_ssid = Some(ssid);
            }
            probe()
        },
        interval,
    );
    if connection_changed && !cancel.is_cancelled() {
        report.connection_changed = true;
        report.loaded = LatencyPhase::default();
        report.recovery = LatencyPhase::default();
    }
    (result, report)
}

fn collect_latency_phase(
    probe: &mut impl FnMut() -> PathSample,
    interval: Duration,
    cancel: &CancelToken,
) -> LatencyPhase {
    let mut tracker = PathTracker::new(5, interval);
    for index in 0..5 {
        if cancel.is_cancelled() {
            break;
        }
        let started = Instant::now();
        tracker.record_sample(&probe());
        if index < 4 && !cancel.wait(interval.saturating_sub(started.elapsed())) {
            break;
        }
    }
    tracker.phase()
}

fn run_load_measurement(
    cancel: &CancelToken,
    speedtest: impl FnOnce() -> Result<Speed> + Send,
    mut probe: impl FnMut() -> PathSample,
    interval: Duration,
) -> (Result<Speed>, LoadLatency) {
    if cancel.is_cancelled() {
        return (Err(MeasureError::Cancelled), LoadLatency::default());
    }
    let idle = collect_latency_phase(&mut probe, interval, cancel);
    if cancel.is_cancelled() {
        return (Err(MeasureError::Cancelled), LoadLatency::default());
    }
    let (result, loaded) = thread::scope(|scope| {
        let speedtest = scope.spawn(speedtest);
        let mut tracker = PathTracker::new(120, interval);
        while !speedtest.is_finished() && !cancel.is_cancelled() {
            let started = Instant::now();
            let sample = probe();
            // A probe completed after the load stopped belongs to neither the
            // loaded window nor its independently collected recovery window.
            if speedtest.is_finished() || cancel.is_cancelled() {
                break;
            }
            tracker.record_sample(&sample);
            if !cancel.wait(interval.saturating_sub(started.elapsed())) {
                break;
            }
        }
        let result = speedtest.join().unwrap_or_else(|_| {
            Err(MeasureError::SpeedtestFailed(
                "worker stopped unexpectedly".into(),
            ))
        });
        (result, tracker.phase())
    });
    if cancel.is_cancelled() {
        return (Err(MeasureError::Cancelled), LoadLatency::default());
    }
    if result.is_err() {
        return (
            result,
            LoadLatency {
                idle,
                ..LoadLatency::default()
            },
        );
    }
    let recovery = collect_latency_phase(&mut probe, interval, cancel);
    if cancel.is_cancelled() {
        return (Err(MeasureError::Cancelled), LoadLatency::default());
    }
    (
        result,
        LoadLatency {
            idle,
            loaded,
            recovery,
            ..Default::default()
        },
    )
}

fn parse_speedtest(text: &str) -> Result<Speed> {
    let value: serde_json::Value = serde_json::from_str(text)
        .map_err(|_| MeasureError::SpeedtestFailed("invalid JSON".into()))?;
    let metric = |section: &str, field: &str| -> Result<f64> {
        value[section][field]
            .as_f64()
            .filter(|v| v.is_finite() && *v >= 0.)
            .ok_or_else(|| {
                MeasureError::SpeedtestFailed(format!("missing or invalid {section}.{field}"))
            })
    };
    let ping_ms = metric("ping", "latency")?;
    // Ookla's bandwidth is bytes/sec, so one Mbps equals 125,000 bytes/sec.
    let down_mbps = metric("download", "bandwidth")? / 125_000.;
    let up_mbps = metric("upload", "bandwidth")? / 125_000.;
    let name = value["server"]["name"]
        .as_str()
        .filter(|name| !name.is_empty());
    let id = match &value["server"]["id"] {
        serde_json::Value::Null => None,
        serde_json::Value::String(value) => Some(value.clone()),
        serde_json::Value::Number(value) => Some(value.to_string()),
        _ => None,
    };
    let server = match (name, id) {
        (Some(name), Some(id)) => Some(format!("{name} ({id})")),
        (Some(name), None) => Some(name.into()),
        (None, Some(id)) => Some(format!("id:{id}")),
        _ => None,
    };
    Ok(Speed {
        ping_ms: Some(ping_ms),
        down_mbps: Some(down_mbps),
        up_mbps: Some(up_mbps),
        server,
    })
}

pub fn gateway() -> Option<String> {
    gateway_with_cancel(&CancelToken::default())
}

pub fn gateway_with_cancel(cancel: &CancelToken) -> Option<String> {
    gateway_on_interface(&wifi_interface(cancel)?, cancel)
}

fn gateway_on_interface(interface: &str, cancel: &CancelToken) -> Option<String> {
    let output = run_command(
        "/sbin/route",
        &["-n", "get", "-ifscope", interface, "default"],
        Duration::from_secs(2),
        cancel,
    )
    .ok()?;
    output
        .status
        .success()
        .then(|| parse_route_field(&output.stdout, "interface"))
        .flatten()
        .filter(|route_interface| route_interface == interface)?;
    parse_route_field(&output.stdout, "gateway").filter(|gateway| gateway.parse::<IpAddr>().is_ok())
}

fn parse_route_field(text: &str, field: &str) -> Option<String> {
    text.lines().find_map(|line| {
        let (label, value) = line.trim().split_once(':')?;
        (label == field).then(|| value.trim().to_string())
    })
}

pub fn parse_ping_rtt(text: &str) -> Option<f64> {
    for (index, _) in text.match_indices("time") {
        let rest = text[index + 4..].trim_start();
        let operator = rest.chars().next()?;
        if !matches!(operator, '=' | '<') {
            continue;
        }
        let number = rest[1..].trim_start();
        let end = number
            .find(|c: char| !c.is_ascii_digit() && c != '.')
            .unwrap_or(number.len());
        let value: f64 = number[..end].parse().ok()?;
        if number[end..].trim_start().starts_with("ms") && value.is_finite() && value >= 0. {
            return Some(if operator == '<' {
                value.max(1.)
            } else {
                value
            });
        }
    }
    None
}

pub fn ping_once(target: &str, cancel: &CancelToken) -> Option<f64> {
    target.parse::<IpAddr>().ok()?;
    ping_on_interface(target, &wifi_interface(cancel)?, cancel)
}

fn ping_on_interface(target: &str, interface: &str, cancel: &CancelToken) -> Option<f64> {
    target.parse::<IpAddr>().ok()?;
    let wait = if cfg!(target_os = "macos") {
        "1000"
    } else {
        "1"
    };
    let output = run_command(
        "/sbin/ping",
        &["-n", "-c", "1", "-W", wait, "-b", interface, target],
        Duration::from_millis(1500),
        cancel,
    )
    .ok()?;
    output
        .status
        .success()
        .then(|| parse_ping_rtt(&output.stdout))
        .flatten()
}

fn probe_path(
    interface: Option<&str>,
    gateway: Option<String>,
    internet_target: &mut String,
    epoch: Instant,
    allow_fallback: bool,
    cancel: &CancelToken,
) -> PathSample {
    let Some(interface) = interface else {
        return PathSample {
            gateway_target: None,
            gateway_ms: None,
            internet_target: String::new(),
            internet_ms: None,
            primary_failed: false,
            observed_at: epoch.elapsed(),
        };
    };
    thread::scope(|scope| {
        let probe_gateway = gateway.clone();
        let gateway_probe = scope.spawn(move || {
            probe_gateway
                .as_deref()
                .and_then(|target| ping_on_interface(target, interface, cancel))
        });
        let mut internet_ms = ping_on_interface(internet_target, interface, cancel);
        let primary_failed = internet_ms.is_none() && internet_target == "1.1.1.1";
        if allow_fallback
            && primary_failed
            && !cancel.is_cancelled()
            && let Some(reply) = ping_on_interface("8.8.8.8", interface, cancel)
        {
            // Keep a successful fallback target for this worker's remaining
            // samples instead of alternating different hosts' RTTs.
            internet_ms = Some(reply);
            *internet_target = "8.8.8.8".into();
        }
        let gateway_ms = gateway_probe.join().unwrap_or(None);
        PathSample {
            gateway_target: gateway,
            gateway_ms,
            internet_target: internet_target.clone(),
            internet_ms,
            primary_failed,
            observed_at: epoch.elapsed(),
        }
    })
}

#[derive(Clone, Debug)]
pub struct PathSample {
    pub gateway_target: Option<String>,
    pub gateway_ms: Option<f64>,
    pub internet_target: String,
    pub internet_ms: Option<f64>,
    pub primary_failed: bool,
    pub observed_at: Duration,
}

#[derive(Default)]
struct ProbeSummary {
    median: Option<f64>,
    p95: Option<f64>,
    loss: Option<f64>,
    outage_ms: Option<i64>,
}

struct ProbeWindow {
    samples: VecDeque<(Option<f64>, Duration)>,
    capacity: usize,
    interval: Duration,
}

impl ProbeWindow {
    fn new(capacity: usize, interval: Duration) -> Self {
        Self {
            samples: VecDeque::new(),
            capacity: capacity.max(1),
            interval,
        }
    }

    fn record(&mut self, value: Option<f64>, observed_at: Duration) {
        if self.samples.len() == self.capacity {
            self.samples.pop_front();
        }
        self.samples
            .push_back((value.filter(|v| v.is_finite() && *v >= 0.), observed_at));
    }

    fn summary(&self) -> ProbeSummary {
        if self.samples.is_empty() {
            return ProbeSummary::default();
        }
        let mut values: Vec<_> = self.samples.iter().filter_map(|(v, _)| *v).collect();
        values.sort_by(f64::total_cmp);
        let len = values.len();
        let median = if len == 0 {
            None
        } else if len % 2 == 0 {
            Some((values[len / 2 - 1] + values[len / 2]) / 2.)
        } else {
            Some(values[len / 2])
        };
        let p95 = if len == 0 {
            None
        } else {
            Some(values[(len * 95).div_ceil(100) - 1])
        };
        let (mut longest, mut streak, mut previous) = (Duration::ZERO, Duration::ZERO, None);
        for (value, observed_at) in &self.samples {
            let elapsed = previous
                .map(|time| observed_at.saturating_sub(time))
                .unwrap_or(self.interval)
                .max(self.interval);
            if value.is_none() {
                streak += elapsed;
            } else {
                streak = Duration::ZERO;
            }
            longest = longest.max(streak);
            previous = Some(*observed_at);
        }
        ProbeSummary {
            median,
            p95,
            loss: Some(100. * (self.samples.len() - len) as f64 / self.samples.len() as f64),
            outage_ms: Some((longest.as_secs_f64() * 1000.).round() as i64),
        }
    }
}

/// A spot-local rolling window. An unknown gateway is never counted as a loss.
pub struct PathTracker {
    gateway: ProbeWindow,
    internet: ProbeWindow,
    gateway_target: Option<String>,
    internet_target: Option<String>,
}

impl Default for PathTracker {
    fn default() -> Self {
        Self::new(60, Duration::from_secs(1))
    }
}

impl PathTracker {
    pub fn new(window_size: usize, interval: Duration) -> Self {
        Self {
            gateway: ProbeWindow::new(window_size, interval),
            internet: ProbeWindow::new(window_size, interval),
            gateway_target: None,
            internet_target: None,
        }
    }

    pub fn record(&mut self, gateway: Option<f64>, internet: Option<f64>, observed_at: Duration) {
        self.gateway.record(gateway, observed_at);
        self.internet.record(internet, observed_at);
    }

    pub fn record_sample(&mut self, sample: &PathSample) {
        if self.gateway_target != sample.gateway_target {
            self.gateway.samples.clear();
            self.gateway_target = sample.gateway_target.clone();
        }
        if self.internet_target.as_deref() != Some(&sample.internet_target) {
            self.internet.samples.clear();
            self.internet_target = Some(sample.internet_target.clone());
        }
        if sample.gateway_target.is_some() {
            self.gateway.record(sample.gateway_ms, sample.observed_at);
        }
        if !sample.internet_target.is_empty() {
            self.internet.record(sample.internet_ms, sample.observed_at);
        }
    }

    pub fn reset(&mut self) {
        self.gateway.samples.clear();
        self.internet.samples.clear();
        self.gateway_target = None;
        self.internet_target = None;
    }

    pub fn diagnostics(&self) -> PathDiagnostics {
        let jitter = |window: &ProbeWindow| {
            let deltas: Vec<_> = window
                .samples
                .iter()
                .zip(window.samples.iter().skip(1))
                .filter_map(|((previous, _), (current, _))| {
                    previous.zip(*current).map(|(a, b)| (b - a).abs())
                })
                .collect();
            (!deltas.is_empty()).then(|| deltas.iter().sum::<f64>() / deltas.len() as f64)
        };
        PathDiagnostics {
            gateway_jitter_ms: jitter(&self.gateway),
            internet_jitter_ms: jitter(&self.internet),
            gateway_latest_ms: self.gateway.samples.back().and_then(|(value, _)| *value),
            internet_latest_ms: self.internet.samples.back().and_then(|(value, _)| *value),
        }
    }

    fn phase(&self) -> LatencyPhase {
        LatencyPhase {
            path: self.fields(),
            diagnostics: self.diagnostics(),
        }
    }

    pub fn fields(&self) -> PathFields {
        let gateway = self.gateway.summary();
        let internet = self.internet.summary();
        let count = self.gateway.samples.len().max(self.internet.samples.len());
        PathFields {
            path_probe_count: (count > 0).then_some(count as i64),
            gateway_rtt_ms: gateway.median,
            gateway_p95_ms: gateway.p95,
            gateway_loss_pct: gateway.loss,
            gateway_max_outage_ms: gateway.outage_ms,
            internet_rtt_ms: internet.median,
            internet_p95_ms: internet.p95,
            internet_loss_pct: internet.loss,
            internet_max_outage_ms: internet.outage_ms,
        }
    }
}

pub struct PathWorker {
    cancel: CancelToken,
    receiver: mpsc::Receiver<PathSample>,
    thread: Option<JoinHandle<()>>,
    epoch: Instant,
}

impl PathWorker {
    pub fn start(gateway: Option<String>) -> Self {
        Self::start_with_cancel(gateway, &CancelToken::default())
    }

    pub fn start_with_cancel(_gateway: Option<String>, parent: &CancelToken) -> Self {
        let cancel = parent.child();
        let worker_cancel = cancel.clone();
        let (sender, receiver) = mpsc::sync_channel(120);
        let epoch = Instant::now();
        let thread = thread::spawn(move || {
            let mut interface = wifi_interface(&worker_cancel);
            let mut internet_target = "1.1.1.1".to_string();
            while !worker_cancel.is_cancelled() {
                let start = Instant::now();
                if interface.is_none() {
                    interface = wifi_interface(&worker_cancel);
                }
                // Resolve the WiFi-scoped route each cycle, so moving between
                // networks cannot keep pinging the previous gateway.
                let gateway = interface
                    .as_deref()
                    .and_then(|interface| gateway_on_interface(interface, &worker_cancel));
                let sample = probe_path(
                    interface.as_deref(),
                    gateway,
                    &mut internet_target,
                    epoch,
                    true,
                    &worker_cancel,
                );
                if worker_cancel.is_cancelled() {
                    break;
                }
                match sender.try_send(sample) {
                    Ok(()) | Err(mpsc::TrySendError::Full(_)) => {}
                    Err(mpsc::TrySendError::Disconnected(_)) => break,
                }
                if !worker_cancel.wait(Duration::from_secs(1).saturating_sub(start.elapsed())) {
                    break;
                }
            }
        });
        Self {
            cancel,
            receiver,
            thread: Some(thread),
            epoch,
        }
    }

    pub fn try_recv(&self) -> Option<PathSample> {
        self.receiver.try_recv().ok()
    }

    /// The actual probe completion time, retained even while queued. The
    /// sample's elapsed timestamp is stamped after both path probes finish.
    pub fn sample_instant(&self, sample: &PathSample) -> Instant {
        self.epoch + sample.observed_at
    }
}

impl Drop for PathWorker {
    fn drop(&mut self) {
        self.cancel.cancel();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

pub fn parse_netstat_ib(text: &str, interface: &str) -> Option<(u64, u64)> {
    for line in text.lines() {
        let tokens: Vec<_> = line.split_whitespace().collect();
        if tokens.first() != Some(&interface) || !line.contains("<Link") {
            continue;
        }
        if tokens.len() < 8 {
            return None;
        }
        return Some((
            tokens[tokens.len() - 5].parse().ok()?,
            tokens[tokens.len() - 2].parse().ok()?,
        ));
    }
    None
}

fn interface_counters(interface: &str, cancel: &CancelToken) -> Option<(u64, u64)> {
    let output = run_command(
        "/usr/sbin/netstat",
        &["-I", interface, "-b"],
        Duration::from_secs(2),
        cancel,
    )
    .ok()?;
    output
        .status
        .success()
        .then(|| parse_netstat_ib(&output.stdout, interface))
        .flatten()
}

pub struct TrafficSampler {
    interface: Option<String>,
    last: Option<((u64, u64), Duration)>,
    epoch: Instant,
}

impl Default for TrafficSampler {
    fn default() -> Self {
        Self {
            interface: None,
            last: None,
            epoch: Instant::now(),
        }
    }
}

impl TrafficSampler {
    pub fn sample(&mut self) -> Option<(f64, f64)> {
        self.sample_with_cancel(&CancelToken::default())
    }

    pub fn sample_with_cancel(&mut self, cancel: &CancelToken) -> Option<(f64, f64)> {
        if self.interface.is_none() {
            self.interface = wifi_interface(cancel);
        }
        let counters = self
            .interface
            .as_deref()
            .and_then(|interface| interface_counters(interface, cancel));
        if counters.is_none() {
            self.interface = None;
        }
        self.record(counters, self.epoch.elapsed())
    }

    pub fn record(
        &mut self,
        counters: Option<(u64, u64)>,
        observed_at: Duration,
    ) -> Option<(f64, f64)> {
        let previous = self.last.take();
        let counters = counters?;
        self.last = Some((counters, observed_at));
        let (previous, time) = previous?;
        let elapsed = observed_at.checked_sub(time)?.as_secs_f64();
        if elapsed <= 0. {
            return None;
        }
        Some((
            counters.0.checked_sub(previous.0)? as f64 / 125_000. / elapsed,
            counters.1.checked_sub(previous.1)? as f64 / 125_000. / elapsed,
        ))
    }
}

#[cfg(not(target_os = "macos"))]
mod corewlan {
    use super::*;
    pub fn signal() -> Result<Signal> {
        Err(MeasureError::Backend(
            "native WiFi sampling requires macOS".into(),
        ))
    }
    pub fn identity() -> Result<(Option<String>, Option<String>, Option<String>)> {
        Err(MeasureError::Backend("CoreWLAN requires macOS".into()))
    }
}

#[cfg(target_os = "macos")]
mod corewlan {
    use super::*;
    use std::ffi::{CStr, c_char, c_void};

    type Object = *mut c_void;
    type Selector = *mut c_void;

    // Sources: Apple's installed SDK CWInterface.h, CWWiFiClient.h and
    // CoreWLANTypes.h; https://developer.apple.com/documentation/corewlan.
    #[link(name = "CoreWLAN", kind = "framework")]
    unsafe extern "C" {}

    #[link(name = "Foundation", kind = "framework")]
    unsafe extern "C" {}

    #[link(name = "objc")]
    unsafe extern "C" {
        fn objc_getClass(name: *const c_char) -> Object;
        fn sel_registerName(name: *const c_char) -> Selector;
        fn objc_msgSend();
        fn objc_autoreleasePoolPush() -> Object;
        fn objc_autoreleasePoolPop(pool: Object);
    }

    struct Pool(Object);
    impl Pool {
        fn new() -> Self {
            // SAFETY: every thread may own an autorelease pool.
            Self(unsafe { objc_autoreleasePoolPush() })
        }
    }
    impl Drop for Pool {
        fn drop(&mut self) {
            // SAFETY: this pool is popped on its creating thread exactly once.
            unsafe { objc_autoreleasePoolPop(self.0) }
        }
    }

    unsafe fn object(receiver: Object, method: &CStr) -> Object {
        // SAFETY: callers use SDK methods with object return types, no args.
        let send: unsafe extern "C" fn(Object, Selector) -> Object =
            unsafe { std::mem::transmute(objc_msgSend as *const ()) };
        unsafe { send(receiver, sel_registerName(method.as_ptr())) }
    }

    unsafe fn integer(receiver: Object, method: &CStr) -> isize {
        // SAFETY: SDK NSInteger and enum returns have the isize ABI.
        let send: unsafe extern "C" fn(Object, Selector) -> isize =
            unsafe { std::mem::transmute(objc_msgSend as *const ()) };
        unsafe { send(receiver, sel_registerName(method.as_ptr())) }
    }

    unsafe fn number(receiver: Object, method: &CStr) -> f64 {
        // SAFETY: CWInterface.transmitRate returns double. Modern macOS supports
        // x86_64 and arm64, where objc_msgSend has the double-return ABI.
        let send: unsafe extern "C" fn(Object, Selector) -> f64 =
            unsafe { std::mem::transmute(objc_msgSend as *const ()) };
        unsafe { send(receiver, sel_registerName(method.as_ptr())) }
    }

    unsafe fn string(receiver: Object, method: &CStr) -> Option<String> {
        // SAFETY: the requested getter returns NSString or nil; UTF8String is
        // copied while the owning autorelease pool is still alive.
        let value = unsafe { object(receiver, method) };
        if value.is_null() {
            return None;
        }
        let utf8 = unsafe { object(value, c"UTF8String") } as *const c_char;
        if utf8.is_null() {
            return None;
        }
        Some(
            unsafe { CStr::from_ptr(utf8) }
                .to_string_lossy()
                .into_owned(),
        )
    }

    unsafe fn interface() -> Result<Object> {
        // SAFETY: class and method names are valid NUL-terminated constants;
        // returned pointers are borrowed within the caller's autorelease pool.
        let class = unsafe { objc_getClass(c"CWWiFiClient".as_ptr()) };
        if class.is_null() {
            return Err(MeasureError::Backend("CWWiFiClient is unavailable".into()));
        }
        let client = unsafe { object(class, c"sharedWiFiClient") };
        let interface = unsafe { object(client, c"interface") };
        if interface.is_null() {
            return Err(MeasureError::NoWifi("no WiFi interface (WiFi off?)".into()));
        }
        Ok(interface)
    }

    pub fn identity() -> Result<(Option<String>, Option<String>, Option<String>)> {
        let _pool = Pool::new();
        // SAFETY: all getters are documented CWInterface NSString methods.
        unsafe {
            let interface = interface()?;
            Ok((
                string(interface, c"ssid").as_deref().and_then(clean_ssid),
                string(interface, c"bssid").as_deref().and_then(clean_bssid),
                string(interface, c"interfaceName"),
            ))
        }
    }

    pub fn signal() -> Result<Signal> {
        let _pool = Pool::new();
        // SAFETY: CoreWLAN getters below match the installed SDK signatures.
        // Objects remain inside this thread's pool; only Rust values escape.
        unsafe {
            let interface = interface()?;
            let rssi = integer(interface, c"rssiValue") as f64;
            if rssi == 0. {
                return Err(MeasureError::NoWifi("WiFi is not associated".into()));
            }
            let noise = integer(interface, c"noiseMeasurement") as f64;
            let noise = (noise != 0.).then_some(noise);
            let rate = number(interface, c"transmitRate");
            let channel = object(interface, c"wlanChannel");
            let (channel_label, band) = if channel.is_null() {
                (None, None)
            } else {
                let number = integer(channel, c"channelNumber");
                let width = match integer(channel, c"channelWidth") {
                    1 => Some(20),
                    2 => Some(40),
                    3 => Some(80),
                    4 => Some(160),
                    _ => None,
                };
                let label = width.map_or_else(
                    || number.to_string(),
                    |width| format!("{number} ({width}MHz)"),
                );
                let band = match integer(channel, c"channelBand") {
                    1 => Some("2.4 GHz"),
                    2 => Some("5 GHz"),
                    3 => Some("6 GHz"),
                    _ => None,
                };
                (Some(label), band.map(str::to_string))
            };
            let phy = match integer(interface, c"activePHYMode") {
                0 => "none".into(),
                1 => "802.11a".into(),
                2 => "802.11b".into(),
                3 => "802.11g".into(),
                4 => "802.11n".into(),
                5 => "802.11ac".into(),
                6 => "802.11ax".into(),
                7 => "802.11be".into(),
                value => format!("unknown({value})"),
            };
            let security = match integer(interface, c"security") {
                0 => Some("open"),
                1 => Some("WEP"),
                2 => Some("WPA Personal"),
                3 => Some("WPA/WPA2 Personal"),
                4 => Some("WPA2 Personal"),
                5 => Some("Personal"),
                6 => Some("Dynamic WEP"),
                7 => Some("WPA Enterprise"),
                8 => Some("WPA/WPA2 Enterprise"),
                9 => Some("WPA2 Enterprise"),
                10 => Some("Enterprise"),
                11 => Some("WPA3 Personal"),
                12 => Some("WPA3 Enterprise"),
                13 => Some("WPA3 Transition"),
                14 => Some("OWE"),
                15 => Some("OWE Transition"),
                _ => None,
            };
            Ok(Signal {
                ssid: string(interface, c"ssid").as_deref().and_then(clean_ssid),
                bssid: string(interface, c"bssid").as_deref().and_then(clean_bssid),
                rssi: Some(rssi),
                noise,
                snr: noise.map(|noise| rssi - noise),
                channel: channel_label,
                phy: Some(phy),
                tx_rate: (rate.is_finite() && rate > 0.).then(|| rate.to_string()),
                // CoreWLAN's public API does not expose MCS index.
                mcs: None,
                band,
                security: security.map(str::to_string),
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profiler_parses_signal_and_preserves_redacted_identity() {
        let sample = parse_profiler("Current Network Information:\n <redacted>:\n PHY Mode: 802.11ax\n Channel: 36 (5GHz, 80MHz)\n Signal / Noise: -60 dBm / -90 dBm\n Transmit Rate: 1200\n").unwrap();
        assert_eq!(sample.rssi, Some(-60.));
        assert_eq!(sample.noise, Some(-90.));
        assert_eq!(sample.snr, Some(30.));
        assert_eq!(sample.ssid, None);
        assert_eq!(sample.bssid, None);
        assert_eq!(sample.channel.as_deref(), Some("36 (5GHz, 80MHz)"));
    }

    #[test]
    fn profiler_disconnected_is_typed_no_wifi() {
        assert!(matches!(
            parse_profiler("Wi-Fi: Off"),
            Err(MeasureError::NoWifi(_))
        ));
    }

    #[test]
    fn signal_average_rounds_ties_even_and_keeps_last_association() {
        let first = Signal {
            rssi: Some(-59.),
            noise: Some(-89.),
            bssid: Some("aa:bb:cc:dd:ee:01".into()),
            ..Signal::default()
        };
        let last = Signal {
            rssi: Some(-60.),
            noise: None,
            bssid: Some("aa:bb:cc:dd:ee:02".into()),
            ..Signal::default()
        };
        let sample = average_signal_samples(&[first, last]);
        assert_eq!(sample.rssi, Some(-60.));
        assert_eq!(sample.noise, Some(-89.));
        assert_eq!(sample.snr, Some(29.));
        assert_eq!(sample.bssid.as_deref(), Some("aa:bb:cc:dd:ee:02"));
        assert_eq!(
            average_signal_samples(&[Signal::default()]),
            Signal::default()
        );
    }

    #[test]
    fn speedtest_converts_bytes_per_second_to_mbps() {
        let sample = parse_speedtest(r#"{"ping":{"latency":11.285},"download":{"bandwidth":11750000},"upload":{"bandwidth":1250000},"server":{"name":"Testville","id":1234}}"#).unwrap();
        assert_eq!(sample.ping_ms, Some(11.285));
        assert_eq!(sample.down_mbps, Some(94.0));
        assert_eq!(sample.up_mbps, Some(10.0));
        assert_eq!(sample.server.as_deref(), Some("Testville (1234)"));
    }

    #[test]
    fn speedtest_rejects_invalid_or_missing_metrics() {
        for text in [
            "not json",
            "{}",
            r#"{"ping":{"latency":-1},"download":{"bandwidth":1},"upload":{"bandwidth":1}}"#,
        ] {
            assert!(parse_speedtest(text).is_err());
        }
    }

    #[test]
    fn identity_normalizes_and_rejects_redaction() {
        assert_eq!(
            parse_helper_identity(
                r#"{"status":"connected","ssid":"mesh-net","bssid":"60-8D-26-8D-CF-3D"}"#
            ),
            Some(("mesh-net".into(), Some("60:8d:26:8d:cf:3d".into())))
        );
        assert_eq!(
            parse_helper_identity(r#"{"status":"connected","ssid":"<redacted>"}"#),
            None
        );
        assert_eq!(
            parse_helper_identity(r#"{"status":"not_connected","ssid":"mesh-net"}"#),
            None
        );
    }

    #[test]
    fn forced_identity_refresh_updates_roamed_ap_but_never_mixes_networks() {
        let mut sample = Signal {
            ssid: Some("mesh-net".into()),
            bssid: Some("aa:bb:cc:dd:ee:01".into()),
            ..Signal::default()
        };
        let current = ("mesh-net".into(), Some("aa:bb:cc:dd:ee:02".into()));
        apply_identity(&mut sample, current.clone(), false);
        assert_eq!(sample.bssid.as_deref(), Some("aa:bb:cc:dd:ee:01"));
        apply_identity(&mut sample, current, true);
        assert_eq!(sample.bssid.as_deref(), Some("aa:bb:cc:dd:ee:02"));
        apply_identity(
            &mut sample,
            ("other-net".into(), Some("aa:bb:cc:dd:ee:03".into())),
            true,
        );
        assert_eq!(sample.ssid.as_deref(), Some("mesh-net"));
        assert_eq!(sample.bssid.as_deref(), Some("aa:bb:cc:dd:ee:02"));
        apply_identity(&mut sample, ("mesh-net".into(), None), true);
        assert_eq!(sample.bssid.as_deref(), Some("aa:bb:cc:dd:ee:02"));
    }

    #[test]
    fn ping_parses_normal_and_submillisecond_reply() {
        assert_eq!(parse_ping_rtt("64 bytes: time=12.345 ms"), Some(12.345));
        assert_eq!(parse_ping_rtt("64 bytes: time<1 ms"), Some(1.0));
        assert_eq!(parse_ping_rtt("Request timeout"), None);
    }

    #[test]
    fn path_jitter_uses_adjacent_successes_without_bridging_losses() {
        let mut tracker = PathTracker::new(6, Duration::from_secs(1));
        for (i, rtt) in [Some(2.), Some(6.), None, Some(100.), Some(110.), None]
            .into_iter()
            .enumerate()
        {
            tracker.record(rtt, rtt, Duration::from_secs(i as u64));
        }
        let diagnostics = tracker.diagnostics();
        assert_eq!(diagnostics.gateway_jitter_ms, Some(7.));
        assert_eq!(diagnostics.internet_jitter_ms, Some(7.));
        assert_eq!(diagnostics.gateway_latest_ms, None);
        assert_eq!(diagnostics.internet_latest_ms, None);
        tracker.reset();
        tracker.record(Some(42.), Some(15.), Duration::ZERO);
        assert_eq!(tracker.diagnostics().gateway_jitter_ms, None);
        assert_eq!(tracker.diagnostics().gateway_latest_ms, Some(42.));
    }

    #[test]
    fn path_windows_reset_when_targets_change_or_gateway_disappears() {
        let mut tracker = PathTracker::default();
        let mut sample = PathSample {
            gateway_target: Some("192.168.1.1".into()),
            gateway_ms: None,
            internet_target: "1.1.1.1".into(),
            internet_ms: Some(15.),
            primary_failed: false,
            observed_at: Duration::ZERO,
        };
        tracker.record_sample(&sample);
        sample.gateway_target = Some("192.168.2.1".into());
        sample.gateway_ms = Some(2.);
        sample.internet_target = "8.8.8.8".into();
        sample.internet_ms = Some(100.);
        sample.observed_at = Duration::from_secs(1);
        tracker.record_sample(&sample);
        assert_eq!(tracker.fields().gateway_loss_pct, Some(0.));
        assert_eq!(tracker.fields().internet_rtt_ms, Some(100.));
        assert_eq!(tracker.diagnostics().internet_jitter_ms, None);
        sample.gateway_target = None;
        sample.gateway_ms = None;
        tracker.record_sample(&sample);
        assert_eq!(tracker.fields().gateway_rtt_ms, None);
        assert_eq!(tracker.fields().gateway_loss_pct, None);
    }

    #[test]
    fn queued_probe_timestamp_keeps_its_original_age() {
        let epoch = Instant::now() - Duration::from_secs(60);
        let (_, receiver) = mpsc::channel();
        let worker = PathWorker {
            cancel: CancelToken::default(),
            receiver,
            thread: None,
            epoch,
        };
        let sample = PathSample {
            gateway_target: Some("192.168.1.1".into()),
            gateway_ms: Some(2.),
            internet_target: "1.1.1.1".into(),
            internet_ms: Some(10.),
            primary_failed: false,
            observed_at: Duration::from_secs(2),
        };
        assert_eq!(
            worker.sample_instant(&sample),
            epoch + Duration::from_secs(2)
        );
        assert!(worker.sample_instant(&sample).elapsed() >= Duration::from_secs(58));
    }

    #[test]
    fn invalid_ap_addresses_are_not_roaming_identities() {
        assert_eq!(clean_bssid("00:00:00:00:00:00"), None);
        assert_eq!(clean_bssid("FF-FF-FF-FF-FF-FF"), None);
    }

    #[test]
    fn changed_gateway_discards_only_gateway_comparison() {
        let mut tracker = PathTracker::default();
        tracker.record(Some(2.), Some(10.), Duration::ZERO);
        tracker.record(Some(4.), Some(20.), Duration::from_secs(1));
        let phase = tracker.phase();
        let mut report = LoadLatency {
            idle: phase.clone(),
            loaded: phase.clone(),
            recovery: phase,
            ..Default::default()
        };
        invalidate_gateway_comparison(&mut report);
        assert!(report.gateway_changed);
        for phase in [&report.idle, &report.loaded, &report.recovery] {
            assert_eq!(phase.path.gateway_rtt_ms, None);
            assert_eq!(phase.path.gateway_loss_pct, None);
            assert_eq!(phase.diagnostics.gateway_jitter_ms, None);
            assert_eq!(phase.diagnostics.gateway_latest_ms, None);
            assert_eq!(phase.path.internet_rtt_ms, Some(15.));
            assert_eq!(phase.diagnostics.internet_jitter_ms, Some(10.));
        }
    }

    #[test]
    fn load_phases_keep_separate_windows_and_failed_load_is_unavailable() {
        let mut count = 0;
        let mut probe = || {
            count += 1;
            PathSample {
                gateway_target: Some("192.168.1.1".into()),
                gateway_ms: Some(if count <= 5 { 2. } else { 50. }),
                internet_target: "1.1.1.1".into(),
                internet_ms: Some(if count <= 5 { 10. } else { 100. }),
                primary_failed: false,
                observed_at: Duration::from_secs(count),
            }
        };
        let cancel = CancelToken::default();
        let idle = collect_latency_phase(&mut probe, Duration::ZERO, &cancel);
        let recovery = collect_latency_phase(&mut probe, Duration::ZERO, &cancel);
        assert_eq!(idle.path.gateway_rtt_ms, Some(2.));
        assert_eq!(recovery.path.gateway_rtt_ms, Some(50.));
        assert_eq!(idle.path.path_probe_count, Some(5));

        let (result, report) = run_load_measurement(
            &cancel,
            || Err(MeasureError::SpeedtestUnavailable),
            probe,
            Duration::ZERO,
        );
        assert!(matches!(result, Err(MeasureError::SpeedtestUnavailable)));
        assert_eq!(report.loaded.path.path_probe_count, None);
        assert_eq!(report.recovery.path.path_probe_count, None);
    }

    #[test]
    fn cancelled_load_never_starts_speedtest_or_collects_samples() {
        let cancel = CancelToken::default();
        cancel.cancel();
        let (result, report) = run_load_measurement(
            &cancel,
            || panic!("cancelled speedtest must not start"),
            || panic!("cancelled probes must not start"),
            Duration::ZERO,
        );
        assert!(matches!(result, Err(MeasureError::Cancelled)));
        assert_eq!(report.idle.path.path_probe_count, None);
    }

    #[test]
    fn successful_load_reports_idle_loaded_and_recovery_independently() {
        let (sender, receiver) = mpsc::sync_channel(0);
        let completed = AtomicBool::new(false);
        let speed_completed = &completed;
        let mut count = 0;
        let (result, report) = run_load_measurement(
            &CancelToken::default(),
            move || {
                receiver.recv().unwrap();
                speed_completed.store(true, Ordering::Release);
                Ok(Speed::default())
            },
            || {
                count += 1;
                let rtt = if count <= 5 {
                    10.
                } else if completed.load(Ordering::Acquire) {
                    20.
                } else {
                    100.
                };
                if count == 7 {
                    sender.send(()).unwrap();
                }
                PathSample {
                    gateway_target: Some("192.168.1.1".into()),
                    gateway_ms: Some(rtt),
                    internet_target: "1.1.1.1".into(),
                    internet_ms: Some(rtt),
                    primary_failed: false,
                    observed_at: Duration::from_secs(count),
                }
            },
            Duration::ZERO,
        );
        assert!(result.is_ok());
        assert_eq!(report.idle.path.internet_rtt_ms, Some(10.));
        assert_eq!(report.loaded.path.internet_rtt_ms, Some(100.));
        assert_eq!(report.recovery.path.internet_rtt_ms, Some(20.));
        assert_eq!(report.recovery.path.path_probe_count, Some(5));
    }

    #[test]
    fn cancelling_during_load_stops_worker_and_discards_partial_report() {
        let cancel = CancelToken::default();
        let start = Instant::now();
        let mut count = 0;
        let (result, report) = run_load_measurement(
            &cancel,
            || {
                cancel.wait(Duration::from_secs(60));
                Err(MeasureError::Cancelled)
            },
            || {
                count += 1;
                if count == 6 {
                    cancel.cancel();
                }
                PathSample {
                    gateway_target: None,
                    gateway_ms: None,
                    internet_target: "1.1.1.1".into(),
                    internet_ms: Some(10.),
                    primary_failed: false,
                    observed_at: Duration::from_secs(count),
                }
            },
            Duration::ZERO,
        );
        assert!(matches!(result, Err(MeasureError::Cancelled)));
        assert_eq!(report.idle.path.path_probe_count, None);
        assert_eq!(report.loaded.path.path_probe_count, None);
        assert_eq!(report.recovery.path.path_probe_count, None);
        assert!(start.elapsed() < Duration::from_secs(3));
    }

    #[test]
    fn injected_network_change_discards_load_comparison_but_same_ssid_roam_does_not() {
        for change_network in [true, false] {
            let (sender, receiver) = mpsc::sync_channel(0);
            let mut probe_count = 0;
            let mut identity_count = 0;
            let (result, report) = run_connection_load_measurement(
                &CancelToken::default(),
                move || {
                    receiver.recv().unwrap();
                    Ok(Speed::default())
                },
                || {
                    probe_count += 1;
                    if probe_count == 7 {
                        sender.send(()).unwrap();
                    }
                    PathSample {
                        gateway_target: Some("192.168.1.1".into()),
                        gateway_ms: Some(2.),
                        internet_target: "1.1.1.1".into(),
                        internet_ms: Some(10.),
                        primary_failed: false,
                        observed_at: Duration::from_secs(probe_count),
                    }
                },
                || {
                    identity_count += 1;
                    if identity_count == 6 {
                        return None;
                    }
                    // The second AP can use the same gateway IP. Only a known
                    // SSID change invalidates the comparison; a redaction gap
                    // must not erase the previously known network.
                    let signal = Signal {
                        ssid: Some(if change_network && identity_count >= 6 {
                            "new-network".into()
                        } else {
                            "mesh-network".into()
                        }),
                        bssid: Some(if identity_count >= 6 {
                            "02:00:00:00:00:02".into()
                        } else {
                            "02:00:00:00:00:01".into()
                        }),
                        ..Default::default()
                    };
                    signal.ssid
                },
                Duration::ZERO,
            );
            assert!(result.is_ok());
            assert_eq!(report.connection_changed, change_network);
            assert_eq!(report.idle.path.internet_rtt_ms, Some(10.));
            assert_eq!(report.loaded.path.internet_rtt_ms.is_none(), change_network);
            assert_eq!(
                report.recovery.path.internet_rtt_ms.is_none(),
                change_network
            );
        }
    }

    #[test]
    fn path_summary_tracks_latency_loss_and_elapsed_outages() {
        let mut tracker = PathTracker::new(6, Duration::from_secs(1));
        for (i, (gateway, internet)) in [
            (Some(3.), Some(20.)),
            (Some(4.), Some(30.)),
            (None, None),
            (None, None),
            (Some(5.), Some(25.)),
            (Some(6.), Some(100.)),
        ]
        .into_iter()
        .enumerate()
        {
            tracker.record(gateway, internet, Duration::from_secs(i as u64));
        }
        let fields = tracker.fields();
        assert_eq!(fields.gateway_rtt_ms, Some(4.5));
        assert_eq!(fields.gateway_p95_ms, Some(6.));
        assert!((fields.gateway_loss_pct.unwrap() - 100. / 3.).abs() < 1e-10);
        assert_eq!(fields.gateway_max_outage_ms, Some(2000));
        assert_eq!(fields.internet_p95_ms, Some(100.));
        tracker.reset();
        tracker.record(Some(3.), Some(20.), Duration::ZERO);
        tracker.record(None, None, Duration::from_millis(2200));
        tracker.record(None, None, Duration::from_millis(4400));
        assert_eq!(tracker.fields().internet_max_outage_ms, Some(4400));
    }

    #[test]
    fn missing_path_metrics_are_not_zero() {
        let mut tracker = PathTracker::default();
        assert_eq!(tracker.fields(), PathFields::default());
        tracker.record_sample(&PathSample {
            gateway_target: None,
            gateway_ms: None,
            internet_target: "1.1.1.1".into(),
            internet_ms: None,
            primary_failed: true,
            observed_at: Duration::ZERO,
        });
        let fields = tracker.fields();
        assert_eq!(fields.path_probe_count, Some(1));
        assert_eq!(fields.gateway_loss_pct, None);
        assert_eq!(fields.gateway_max_outage_ms, None);
        assert_eq!(fields.internet_rtt_ms, None);
        assert_eq!(fields.internet_loss_pct, Some(100.));
        tracker.reset();
        assert_eq!(tracker.fields(), PathFields::default());
    }

    #[test]
    fn netstat_link_rows_allow_an_empty_address_column() {
        let text = "Name Mtu Network Address Ipkts Ierrs Ibytes Opkts Oerrs Obytes Coll\nen0 1500 <Link#14> aa:bb:cc:dd:ee:ff 19 0 25850300535 8 0 3852569809 0\nutun6 1420 <Link#27> 6 0 8524156520 4 0 2180756050 0";
        assert_eq!(
            parse_netstat_ib(text, "en0"),
            Some((25850300535, 3852569809))
        );
        assert_eq!(
            parse_netstat_ib(text, "utun6"),
            Some((8524156520, 2180756050))
        );
        assert_eq!(parse_netstat_ib(text, "en9"), None);
    }

    #[test]
    fn traffic_rates_rebaseline_after_missing_or_reset_counters() {
        let mut sampler = TrafficSampler::default();
        assert_eq!(sampler.record(Some((1000, 500)), Duration::ZERO), None);
        assert_eq!(
            sampler.record(Some((126000, 25500)), Duration::from_secs(1)),
            Some((1., 0.2))
        );
        assert_eq!(sampler.record(None, Duration::from_secs(2)), None);
        assert_eq!(
            sampler.record(Some((100, 100)), Duration::from_secs(3)),
            None
        );
        assert_eq!(sampler.record(Some((50, 50)), Duration::from_secs(4)), None);
    }

    #[test]
    fn profiler_does_not_mistake_a_nearby_network_for_an_association() {
        assert!(matches!(
            parse_profiler(
                "Other Local Wi-Fi Networks:\n Neighbor:\n Signal / Noise: -40 dBm / -90 dBm"
            ),
            Err(MeasureError::NoWifi(_))
        ));
    }

    #[test]
    fn networksetup_ssid_ignores_redacted_and_disconnected_outputs() {
        assert_eq!(
            parse_networksetup_ssid("Current Wi-Fi Network: mesh:floor 2"),
            Some("mesh:floor 2".into())
        );
        assert_eq!(
            parse_networksetup_ssid("Current AirPort Network: <redacted>"),
            None
        );
        assert_eq!(
            parse_networksetup_ssid("You are not associated with an AirPort network."),
            None
        );
    }

    #[test]
    fn helper_version_selection_prefers_stable_over_matching_prerelease() {
        assert!(version_key("3.0.0") > version_key("3.0.0-alpha.1"));
        assert!(version_key("3.10.0") > version_key("3.9.0"));
    }

    #[test]
    fn rolling_path_window_discards_old_failures_and_outages() {
        let mut tracker = PathTracker::new(2, Duration::from_secs(1));
        tracker.record(None, None, Duration::ZERO);
        tracker.record(Some(2.), Some(10.), Duration::from_secs(1));
        tracker.record(Some(4.), Some(20.), Duration::from_secs(2));
        let fields = tracker.fields();
        assert_eq!(fields.path_probe_count, Some(2));
        assert_eq!(fields.gateway_loss_pct, Some(0.));
        assert_eq!(fields.gateway_max_outage_ms, Some(0));
        assert_eq!(fields.gateway_rtt_ms, Some(3.));
    }

    #[cfg(unix)]
    #[test]
    fn subprocess_drains_output_larger_than_pipe_capacity() {
        let fixture = tempfile::NamedTempFile::new().unwrap();
        std::fs::write(fixture.path(), vec![b'x'; 256 * 1024]).unwrap();
        let output = run_command(
            "/bin/cat",
            &[fixture.path().to_str().unwrap()],
            Duration::from_secs(5),
            &CancelToken::default(),
        )
        .unwrap();
        assert!(output.status.success());
        assert_eq!(output.stdout.len(), 256 * 1024);
        assert!(output.stderr.is_empty());
    }

    #[cfg(unix)]
    #[test]
    fn subprocess_timeout_terminates_and_reaps_child() {
        let start = Instant::now();
        let result = run_command(
            "/bin/sleep",
            &["30"],
            Duration::from_millis(50),
            &CancelToken::default(),
        );
        assert!(
            matches!(result, Err(MeasureError::Backend(message)) if message.contains("timed out"))
        );
        assert!(start.elapsed() < Duration::from_secs(3));
    }

    #[cfg(unix)]
    #[test]
    fn running_subprocess_can_be_cancelled_promptly() {
        let cancel = CancelToken::default();
        let worker_cancel = cancel.clone();
        let start = Instant::now();
        let worker = thread::spawn(move || {
            run_command(
                "/bin/sleep",
                &["30"],
                Duration::from_secs(60),
                &worker_cancel,
            )
        });
        thread::sleep(Duration::from_millis(50));
        cancel.cancel();
        assert!(matches!(
            worker.join().unwrap(),
            Err(MeasureError::Cancelled)
        ));
        assert!(start.elapsed() < Duration::from_secs(3));
    }

    #[test]
    fn invalid_ping_target_never_starts_a_process() {
        assert_eq!(
            ping_once("-c 100 example.com", &CancelToken::default()),
            None
        );
    }

    #[test]
    fn cancellation_children_stop_with_parent_and_leave_siblings_independent() {
        let parent = CancelToken::default();
        let first = parent.child();
        let second = parent.child();
        first.cancel();
        assert!(first.is_cancelled());
        assert!(!parent.is_cancelled());
        assert!(!second.is_cancelled());
        parent.cancel();
        assert!(second.is_cancelled());
        assert!(parent.child().is_cancelled());
    }

    #[cfg(unix)]
    #[test]
    fn parent_cancel_kills_registered_child_without_waiting_for_worker_poll() {
        use std::os::unix::process::{CommandExt, ExitStatusExt};
        let parent = CancelToken::default();
        let cancel = parent.child();
        let process = Command::new("/bin/sleep")
            .arg("30")
            .process_group(0)
            .spawn()
            .unwrap();
        cancel.processes().insert(process.id());
        let mut child = ChildGuard {
            child: process,
            cancel,
            finished: false,
        };
        parent.cancel();
        let status = child.child.wait().unwrap();
        child.cancel.processes().remove(&child.child.id());
        child.finished = true;
        assert_eq!(status.signal(), Some(libc::SIGKILL));
    }

    #[test]
    fn closing_interrupt_listener_does_not_cancel_measurements() {
        let cancel = CancelToken::default();
        let start = Instant::now();
        drop(install_interrupt_handler(&cancel).unwrap());
        assert!(!cancel.is_cancelled());
        assert!(start.elapsed() < Duration::from_secs(3));
    }
}
