use super::*;
use crate::{
    app::{EvalApp, demo_store},
    store::Filter,
};
use ratatui::{Terminal, backend::TestBackend};

fn rendered_style(draw: impl FnOnce(&mut Frame), needle: &str) -> Style {
    let mut terminal = Terminal::new(TestBackend::new(120, 36)).unwrap();
    terminal.draw(draw).unwrap();
    let buffer = terminal.backend().buffer();
    for y in 0..buffer.area.height {
        let line: String = (0..buffer.area.width)
            .map(|x| buffer[(x, y)].symbol())
            .collect();
        if let Some(byte_index) = line.find(needle) {
            let x = line[..byte_index].chars().count() as u16;
            return buffer[(x, y)].style();
        }
    }
    panic!("missing rendered text: {needle}");
}

#[test]
fn live_signal_cards_restore_quality_colors_and_muted_missing_values() {
    let mut app = WalkApp::new(
        demo_store().unwrap(),
        &crate::cli::WalkArgs {
            demo: true,
            ..Default::default()
        },
    )
    .unwrap();
    for (rssi, snr, expected) in [
        (Some(-60.0), Some(25.0), GOOD),
        (Some(-70.0), Some(15.0), WARN),
        (Some(-71.0), Some(14.0), BAD),
        (None, None, DIM),
    ] {
        app.signal.rssi = rssi;
        app.signal.snr = snr;
        assert_eq!(
            rendered_style(
                |frame| draw_walk(frame, &app),
                &format!("{} dBm", display(rssi))
            )
            .fg,
            Some(expected)
        );
        assert_eq!(
            rendered_style(
                |frame| draw_walk(frame, &app),
                &format!("{} dB", display(snr))
            )
            .fg,
            Some(expected)
        );
    }
}

#[test]
fn selected_ranking_row_retains_metric_color_and_uses_the_focused_metric() {
    let mut reading = crate::model::Reading::default();
    reading.signal.rssi = Some(-77.0);
    reading.signal.snr = Some(31.0);
    let mut app = EvalApp::new(vec![reading], "test".into(), false).unwrap();
    let rssi = rendered_style(|frame| ranking_table(frame, &app, frame.area()), "-77.0");
    assert_eq!(rssi.fg, Some(BAD));
    assert_eq!(rssi.bg, Some(PANEL));
    app.metric_index = crate::evaluation::METRICS
        .iter()
        .position(|m| m.key == "snr")
        .unwrap();
    let snr = rendered_style(|frame| ranking_table(frame, &app, frame.area()), "31.0");
    assert_eq!(snr.fg, Some(GOOD));
    assert_eq!(snr.bg, Some(PANEL));
}

#[test]
fn comparisons_distinguish_improvement_regression_and_displayed_zero() {
    for (key, before, after, label, color) in [
        ("rssi", -70.0, -60.0, "+10.0", GOOD),
        ("rssi", -60.0, -70.0, "-10.0", BAD),
        ("ping_ms", 20.0, 10.0, "-10.0", GOOD),
        ("ping_ms", 10.0, 20.0, "+10.0", BAD),
        ("ping_ms", 10.0, 9.96, "+0.0", DIM),
    ] {
        let mut before_reading = crate::model::Reading {
            walk_id: Some(1),
            ..Default::default()
        };
        before_reading.signal.rssi = Some(before);
        before_reading.speed.ping_ms = Some(before);
        let mut after_reading = before_reading.clone();
        after_reading.walk_id = Some(2);
        after_reading.signal.rssi = Some(after);
        after_reading.speed.ping_ms = Some(after);
        let mut app = EvalApp::new(vec![], "test".into(), false).unwrap();
        app.before = Some(1);
        app.after = Some(2);
        app.metric_index = crate::evaluation::COMPARISON_METRICS
            .iter()
            .position(|m| *m == key)
            .unwrap();
        app.comparisons =
            crate::evaluation::compare_walks(&[before_reading, after_reading], 1, 2).unwrap();
        assert_eq!(
            rendered_style(|frame| comparison_table(frame, &app, frame.area()), label).fg,
            Some(color)
        );
    }
}

#[test]
fn path_colors_separate_replies_loss_and_unknown_targets() {
    for (rtt, loss, label, expected) in [
        (Some(12.3), Some(25.0), "12.3 ms", GOOD),
        (Some(12.3), Some(25.0), "25.0%", WARN),
        (None, Some(100.0), "— ms", BAD),
        (None, None, "— ms", DIM),
        (None, Some(25.0), "— ms", DIM),
        (Some(12.3), Some(0.0), "0.0%", DIM),
    ] {
        let style = rendered_style(
            |frame| {
                frame.render_widget(Paragraph::new(path_summary(rtt, loss)), frame.area());
            },
            label,
        );
        assert_eq!(style.fg, Some(expected));
    }
    let missing_p95 = rendered_style(
        |frame| {
            frame.render_widget(
                Paragraph::new(labeled_path("Gateway: RTT ", None, None, None)),
                frame.area(),
            );
        },
        "— ms",
    );
    assert_eq!(missing_p95.fg, Some(DIM));
}

#[test]
fn history_colors_each_sample_and_keeps_real_weak_readings_visible() {
    let history = [Some(-60.0), Some(-70.0), Some(-100.0), None].into();
    let mut terminal = Terminal::new(TestBackend::new(4, 5)).unwrap();
    terminal
        .draw(|frame| {
            frame.render_widget(
                Sparkline::default()
                    .data(signal_history(&history, frame.area()))
                    .max(80),
                frame.area(),
            );
        })
        .unwrap();
    let buffer = terminal.backend().buffer();
    for (x, color) in [(0, GOOD), (1, WARN), (2, BAD)] {
        assert_eq!(buffer[(x, 4)].fg, color);
        assert_ne!(buffer[(x, 4)].symbol(), " ");
    }
    assert_eq!(buffer[(3, 4)].symbol(), " ");

    // Narrow terminals average present samples; all-missing buckets remain gaps.
    let history = [
        Some(-55.0),
        Some(-65.0),
        None,
        None,
        Some(-75.0),
        Some(-85.0),
    ]
    .into();
    let mut terminal = Terminal::new(TestBackend::new(3, 5)).unwrap();
    terminal
        .draw(|frame| {
            frame.render_widget(
                Sparkline::default()
                    .data(signal_history(&history, frame.area()))
                    .max(80),
                frame.area(),
            );
        })
        .unwrap();
    let buffer = terminal.backend().buffer();
    assert_eq!(buffer[(0, 4)].fg, GOOD);
    assert_eq!(buffer[(1, 4)].symbol(), " ");
    assert_eq!(buffer[(2, 4)].fg, BAD);
    assert_ne!(buffer[(2, 4)].symbol(), " ");
    assert!(signal_history(&history, Rect::default()).is_empty());
}

#[test]
fn reading_details_keep_quality_and_benchmark_change_colors() {
    let mut reading = crate::model::Reading::default();
    reading.signal.rssi = Some(-76.0);
    reading.signal.snr = Some(27.0);
    reading.delta_rssi = Some(-13.0);
    reading.delta_down_mbps = Some(82.0);
    let mut app = EvalApp::new(vec![reading], "test".into(), false).unwrap();
    assert_eq!(
        rendered_style(|frame| reading_detail(frame, &app), "-76.0").fg,
        Some(BAD)
    );
    assert_eq!(
        rendered_style(|frame| reading_detail(frame, &app), "27.0").fg,
        Some(GOOD)
    );
    app.detail_page = 2;
    assert_eq!(
        rendered_style(|frame| reading_detail(frame, &app), "-13.0").fg,
        Some(BAD)
    );
    assert_eq!(
        rendered_style(|frame| reading_detail(frame, &app), "+82.0").fg,
        Some(GOOD)
    );
}

fn screen(width: u16, height: u16, draw: impl FnOnce(&mut Frame)) -> String {
    let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
    terminal.draw(draw).unwrap();
    terminal
        .backend()
        .buffer()
        .content
        .iter()
        .map(|cell| cell.symbol())
        .collect()
}

#[test]
fn evaluation_render_marks_simulation_and_shows_ranked_diagnostics() {
    let app = EvalApp::new(
        demo_store()
            .unwrap()
            .list_readings(&Filter::default())
            .unwrap(),
        "demo".into(),
        true,
    )
    .unwrap();
    let output = screen(120, 35, |frame| draw_eval(frame, &app));
    for expected in [
        "WIFIMAP",
        "DEMO / SIMULATED",
        "Ranked readings",
        "Living room",
        "RSSI",
        "Selected place",
    ] {
        assert!(output.contains(expected), "missing {expected}");
    }
}

#[test]
fn undersized_terminal_has_actionable_fallback() {
    let app = EvalApp::new(vec![], "test".into(), false).unwrap();
    let output = screen(60, 15, |frame| draw_eval(frame, &app));
    assert!(output.contains("Resize terminal"));
}

#[test]
fn minimum_size_and_empty_report_render_without_overflow() {
    let app = EvalApp::new(vec![], "test".into(), false).unwrap();
    let output = screen(76, 24, |frame| draw_eval(frame, &app));
    assert!(output.contains("No readings"));
}

#[test]
fn walk_render_uses_reference_palette_and_live_survey_layout() {
    let mut app = WalkApp::new(
        demo_store().unwrap(),
        &crate::cli::WalkArgs {
            demo: true,
            ..Default::default()
        },
    )
    .unwrap();
    app.speed.down_mbps = Some(10000.0);
    app.speed.up_mbps = Some(1000.0);
    let output = screen(120, 36, |frame| draw_walk(frame, &app));
    for expected in [
        "DEMO / SIMULATED",
        "Survey spots",
        "Live diagnostics",
        "Signal history",
        "Reference / comparison",
        "Bandwidth",
        "Down 10000.0 Mbps",
        "Up   1000.0 Mbps",
    ] {
        assert!(output.contains(expected), "missing {expected}");
    }
    let mut terminal = Terminal::new(TestBackend::new(76, 24)).unwrap();
    terminal.draw(|frame| draw_walk(frame, &app)).unwrap();
    assert_eq!(terminal.backend().buffer()[(0, 0)].bg, BG);
    let compact = screen(76, 24, |frame| draw_walk(frame, &app));
    assert!(compact.contains("Down 10000.0 Mbps"));
    assert!(compact.contains("Up   1000.0 Mbps"));
}

#[test]
fn diagnostics_render_roaming_and_load_at_minimum_terminal_size() {
    use crate::{model::Signal, path_diagnostics::LoadLatency};
    use std::time::Duration;

    let mut app = WalkApp::new(
        demo_store().unwrap(),
        &crate::cli::WalkArgs {
            demo: true,
            ..Default::default()
        },
    )
    .unwrap();
    app.diagnostics_open = true;
    let mut signal = Signal {
        ssid: Some("Actual WiFi".into()),
        bssid: Some("02:00:00:00:00:01".into()),
        rssi: Some(-72.),
        tx_rate: Some("144".into()),
        ..Default::default()
    };
    app.timeline.record(
        Duration::from_secs(1),
        Some(&signal),
        Some(12.),
        Some(60.),
        None,
    );
    signal.bssid = Some("02:00:00:00:00:02".into());
    signal.rssi = Some(-53.);
    signal.tx_rate = Some("866".into());
    app.timeline.record(
        Duration::from_secs(2),
        Some(&signal),
        Some(2.),
        Some(18.),
        None,
    );
    app.signal = signal;
    app.path.gateway_rtt_ms = Some(2.);
    app.path.gateway_loss_pct = Some(0.);
    app.path_diagnostics.gateway_jitter_ms = Some(1.2);
    let mut load = LoadLatency::default();
    load.idle.path.gateway_rtt_ms = Some(2.);
    load.loaded.path.gateway_rtt_ms = Some(22.);
    load.idle.path.internet_rtt_ms = Some(18.);
    load.loaded.path.internet_rtt_ms = Some(78.);
    app.load_latency = Some(load);
    for (width, height) in [(76, 24), (120, 36)] {
        let output = screen(width, height, |frame| draw_walk(frame, &app));
        for expected in [
            "Connection diagnostics",
            "Actual WiFi",
            "Jitter",
            "Loss",
            "Recovery",
            "Roam",
            "02:00:00:00:00:02",
            "+20.0",
            "+60.0",
        ] {
            assert!(
                output.contains(expected),
                "missing {expected} at {width}x{height}"
            );
        }
    }
    app.diagnostics_events = false;
    let output = screen(76, 24, |frame| draw_walk(frame, &app));
    for expected in [
        "Signal / rate / AP timeline",
        "RSSI",
        "SNR",
        "TX",
        "866",
        "-53",
    ] {
        assert!(output.contains(expected), "missing {expected}");
    }
}

#[test]
fn diagnostics_missing_identity_and_empty_history_are_actionable() {
    let mut app = WalkApp::new(
        demo_store().unwrap(),
        &crate::cli::WalkArgs {
            demo: true,
            ..Default::default()
        },
    )
    .unwrap();
    app.diagnostics_open = true;
    app.signal.bssid = None;
    let output = screen(76, 24, |frame| draw_walk(frame, &app));
    assert!(output.contains("BSSID unavailable"));
    assert!(output.contains("No observed connection changes"));
    assert!(output.contains("Press t"));
    app.diagnostics_events = false;
    let output = screen(76, 24, |frame| draw_walk(frame, &app));
    assert!(output.contains("Waiting for connection samples"));
    app.load_running = true;
    let output = screen(76, 24, |frame| draw_walk(frame, &app));
    assert!(output.contains("Measuring idle"));
}

#[test]
fn diagnostics_explain_invalidated_load_comparisons() {
    use crate::path_diagnostics::LoadLatency;

    let mut app = WalkApp::new(
        demo_store().unwrap(),
        &crate::cli::WalkArgs {
            demo: true,
            ..Default::default()
        },
    )
    .unwrap();
    app.diagnostics_open = true;
    let mut report = LoadLatency {
        gateway_changed: true,
        ..Default::default()
    };
    report.idle.path.internet_rtt_ms = Some(18.);
    report.loaded.path.internet_rtt_ms = Some(78.);
    app.load_latency = Some(report.clone());
    let output = screen(76, 24, |frame| draw_walk(frame, &app));
    assert!(output.contains("Gateway changed: comparison"));
    assert!(output.contains("+60.0"));
    report.connection_changed = true;
    app.load_latency = Some(report);
    let output = screen(76, 24, |frame| draw_walk(frame, &app));
    assert!(output.contains("Connection changed; latency comparison unavailable"));
    assert!(!output.contains("+60.0"));
}
