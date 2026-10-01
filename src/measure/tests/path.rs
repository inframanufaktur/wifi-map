use super::*;

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

#[test]
fn invalid_ping_target_never_starts_a_process() {
    assert_eq!(
        ping_once("-c 100 example.com", &CancelToken::default()),
        None
    );
}
