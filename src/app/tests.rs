use super::*;
fn key(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::NONE)
}
fn args() -> WalkArgs {
    WalkArgs {
        interval: 1.0,
        no_speedtest: false,
        location: None,
        ssid: None,
        name: Some("Test survey".into()),
        compare_to: None,
        demo: true,
    }
}

fn live_event(app: &WalkApp, signal: Signal, elapsed: Duration) -> WorkerEvent {
    WorkerEvent::Live {
        generation: app.generation.load(Ordering::Relaxed),
        observed_at: app.diagnostics_epoch + elapsed,
        result: Ok(LiveSample {
            signal,
            path: PathFields::default(),
            diagnostics: PathDiagnostics::default(),
            latest_probe: None,
            rates: None,
        }),
    }
}

fn load_report(app: &WalkApp, latency: LoadLatency) -> Box<LoadReport> {
    Box::new(LoadReport {
        latency,
        association: app.signal.clone(),
        completed_at: Instant::now(),
    })
}

#[test]
fn demo_snapshots_belong_to_named_session_and_finish_cleanly() {
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    let walk_id = app.walk_id.unwrap();
    app.press(key(KeyCode::Char('s')));
    let readings = app
        .store
        .list_readings(&Filter {
            walk_id: Some(walk_id),
            ..Default::default()
        })
        .unwrap();
    assert_eq!(readings.len(), 1);
    assert_eq!(readings[0].walk_name.as_deref(), Some("Test survey"));
    app.finish_session().unwrap();
    assert!(
        app.store
            .get_walk(walk_id)
            .unwrap()
            .unwrap()
            .ended_at
            .is_some()
    );
}
#[test]
fn editor_accepts_shortcut_characters_as_text_and_can_cancel() {
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    app.begin_create(Level::Spot);
    for c in "q?st".chars() {
        assert!(!app.press(key(KeyCode::Char(c))));
    }
    assert_eq!(app.editor.as_ref().unwrap().text, "q?st");
    app.press(key(KeyCode::Esc));
    assert!(app.editor.is_none());
}
#[test]
fn late_snapshot_keeps_its_original_spot() {
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    let mut target = app.active.clone();
    target.ts = timestamp();
    target.walk_id = app.walk_id;
    let original = target.spot_id;
    app.select_spot(app.places[1].spot_id).unwrap();
    app.apply_event(WorkerEvent::Measured {
        target: Box::new(target),
        generation: app.generation.load(Ordering::Relaxed),
        kind: WorkKind::Snapshot,
        result: Ok((measure::demo_signal(0), demo_speed(0), None, None)),
    });
    let readings = app
        .store
        .list_readings(&Filter {
            walk_id: app.walk_id,
            ..Default::default()
        })
        .unwrap();
    assert_eq!(readings[0].spot_id, original);
    assert!(app.speed.down_mbps.is_none());
}

#[test]
fn live_signal_tracks_the_actual_network_instead_of_the_survey_label() {
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    let mut signal = measure::demo_signal(0);
    signal.ssid = Some("Actual WiFi".into());
    app.apply_event(WorkerEvent::Live {
        generation: app.generation.load(Ordering::Relaxed),
        observed_at: Instant::now(),
        result: Ok(LiveSample {
            signal,
            path: PathFields::default(),
            diagnostics: PathDiagnostics::default(),
            latest_probe: None,
            rates: None,
        }),
    });
    assert_eq!(app.signal.ssid.as_deref(), Some("Actual WiFi"));
    assert_eq!(app.active.signal.ssid.as_deref(), Some("Demo WiFi"));
    assert_eq!(
        app.timeline
            .samples
            .back()
            .unwrap()
            .signal
            .as_ref()
            .unwrap()
            .ssid
            .as_deref(),
        Some("Actual WiFi")
    );
}

#[test]
fn roaming_history_survives_spot_and_room_changes_but_resets_for_a_new_session() {
    use crate::timeline::TimelineEventKind;
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    let epoch = app.diagnostics_epoch;
    app.apply_event(live_event(
        &app,
        measure::demo_signal(0),
        Duration::from_secs(1),
    ));
    app.select_spot(app.places[1].spot_id).unwrap();
    assert_eq!(app.timeline.samples.len(), 1);
    assert_eq!(app.diagnostics_epoch, epoch);
    let room_id = app
        .places
        .iter()
        .find(|place| place.room_id != app.active.room_id)
        .unwrap()
        .room_id;
    app.apply_choice(Level::Room, room_id).unwrap();
    assert_eq!(app.timeline.samples.len(), 1);
    assert_eq!(app.diagnostics_epoch, epoch);
    app.apply_event(live_event(
        &app,
        measure::demo_signal(20),
        Duration::from_secs(2),
    ));
    assert_eq!(
        app.timeline.events.back().unwrap().kind,
        TimelineEventKind::Roam
    );
    app.start_session().unwrap();
    assert!(app.timeline.samples.is_empty());
    assert!(app.timeline.events.is_empty());
    app.apply_event(live_event(
        &app,
        measure::demo_signal(0),
        Duration::from_secs(1),
    ));
    app.set_ssid("Another survey network").unwrap();
    assert!(app.timeline.samples.is_empty());
    app.apply_event(live_event(
        &app,
        measure::demo_signal(0),
        Duration::from_secs(1),
    ));
    let location = app.store.create_location("Another location").unwrap();
    app.select_location(location).unwrap();
    assert!(app.timeline.samples.is_empty());
}

#[test]
fn live_timeline_uses_collection_time_and_raw_rtt_instead_of_rolling_median() {
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    let collected_at = Duration::from_secs(3);
    app.apply_event(WorkerEvent::Live {
        generation: app.generation.load(Ordering::Relaxed),
        observed_at: app.diagnostics_epoch + collected_at,
        result: Ok(LiveSample {
            signal: measure::demo_signal(0),
            path: PathFields {
                gateway_rtt_ms: Some(10.),
                ..Default::default()
            },
            diagnostics: PathDiagnostics {
                gateway_latest_ms: Some(42.),
                ..Default::default()
            },
            latest_probe: Some((Some(42.), Some(18.))),
            rates: Some((7., 1.)),
        }),
    });
    let sample = app.timeline.samples.back().unwrap();
    assert_eq!(sample.observed_at, collected_at);
    assert_eq!(sample.gateway_ms, Some(42.));
    assert_eq!(sample.internet_ms, Some(18.));
    assert_eq!(sample.rates_mbps, Some((7., 1.)));
    assert_eq!(app.path.gateway_rtt_ms, Some(10.));
    app.apply_event(live_event(
        &app,
        measure::demo_signal(0),
        Duration::from_secs(4),
    ));
    assert_eq!(app.timeline.samples.back().unwrap().gateway_ms, None);
}

#[test]
fn hidden_identity_and_measurement_errors_have_distinct_timeline_events() {
    use crate::timeline::TimelineEventKind;
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    app.apply_event(live_event(
        &app,
        measure::demo_signal(0),
        Duration::from_secs(1),
    ));
    let mut redacted = measure::demo_signal(1);
    redacted.ssid = None;
    redacted.bssid = None;
    app.apply_event(live_event(&app, redacted, Duration::from_secs(2)));
    assert!(app.timeline.events.is_empty());
    app.apply_event(WorkerEvent::Live {
        generation: app.generation.load(Ordering::Relaxed),
        observed_at: app.diagnostics_epoch + Duration::from_secs(3),
        result: Err("radio sample failed".into()),
    });
    assert_eq!(
        app.timeline.events.back().unwrap().kind,
        TimelineEventKind::SampleUnavailable
    );
    assert!(app.sample_error);
    assert!(app.rates.is_none());
    app.apply_event(live_event(
        &app,
        measure::demo_signal(3),
        Duration::from_secs(4),
    ));
    assert_eq!(
        app.timeline.events.back().unwrap().kind,
        TimelineEventKind::SampleRecovered
    );
    assert!(!app.sample_error);
}

#[test]
fn load_latency_report_is_accepted_for_current_work_and_rejected_after_generation_changes() {
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    app.start_work(WorkKind::Probe).unwrap();
    assert_eq!(
        app.load_latency
            .as_ref()
            .unwrap()
            .loaded
            .path
            .gateway_rtt_ms,
        Some(24.)
    );
    assert!(!app.load_running);
    let target = app.active.clone();
    let generation = app.generation.load(Ordering::Relaxed);
    let original_spot = app.active.spot_id;
    app.select_spot(app.places[1].spot_id).unwrap();
    app.select_spot(original_spot).unwrap();
    app.pending = 1;
    app.load_running = true;
    app.apply_event(WorkerEvent::Measured {
        target: Box::new(target),
        generation,
        kind: WorkKind::Probe,
        result: Ok((
            measure::demo_signal(0),
            demo_speed(0),
            None,
            Some(load_report(&app, demo_load_latency())),
        )),
    });
    assert!(app.load_latency.is_none());
    assert!(app.speed.down_mbps.is_none());
    assert_eq!(app.pending, 0);
    assert!(!app.load_running);
}

#[test]
fn disabled_speedtests_never_produce_load_reports() {
    let mut selected = args();
    selected.no_speedtest = true;
    let mut app = WalkApp::new(demo_store().unwrap(), &selected).unwrap();
    app.start_work(WorkKind::Snapshot).unwrap();
    assert!(app.load_latency.is_none());
    assert!(!app.load_running);
    assert!(app.start_work(WorkKind::Probe).is_err());
    app.apply_event(WorkerEvent::Measured {
        target: Box::new(app.active.clone()),
        generation: app.generation.load(Ordering::Relaxed),
        kind: WorkKind::Snapshot,
        result: Ok((
            measure::demo_signal(0),
            demo_speed(0),
            None,
            Some(load_report(&app, demo_load_latency())),
        )),
    });
    assert!(app.load_latency.is_none());
}

#[test]
fn diagnostics_controls_switch_timeline_views_and_preserve_survey_selection() {
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    app.picker = None;
    let spot = app.active.spot_id;
    for tick in 0..4 {
        app.apply_event(live_event(
            &app,
            measure::demo_signal(tick),
            Duration::from_secs(tick),
        ));
    }
    app.press(key(KeyCode::Char('d')));
    assert!(app.diagnostics_open);
    assert!(app.diagnostics_events);
    app.press(key(KeyCode::Tab));
    assert!(!app.diagnostics_events);
    app.press(key(KeyCode::Down));
    assert_eq!(app.diagnostics_offset, 1);
    assert_eq!(app.active.spot_id, spot);
    app.press(key(KeyCode::End));
    assert_eq!(app.diagnostics_offset, 3);
    app.press(key(KeyCode::Home));
    assert_eq!(app.diagnostics_offset, 0);
    app.press(key(KeyCode::Char('r')));
    assert!(app.picker.is_none());
    app.press(key(KeyCode::Esc));
    assert!(!app.diagnostics_open);
}

#[test]
fn recovery_discards_expired_probes_and_probes_from_a_previous_generation() {
    let epoch = Instant::now();
    let recovered_at = epoch + Duration::from_secs(120);
    let boundary = epoch + Duration::from_secs(110);
    let probe = |rtt, elapsed| measure::PathSample {
        gateway_target: Some("192.168.1.1".into()),
        gateway_ms: Some(rtt),
        internet_target: "1.1.1.1".into(),
        internet_ms: Some(rtt + 10.),
        primary_failed: false,
        observed_at: Duration::from_secs(elapsed),
    };
    let mut tracker = measure::PathTracker::default();
    let latest = record_recent_probes(
        &mut tracker,
        [
            (probe(90., 30), epoch + Duration::from_secs(30)),
            (probe(80., 109), epoch + Duration::from_secs(109)),
            (probe(70., 117), epoch + Duration::from_secs(117)),
            (probe(2., 119), epoch + Duration::from_secs(119)),
        ],
        recovered_at,
        boundary,
    );
    assert_eq!(latest, Some((Some(2.), Some(12.))));
    assert_eq!(tracker.fields().path_probe_count, Some(2));
    assert_eq!(tracker.fields().gateway_rtt_ms, Some(36.));
    tracker.reset();
    assert_eq!(
        record_recent_probes(
            &mut tracker,
            [(probe(90., 30), epoch + Duration::from_secs(30))],
            recovered_at,
            boundary,
        ),
        None
    );
    assert!(tracker.fields().gateway_rtt_ms.is_none());
}

#[test]
fn completed_load_reports_clear_on_ap_or_network_changes_but_survive_redaction() {
    for network_change in [false, true] {
        let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
        app.apply_event(live_event(
            &app,
            measure::demo_signal(0),
            Duration::from_secs(1),
        ));
        app.load_latency = Some(demo_load_latency());
        let redacted = Signal {
            ssid: Some("<redacted>".into()),
            ..Default::default()
        };
        app.apply_event(live_event(&app, redacted, Duration::from_secs(2)));
        assert!(app.load_latency.is_some());
        let mut changed = measure::demo_signal(if network_change { 0 } else { 20 });
        if network_change {
            changed.ssid = Some("Another actual WiFi".into());
        }
        app.apply_event(live_event(&app, changed, Duration::from_secs(3)));
        assert!(app.load_latency.is_none());
    }
}

#[test]
fn slow_radio_sampling_preserves_all_current_path_probes_for_loss_and_jitter() {
    let epoch = Instant::now();
    let samples = (1..=10).map(|second| {
        let rtt = (second != 2 && second != 3).then_some(second as f64);
        (
            measure::PathSample {
                gateway_target: Some("192.168.1.1".into()),
                gateway_ms: rtt,
                internet_target: "1.1.1.1".into(),
                internet_ms: rtt,
                primary_failed: rtt.is_none(),
                observed_at: Duration::from_secs(second),
            },
            epoch + Duration::from_secs(second),
        )
    });
    let mut tracker = measure::PathTracker::default();
    let latest = record_recent_probes(
        &mut tracker,
        samples,
        epoch + Duration::from_secs(10),
        epoch,
    );
    assert_eq!(latest, Some((Some(10.), Some(10.))));
    assert_eq!(tracker.fields().path_probe_count, Some(10));
    assert_eq!(tracker.fields().gateway_loss_pct, Some(20.));
    assert_eq!(tracker.diagnostics().gateway_jitter_ms, Some(1.));
}

#[test]
fn queued_load_reports_do_not_reappear_after_a_later_ap_or_network_change() {
    for network_change in [false, true] {
        let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
        let original = measure::demo_signal(0);
        app.apply_event(live_event(&app, original.clone(), Duration::from_secs(1)));
        let report = Box::new(LoadReport {
            latency: demo_load_latency(),
            association: original.clone(),
            completed_at: app.diagnostics_epoch + Duration::from_secs(2),
        });
        let mut changed = measure::demo_signal(if network_change { 0 } else { 20 });
        if network_change {
            changed.ssid = Some("Another actual WiFi".into());
        }
        app.apply_event(live_event(&app, changed, Duration::from_secs(3)));
        app.apply_event(WorkerEvent::Measured {
            target: Box::new(app.active.clone()),
            generation: app.generation.load(Ordering::Relaxed),
            kind: WorkKind::Probe,
            result: Ok((original, demo_speed(0), None, Some(report))),
        });
        assert!(app.load_latency.is_none());
    }
}

#[test]
fn a_roam_during_load_uses_completion_identity_and_ignores_older_radio_observations() {
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    let original = measure::demo_signal(0);
    let roamed = measure::demo_signal(20);
    app.apply_event(live_event(&app, original.clone(), Duration::from_secs(1)));
    app.apply_event(live_event(&app, roamed.clone(), Duration::from_secs(2)));
    app.apply_event(WorkerEvent::Measured {
        target: Box::new(app.active.clone()),
        generation: app.generation.load(Ordering::Relaxed),
        kind: WorkKind::Probe,
        result: Ok((
            original.clone(),
            demo_speed(0),
            None,
            Some(Box::new(LoadReport {
                latency: demo_load_latency(),
                association: roamed.clone(),
                completed_at: app.diagnostics_epoch + Duration::from_secs(3),
            })),
        )),
    });
    assert!(app.load_latency.is_some());
    app.apply_event(live_event(
        &app,
        original.clone(),
        Duration::from_millis(2500),
    ));
    assert!(app.load_latency.is_some());
    app.apply_event(live_event(&app, roamed, Duration::from_secs(4)));
    assert!(app.load_latency.is_some());
    app.apply_event(live_event(&app, original, Duration::from_secs(5)));
    assert!(app.load_latency.is_none());
}

#[test]
fn unsaved_speed_probe_runs_without_a_selected_survey_location_or_spot() {
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    app.finish_session().unwrap();
    app.active = Reading::default();
    app.picker = None;
    app.diagnostics_open = true;
    let readings_before = app.store.list_readings(&Filter::default()).unwrap().len();
    app.press(key(KeyCode::Char('t')));
    assert!(app.speed.down_mbps.is_some());
    assert!(app.load_latency.is_some());
    assert_eq!(
        app.store.list_readings(&Filter::default()).unwrap().len(),
        readings_before
    );
    assert!(app.start_work(WorkKind::Snapshot).is_err());
    assert!(app.start_work(WorkKind::Benchmark).is_err());
    app.no_speedtest = true;
    assert!(app.start_work(WorkKind::Probe).is_err());
}

#[test]
fn newer_redacted_bssid_does_not_reject_a_report_from_a_roam_during_load() {
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    let original = measure::demo_signal(0);
    app.apply_event(live_event(&app, original.clone(), Duration::from_secs(1)));
    let mut redacted = original.clone();
    redacted.bssid = None;
    app.apply_event(live_event(&app, redacted, Duration::from_secs(4)));
    app.apply_event(WorkerEvent::Measured {
        target: Box::new(app.active.clone()),
        generation: app.generation.load(Ordering::Relaxed),
        kind: WorkKind::Probe,
        result: Ok((
            original,
            demo_speed(0),
            None,
            Some(Box::new(LoadReport {
                latency: demo_load_latency(),
                association: measure::demo_signal(20),
                completed_at: app.diagnostics_epoch + Duration::from_secs(3),
            })),
        )),
    });
    assert!(app.load_latency.is_some());
}
#[test]
fn evaluation_filters_groups_and_compares_real_demo_walks() {
    let readings = demo_store()
        .unwrap()
        .list_readings(&Filter::default())
        .unwrap();
    let mut app = EvalApp::new(readings, "demo".into(), true).unwrap();
    assert_eq!(app.rows.len(), 6);
    app.press(key(KeyCode::Char('m')));
    assert_eq!(app.rows.len(), 36);
    app.press(key(KeyCode::Char('c')));
    app.press(key(KeyCode::Enter));
    app.press(key(KeyCode::Enter));
    assert!(app.comparing());
    assert_eq!(app.comparisons.len(), 6);
    app.press(key(KeyCode::Char('x')));
    assert_eq!(app.before, Some(2));
    app.press(key(KeyCode::Esc));
    assert!(!app.comparing());
}

#[test]
fn partial_speed_failure_preserves_signal_and_displays_warning() {
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    let mut target = app.active.clone();
    target.ts = timestamp();
    target.walk_id = app.walk_id;
    let failed_load_report = LoadLatency {
        idle: demo_load_latency().idle,
        ..Default::default()
    };
    app.apply_event(WorkerEvent::Measured {
        target: Box::new(target),
        generation: app.generation.load(Ordering::Relaxed),
        kind: WorkKind::Snapshot,
        result: Ok((
            measure::demo_signal(0),
            Speed {
                server: Some("ERROR: no speedtest".into()),
                ..Default::default()
            },
            Some("no speedtest".into()),
            Some(load_report(&app, failed_load_report)),
        )),
    });
    let reading = &app
        .store
        .list_readings(&Filter {
            walk_id: app.walk_id,
            ..Default::default()
        })
        .unwrap()[0];
    assert!(reading.signal.rssi.is_some());
    assert!(reading.speed.down_mbps.is_none());
    assert!(app.message.contains("speed unavailable"));
    let report = app.load_latency.as_ref().unwrap();
    assert_eq!(report.idle.path.gateway_rtt_ms, Some(2.));
    assert!(report.loaded.path.gateway_rtt_ms.is_none());
}

#[test]
fn spot_change_rejects_queued_live_path_metrics_and_unsaved_speed() {
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    let generation = app.generation.load(Ordering::Relaxed);
    app.path.gateway_rtt_ms = Some(99.0);
    app.speed = demo_speed(0);
    app.select_spot(app.places[1].spot_id).unwrap();
    app.apply_event(WorkerEvent::Live {
        generation,
        observed_at: Instant::now(),
        result: Ok(LiveSample {
            signal: measure::demo_signal(0),
            path: PathFields {
                gateway_rtt_ms: Some(99.0),
                ..Default::default()
            },
            diagnostics: PathDiagnostics::default(),
            latest_probe: None,
            rates: None,
        }),
    });
    assert!(app.path.gateway_rtt_ms.is_none());
    assert!(app.speed.down_mbps.is_none());
    assert!(app.timeline.samples.is_empty());
}

#[test]
fn measurements_serialize_and_benchmark_replacement_requires_selection() {
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    app.pending = 1;
    for kind in [WorkKind::Snapshot, WorkKind::Benchmark, WorkKind::Probe] {
        assert!(app.start_work(kind).is_err());
    }
    app.pending = 0;
    app.press(key(KeyCode::Char('b')));
    let original = app.benchmark.clone().unwrap();
    app.press(key(KeyCode::Char('b')));
    assert_eq!(
        app.picker.as_ref().unwrap().level,
        Level::BenchmarkConfirmation
    );
    app.press(key(KeyCode::Enter));
    assert_eq!(app.benchmark.as_ref().unwrap().ts, original.ts);
}

#[test]
fn comparison_rejects_empty_walk_and_uses_signal_medians() {
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    let empty = app
        .store
        .create_walk(app.active.location_id, app.active.ssid_id, "Empty")
        .unwrap();
    assert!(app.select_baseline(empty).is_err());
    let before = app
        .store
        .create_walk(app.active.location_id, app.active.ssid_id, "Medians")
        .unwrap();
    for rssi in [-90.0, -60.5, -20.0] {
        let mut reading = app.active.clone();
        reading.ts = timestamp();
        reading.walk_id = Some(before);
        reading.signal.rssi = Some(rssi);
        app.store.add_reading(&reading).unwrap();
    }
    app.select_baseline(before).unwrap();
    assert_eq!(app.baseline.as_ref().unwrap().signal.rssi, Some(-60.5));
}

#[test]
fn numeric_ssid_selection_uses_the_stored_name() {
    let mut selected = args();
    selected.ssid = Some(" 1 ".into());
    let mut app = WalkApp::new(demo_store().unwrap(), &selected).unwrap();
    assert_eq!(app.active.signal.ssid.as_deref(), Some("Demo WiFi"));
    app.set_ssid(" 1 ").unwrap();
    app.press(key(KeyCode::Char('s')));
    assert_eq!(app.saved, 1);
}

#[test]
fn failed_startup_comparison_finishes_the_created_walk() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("test.sqlite3");
    let store = Store::open(&path).unwrap();
    let location = store.create_location("Home").unwrap();
    let mut selected = args();
    selected.demo = false;
    selected.location = Some("Home".into());
    selected.ssid = Some("WiFi".into());
    selected.compare_to = Some("Missing".into());
    assert!(WalkApp::new(store, &selected).is_err());
    let walks = Store::open_readonly(path)
        .unwrap()
        .list_walks(location)
        .unwrap();
    assert_eq!(walks.len(), 1);
    assert!(walks[0].ended_at.is_some());
}

#[test]
fn late_probe_does_not_populate_a_different_network_session() {
    let mut app = WalkApp::new(demo_store().unwrap(), &args()).unwrap();
    let target = app.active.clone();
    app.set_ssid("Other WiFi").unwrap();
    app.apply_event(WorkerEvent::Measured {
        target: Box::new(target),
        generation: app.generation.load(Ordering::Relaxed),
        kind: WorkKind::Probe,
        result: Ok((measure::demo_signal(0), demo_speed(0), None, None)),
    });
    assert!(app.speed.down_mbps.is_none());
}

#[test]
fn cancelling_scope_picker_preserves_existing_location_filter() {
    let mut app = EvalApp::new(
        demo_store()
            .unwrap()
            .list_readings(&Filter::default())
            .unwrap(),
        "demo".into(),
        true,
    )
    .unwrap();
    app.scope = Some("Demo home".into());
    app.refresh().unwrap();
    app.open_scope(Level::Ssid);
    app.press(key(KeyCode::Esc));
    app.press(key(KeyCode::Tab));
    assert_eq!(app.scope_kind, Level::Location);
    assert_eq!(app.rows.len(), 6);
}

#[test]
fn unavailable_ssid_is_distinct_from_a_network_named_unknown() {
    let mut readings = demo_store()
        .unwrap()
        .list_readings(&Filter::default())
        .unwrap();
    readings.truncate(2);
    readings[0].signal.ssid = None;
    readings[1].signal.ssid = Some("Unknown".into());
    let mut app = EvalApp::new(readings, "demo".into(), true).unwrap();
    app.open_scope(Level::Ssid);
    app.picker.as_mut().unwrap().selected = 1;
    app.press(key(KeyCode::Enter));
    assert_eq!(app.scoped().len(), 1);
    assert!(app.scoped()[0].signal.ssid.is_none());
    app.open_scope(Level::Ssid);
    app.picker.as_mut().unwrap().selected = 2;
    app.press(key(KeyCode::Enter));
    assert_eq!(app.scoped().len(), 1);
    assert_eq!(app.scoped()[0].signal.ssid.as_deref(), Some("Unknown"));
}
