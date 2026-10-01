use super::*;

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
    assert!(matches!(result, Err(MeasureError::Backend(message)) if message.contains("timed out")));
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
