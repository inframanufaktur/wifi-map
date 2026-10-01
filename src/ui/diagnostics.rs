//! Session diagnostics for the Wi-Fi connection currently in use.
use std::time::Duration;

use ratatui::{
    Frame,
    layout::{Constraint, Layout, Rect},
    style::Style,
    text::Line,
    widgets::{Cell, Paragraph, Row, Table, Wrap},
};

use crate::{
    app::WalkApp,
    path_diagnostics::LoadLatency,
    timeline::{TimelineEvent, TimelineEventKind, TimelineSample},
};

use super::{ACCENT, DIM, WARN, base, block, footer, help, number, signed, text};

pub(super) fn draw(frame: &mut Frame, app: &WalkApp) {
    if base(
        frame,
        "Connection diagnostics",
        app.demo,
        app.sample_error,
        true,
    )
    .is_none()
    {
        return;
    }
    let load_report = app.load_latency.as_ref().filter(|_| !app.load_running);
    let regions = Layout::vertical([
        Constraint::Length(3),
        Constraint::Length(1),
        Constraint::Length(5),
        Constraint::Length(if load_report.is_some() { 7 } else { 3 }),
        Constraint::Min(5),
        Constraint::Length(3),
    ])
    .split(frame.area());
    let dwell = app
        .timeline
        .current_ap_dwell()
        .map(elapsed)
        .unwrap_or_else(|| "—".into());
    frame.render_widget(
        Paragraph::new(format!(
            " {} · {} · observed AP dwell {dwell}",
            text(&app.signal.ssid),
            app.signal.bssid.as_deref().unwrap_or("BSSID unavailable"),
        ))
        .style(Style::default().fg(DIM)),
        regions[1],
    );
    paths(frame, app, regions[2]);
    if let Some(report) = load_report {
        load(frame, report, regions[3]);
    } else {
        frame.render_widget(
            Paragraph::new(if app.load_running {
                "Measuring idle, load and recovery latency…"
            } else {
                "Press t for idle → loaded → recovery latency."
            })
            .block(block(" Latency under load "))
            .style(Style::default().fg(DIM)),
            regions[3],
        );
    }
    if app.diagnostics_events {
        events(frame, app, regions[4]);
    } else {
        samples(frame, app, regions[4]);
    }
    footer(
        frame,
        &app.message,
        "Tab events/samples  ↑/↓ scroll  t speed probe  d/Esc back  ? help  q quit",
        regions[5],
    );
    if app.help {
        help(frame, false);
    }
}

fn paths(frame: &mut Frame, app: &WalkApp, area: Rect) {
    let path = &app.path;
    let diagnostics = &app.path_diagnostics;
    let rows = [
        (
            "Gateway",
            path.gateway_rtt_ms,
            path.gateway_p95_ms,
            diagnostics.gateway_jitter_ms,
            path.gateway_loss_pct,
            path.gateway_max_outage_ms,
        ),
        (
            "Internet",
            path.internet_rtt_ms,
            path.internet_p95_ms,
            diagnostics.internet_jitter_ms,
            path.internet_loss_pct,
            path.internet_max_outage_ms,
        ),
    ];
    frame.render_widget(
        Table::new(
            rows.into_iter()
                .map(|(name, median, p95, jitter, loss, outage)| {
                    Row::new([
                        name.to_owned(),
                        number(median),
                        number(p95),
                        number(jitter),
                        number(loss),
                        outage
                            .map(|n| format!("{:.1}s", n as f64 / 1000.))
                            .unwrap_or_else(|| "—".into()),
                    ])
                }),
            [
                Constraint::Length(12),
                Constraint::Fill(1),
                Constraint::Fill(1),
                Constraint::Fill(1),
                Constraint::Fill(1),
                Constraint::Fill(1),
            ],
        )
        .header(
            Row::new(["Path", "RTT ms", "P95 ms", "Jitter ms", "Loss %", "Outage"])
                .style(Style::default().fg(DIM)),
        )
        .block(block(" Path quality · rolling probes ")),
        area,
    );
}

fn load(frame: &mut Frame, report: &LoadLatency, area: Rect) {
    let regions = Layout::vertical([Constraint::Length(6), Constraint::Length(1)]).split(area);
    let columns = Layout::horizontal([Constraint::Fill(1), Constraint::Fill(1)]).split(regions[0]);
    for (gateway, title, column) in [
        (true, " Load · gateway ", columns[0]),
        (false, " Load · Internet ", columns[1]),
    ] {
        let rows = [
            ("Idle", &report.idle),
            ("Loaded", &report.loaded),
            ("Recovery", &report.recovery),
        ]
        .map(|(name, phase)| {
            let path = &phase.path;
            let (median, p95, loss) = if gateway {
                (
                    path.gateway_rtt_ms,
                    path.gateway_p95_ms,
                    path.gateway_loss_pct,
                )
            } else {
                (
                    path.internet_rtt_ms,
                    path.internet_p95_ms,
                    path.internet_loss_pct,
                )
            };
            Row::new([name.to_owned(), number(median), number(p95), number(loss)])
        });
        frame.render_widget(
            Table::new(
                rows,
                [
                    Constraint::Length(8),
                    Constraint::Fill(1),
                    Constraint::Fill(1),
                    Constraint::Fill(1),
                ],
            )
            .header(
                Row::new(["Phase", "RTT ms", "P95 ms", "Loss %"]).style(Style::default().fg(DIM)),
            )
            .block(block(title)),
            column,
        );
    }
    let delta =
        |idle: Option<f64>, loaded: Option<f64>| signed(idle.zip(loaded).map(|(a, b)| b - a));
    let comparison = if report.connection_changed {
        " Connection changed; latency comparison unavailable.".to_owned()
    } else if report.gateway_changed {
        format!(
            " Gateway changed: comparison — · Internet loaded − idle {} ms",
            delta(
                report.idle.path.internet_rtt_ms,
                report.loaded.path.internet_rtt_ms
            )
        )
    } else {
        format!(
            " Loaded − idle: gateway {} ms · Internet {} ms",
            delta(
                report.idle.path.gateway_rtt_ms,
                report.loaded.path.gateway_rtt_ms
            ),
            delta(
                report.idle.path.internet_rtt_ms,
                report.loaded.path.internet_rtt_ms
            )
        )
    };
    frame.render_widget(
        Paragraph::new(comparison).style(Style::default().fg(ACCENT)),
        regions[1],
    );
}

fn events(frame: &mut Frame, app: &WalkApp, area: Rect) {
    let title = format!(
        " AP / connection events · {} retained · Tab samples ",
        app.timeline.events.len()
    );
    if app.timeline.events.is_empty() {
        frame.render_widget(Paragraph::new("No observed connection changes.\nTab shows correlated signal, rate and latency samples.")
            .style(Style::default().fg(DIM)).wrap(Wrap { trim: true }).block(block(title)), area);
        return;
    }
    let offset = app
        .diagnostics_offset
        .min(app.timeline.events.len().saturating_sub(1));
    let rows = app
        .timeline
        .events
        .iter()
        .rev()
        .skip(offset)
        .take(usize::from(area.height.saturating_sub(2)) / 3)
        .map(|event| Row::new([Cell::from(event_lines(event))]).height(3));
    frame.render_widget(
        Table::new(rows, [Constraint::Min(1)]).block(block(title)),
        area,
    );
}

fn event_lines(event: &TimelineEvent) -> Vec<Line<'static>> {
    let before = event.before.as_ref();
    let after = &event.after;
    let identity = |sample: Option<&TimelineSample>| -> String {
        let signal = sample.and_then(|sample| sample.signal.as_ref());
        match event.kind {
            TimelineEventKind::NetworkChange => signal.and_then(|s| s.ssid.clone()),
            TimelineEventKind::ChannelChange => signal.and_then(|s| s.channel.clone()),
            _ => signal.and_then(|s| s.bssid.clone()),
        }
        .unwrap_or_else(|| "—".into())
    };
    let label = match event.kind {
        TimelineEventKind::Roam => "Roam",
        TimelineEventKind::ApChange => "AP change",
        TimelineEventKind::NetworkChange => "Network change",
        TimelineEventKind::ChannelChange => "Channel change",
        TimelineEventKind::SampleUnavailable => "Sample unavailable",
        TimelineEventKind::SampleRecovered => "Sampling resumed",
    };
    let old = before.and_then(|s| s.signal.as_ref());
    let new = after.signal.as_ref();
    vec![
        Line::styled(
            format!(
                "{}  {label}{}  {} → {}",
                elapsed(after.observed_at),
                if event.identity_gap { " (gap)" } else { "" },
                identity(before),
                identity(Some(after))
            ),
            Style::default().fg(if event.identity_gap { WARN } else { ACCENT }),
        ),
        Line::from(format!(
            "  RSSI {} → {} dBm · TX {} → {} Mbps",
            number(old.and_then(|s| s.rssi)),
            number(new.and_then(|s| s.rssi)),
            old.and_then(|s| s.tx_rate.as_deref()).unwrap_or("—"),
            new.and_then(|s| s.tx_rate.as_deref()).unwrap_or("—")
        )),
        Line::styled(
            format!(
                "  GW {} → {} ms · Internet {} → {} ms · observed {:.1}s",
                number(before.and_then(|s| s.gateway_ms)),
                number(after.gateway_ms),
                number(before.and_then(|s| s.internet_ms)),
                number(after.internet_ms),
                event.observed_interval.as_secs_f64()
            ),
            Style::default().fg(DIM),
        ),
    ]
}

fn samples(frame: &mut Frame, app: &WalkApp, area: Rect) {
    let title = " Signal / rate / AP timeline · newest first · Tab events ";
    if app.timeline.samples.is_empty() {
        frame.render_widget(
            Paragraph::new("Waiting for connection samples…")
                .style(Style::default().fg(DIM))
                .block(block(title)),
            area,
        );
        return;
    }
    let wide = area.width >= 105;
    let offset = app
        .diagnostics_offset
        .min(app.timeline.samples.len().saturating_sub(1));
    let rows = app
        .timeline
        .samples
        .iter()
        .rev()
        .skip(offset)
        .take(usize::from(area.height.saturating_sub(3)))
        .map(|sample| {
            let signal = sample.signal.as_ref();
            let mut values = vec![
                elapsed(sample.observed_at),
                signal
                    .and_then(|s| s.bssid.clone())
                    .unwrap_or_else(|| "—".into()),
                signal
                    .and_then(|s| s.channel.as_deref())
                    .and_then(|s| s.split_whitespace().next())
                    .unwrap_or("—")
                    .to_owned(),
                number(signal.and_then(|s| s.rssi)),
                number(signal.and_then(|s| s.snr)),
                signal
                    .and_then(|s| s.tx_rate.clone())
                    .unwrap_or_else(|| "—".into()),
                number(sample.gateway_ms),
                number(sample.internet_ms),
            ];
            if wide {
                values.push(
                    sample
                        .rates_mbps
                        .map(|(rx, tx)| format!("{rx:.1}/{tx:.1}"))
                        .unwrap_or_else(|| "—".into()),
                );
            }
            Row::new(values).style(Style::default().fg(if signal.is_some() {
                ratatui::style::Color::White
            } else {
                DIM
            }))
        });
    let mut columns = vec![
        Constraint::Length(7),
        Constraint::Length(17),
        Constraint::Length(4),
        Constraint::Length(6),
        Constraint::Length(5),
        Constraint::Length(7),
        Constraint::Fill(1),
        Constraint::Fill(1),
    ];
    let mut labels = vec![
        "Elapsed", "BSSID", "Ch", "RSSI", "SNR", "TX Mbps", "GW ms", "Net ms",
    ];
    if wide {
        columns.push(Constraint::Length(12));
        labels.push("↓/↑ Mbps");
    }
    frame.render_widget(
        Table::new(rows, columns)
            .header(Row::new(labels).style(Style::default().fg(DIM)))
            .block(block(title)),
        area,
    );
}

fn elapsed(time: Duration) -> String {
    format!("{:02}:{:02}", time.as_secs() / 60, time.as_secs() % 60)
}
