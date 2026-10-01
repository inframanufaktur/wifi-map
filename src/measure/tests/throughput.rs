use super::*;
use crate::model::Signal;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    mpsc,
};

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
