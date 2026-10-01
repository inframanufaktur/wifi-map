//! Saved reading detail pages.
use ratatui::{
    Frame,
    style::Style,
    text::{Line, Span},
    widgets::{Clear, Paragraph, Wrap},
};

use super::{
    ACCENT, DIM, WARN, block, change_value, labeled_path, popup, radio_summary, speed_summary, text,
};
use crate::{app::EvalApp, model::display};

pub(super) fn reading_detail(frame: &mut Frame, app: &EvalApp) {
    let readings = app.selected_readings();
    let area = popup(frame.area(), 92, 19);
    frame.render_widget(Clear, area);
    let mut lines = vec![
        Line::styled(
            format!(
                "Reading {} / {} · {} · page {} / 3",
                app.detail_index + usize::from(!readings.is_empty()),
                readings.len(),
                if app.detail_side_after && app.comparing() {
                    "after"
                } else {
                    "before / selected"
                },
                app.detail_page + 1
            ),
            Style::default().fg(ACCENT),
        ),
        Line::from(""),
    ];
    if let Some(r) = readings.get(app.detail_index) {
        lines.push(Line::from(format!(
            "#{} · {} · {} / {} / {}",
            r.id, r.ts, r.location, r.room, r.spot
        )));
        match app.detail_page {
            0 => {
                lines.extend([
                    Line::from(format!(
                        "SSID {} · AP {} · BSSID {}",
                        text(&r.signal.ssid),
                        text(&r.ap_name),
                        text(&r.signal.bssid)
                    )),
                    radio_summary(r.signal.rssi, r.signal.noise, r.signal.snr),
                    Line::from(format!(
                        "Channel {} · PHY {} · TX {} Mbps",
                        text(&r.signal.channel),
                        text(&r.signal.phy),
                        text(&r.signal.tx_rate)
                    )),
                    Line::from(format!(
                        "MCS {} · band {} · security {}",
                        display(r.signal.mcs),
                        text(&r.signal.band),
                        text(&r.signal.security)
                    )),
                    speed_summary(r.speed.ping_ms, r.speed.down_mbps, r.speed.up_mbps),
                    Line::from(format!("Speed server {}", text(&r.speed.server))),
                ]);
            }
            1 => lines.extend([
                Line::from(format!("Path probes {}", display(r.path.path_probe_count))),
                labeled_path(
                    "Gateway: RTT ",
                    r.path.gateway_rtt_ms,
                    r.path.gateway_p95_ms,
                    r.path.gateway_loss_pct,
                ),
                Line::from(format!(
                    "Gateway maximum outage {} ms",
                    display(r.path.gateway_max_outage_ms)
                )),
                labeled_path(
                    "Internet: RTT ",
                    r.path.internet_rtt_ms,
                    r.path.internet_p95_ms,
                    r.path.internet_loss_pct,
                ),
                Line::from(format!(
                    "Internet maximum outage {} ms",
                    display(r.path.internet_max_outage_ms)
                )),
            ]),
            _ => lines.extend([
                Line::from(format!(
                    "Walk {} · #{}",
                    text(&r.walk_name),
                    display(r.walk_id)
                )),
                Line::from(format!("Floor {} · outdoors {}", r.floor, r.outdoors)),
                Line::from(vec![
                    Span::raw("Benchmark Δ RSSI "),
                    change_value(r.delta_rssi, true),
                    Span::raw(" dB · SNR "),
                    change_value(r.delta_snr, true),
                    Span::raw(" dB"),
                ]),
                Line::from(vec![
                    Span::raw("Benchmark Δ down "),
                    change_value(r.delta_down_mbps, true),
                    Span::raw(" / up "),
                    change_value(r.delta_up_mbps, true),
                    Span::raw(" Mbps"),
                ]),
                Line::from(format!("Note {}", text(&r.note))),
            ]),
        }
    } else {
        lines.push(Line::styled(
            "No readings on this side. Use ← / → to switch walks.",
            Style::default().fg(WARN),
        ));
    }
    lines.push(Line::from(""));
    lines.push(Line::styled(
        "↑/↓ reading · Tab page · ←/→ comparison side · Esc return · q quit",
        Style::default().fg(DIM),
    ));
    frame.render_widget(
        Paragraph::new(lines)
            .block(block(" Reading details "))
            .wrap(Wrap { trim: true }),
        area,
    );
}
