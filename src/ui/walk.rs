//! Live survey dashboard and reference comparison.
use ratatui::{
    Frame,
    layout::{Constraint, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{
        Cell, Clear, List, ListItem, ListState, Paragraph, RenderDirection, Row, Sparkline, Table,
        Wrap,
    },
};

use super::{
    ACCENT, DIM, PANEL, WARN, base, block, diagnostics, fallback, footer, help, live_comparison,
    metric_color, metric_value, overlays::draw_picker, path_summary, popup, signal_history, text,
};
use crate::{
    app::{Level, WalkApp},
    model::display,
};

pub fn draw_walk(frame: &mut Frame, app: &WalkApp) {
    if app.diagnostics_open {
        diagnostics::draw(frame, app);
        return;
    }
    let Some(regions) = base(frame, "WiFi survey", app.demo, app.sample_error, true) else {
        return;
    };
    frame.render_widget(
        Paragraph::new(format!(
            " {} / {} / {}   ·   {}   ·   {} saved   ·   {} running",
            fallback(&app.active.location, "Choose location"),
            fallback(&app.active.room, "Choose room"),
            fallback(&app.active.spot, "Choose spot"),
            fallback(&app.walk_name, "No active walk"),
            app.saved,
            app.pending
        ))
        .style(Style::default().fg(DIM)),
        regions[1],
    );
    let columns = Layout::horizontal([
        Constraint::Length(if frame.area().width < 100 { 22 } else { 28 }),
        Constraint::Min(48),
    ])
    .split(regions[2]);
    walk_sidebar(frame, app, columns[0]);
    let content = Layout::vertical([Constraint::Length(5), Constraint::Min(3)]).split(columns[1]);
    let cards = Layout::horizontal([
        Constraint::Fill(1),
        Constraint::Fill(1),
        Constraint::Length((content[0].width / 3).max(20)),
    ])
    .split(content[0]);
    metric_card(
        frame,
        " Signal ",
        format!("{} dBm", display(app.signal.rssi)),
        app.signal
            .rssi
            .map(|n| {
                if n >= -60.0 {
                    "Strong"
                } else if n >= -70.0 {
                    "Fair"
                } else {
                    "Weak"
                }
            })
            .unwrap_or("Unavailable"),
        metric_color("rssi", app.signal.rssi),
        cards[0],
    );
    metric_card(
        frame,
        " Margin ",
        format!("{} dB", display(app.signal.snr)),
        app.signal
            .snr
            .map(|n| {
                if n >= 25.0 {
                    "Healthy SNR"
                } else if n >= 15.0 {
                    "Fair SNR"
                } else {
                    "Low SNR"
                }
            })
            .unwrap_or("Unavailable"),
        metric_color("snr", app.signal.snr),
        cards[1],
    );
    bandwidth_card(frame, app, cards[2]);
    let rows = [
        (
            "Network",
            Line::from(
                app.signal
                    .ssid
                    .clone()
                    .unwrap_or_else(|| "SSID unavailable".into()),
            ),
        ),
        (
            "Access point",
            Line::from(
                app.active
                    .ap_name
                    .clone()
                    .unwrap_or_else(|| "a to name".into()),
            ),
        ),
        (
            "BSSID",
            Line::from(app.signal.bssid.clone().unwrap_or_else(|| "—".into())),
        ),
        (
            "Link",
            Line::from(format!(
                "ch {} · {} · TX {} Mbps",
                text(&app.signal.channel),
                text(&app.signal.phy),
                text(&app.signal.tx_rate)
            )),
        ),
        (
            "Radio",
            Line::from(format!(
                "{} · MCS {} · {}",
                text(&app.signal.band),
                display(app.signal.mcs),
                text(&app.signal.security)
            )),
        ),
        (
            "Traffic",
            Line::from(
                app.rates
                    .map(|(down, up)| format!("↓ {down:.1} / ↑ {up:.1} Mbps · interface traffic"))
                    .unwrap_or_else(|| "Unavailable".into()),
            ),
        ),
        (
            "Gateway",
            path_summary(app.path.gateway_rtt_ms, app.path.gateway_loss_pct),
        ),
        (
            "Internet",
            path_summary(app.path.internet_rtt_ms, app.path.internet_loss_pct),
        ),
    ];
    frame.render_widget(
        Table::new(
            rows.into_iter().map(|(key, value)| {
                Row::new([
                    Cell::from(key).style(Style::default().fg(DIM)),
                    Cell::from(value),
                ])
            }),
            [Constraint::Length(14), Constraint::Min(28)],
        )
        .block(block(" Live diagnostics ")),
        content[1],
    );
    walk_details(frame, app, regions[3]);
    footer(
        frame,
        &app.message,
        "↑/↓ spot  l location  r room  p spot  s save  t speed  d diagnostics  ?  q",
        regions[4],
    );
    if app.help {
        help(frame, false);
    }
    if let Some(picker) = &app.picker {
        draw_picker(
            frame,
            picker,
            !matches!(picker.level, Level::Baseline | Level::BenchmarkConfirmation),
        );
    }
    if let Some(editor) = &app.editor {
        let area = popup(frame.area(), 64, 7);
        frame.render_widget(Clear, area);
        frame.render_widget(
            Paragraph::new(vec![
                Line::styled(editor.text.clone(), Style::default().fg(ACCENT)),
                Line::from(""),
                Line::styled(
                    "Enter save · Esc cancel · Ctrl-U clear",
                    Style::default().fg(DIM),
                ),
            ])
            .block(block(format!(" {} ", editor.title))),
            area,
        );
        let x = area.x + 1 + (editor.text.chars().count() as u16).min(area.width.saturating_sub(3));
        frame.set_cursor_position((x, area.y + 1));
    }
}

fn walk_sidebar(frame: &mut Frame, app: &WalkApp, area: Rect) {
    let items: Vec<_> = app
        .places
        .iter()
        .map(|p| {
            ListItem::new(vec![
                Line::styled(p.room.clone(), Style::default().fg(DIM)),
                Line::from(p.spot.clone()),
                Line::from(""),
            ])
        })
        .collect();
    let mut state = ListState::default().with_selected((!items.is_empty()).then_some(app.selected));
    frame.render_stateful_widget(
        List::new(items)
            .block(block(" Survey spots "))
            .highlight_style(
                Style::default()
                    .bg(PANEL)
                    .fg(ACCENT)
                    .add_modifier(Modifier::BOLD),
            )
            .highlight_symbol("▸ "),
        area,
        &mut state,
    );
    if app.places.is_empty() {
        frame.render_widget(
            Paragraph::new(
                "No survey spots.\n\nl choose location\nr choose room\nn create a place",
            )
            .style(Style::default().fg(DIM))
            .wrap(Wrap { trim: true }),
            block("").inner(area),
        );
    }
}

fn walk_details(frame: &mut Frame, app: &WalkApp, area: Rect) {
    let columns =
        Layout::horizontal([Constraint::Percentage(42), Constraint::Percentage(58)]).split(area);
    let graph = Layout::vertical([Constraint::Min(3), Constraint::Length(1)]).split(columns[0]);
    let data = signal_history(&app.history, block("").inner(graph[0]));
    frame.render_widget(
        Sparkline::default()
            .block(block(" Signal history · last 60 polls "))
            .data(data)
            .max(80)
            .direction(RenderDirection::RightToLeft)
            .absent_value_style(Style::default().fg(DIM))
            .style(Style::default().fg(ACCENT)),
        graph[0],
    );
    frame.render_widget(
        Paragraph::new("  −100 dBm to −20 dBm · gaps = unavailable")
            .style(Style::default().fg(DIM)),
        graph[1],
    );
    let mut lines = Vec::new();
    if let Some(name) = &app.baseline_name {
        lines.push(Line::styled(
            format!("Before: {name}"),
            Style::default().fg(ACCENT),
        ));
        if let Some(before) = &app.baseline {
            lines.push(live_comparison(
                "RSSI",
                "rssi",
                app.signal.rssi,
                before.signal.rssi,
                "dB",
            ));
            lines.push(live_comparison(
                "SNR ",
                "snr",
                app.signal.snr,
                before.signal.snr,
                "dB",
            ));
            lines.push(live_comparison(
                "Down",
                "down_mbps",
                app.speed.down_mbps,
                before.speed.down_mbps,
                "Mbps",
            ));
            lines.push(Line::styled(
                format!(
                    "Test server: now {} · before {}",
                    text(&app.speed.server),
                    text(&before.speed.server)
                ),
                Style::default().fg(DIM),
            ));
        } else {
            lines.push(Line::styled(
                "No baseline captured at this spot.",
                Style::default().fg(WARN),
            ));
        }
    } else if let Some(ideal) = &app.benchmark {
        lines.push(Line::styled("Ideal benchmark", Style::default().fg(ACCENT)));
        lines.push(Line::from(vec![
            Span::raw("RSSI "),
            metric_value("rssi", ideal.signal.rssi),
            Span::raw(" dBm · SNR "),
            metric_value("snr", ideal.signal.snr),
            Span::raw(" dB"),
        ]));
        lines.push(Line::from(vec![
            Span::raw("Down "),
            metric_value("down_mbps", ideal.speed.down_mbps),
            Span::raw(" / up "),
            metric_value("up_mbps", ideal.speed.up_mbps),
            Span::raw(" Mbps"),
        ]));
        lines.push(Line::styled(
            format!("{} · {}", ideal.ts, text(&ideal.speed.server)),
            Style::default().fg(DIM),
        ));
    } else {
        lines.push(Line::styled(
            "Capture a reference",
            Style::default().fg(ACCENT),
        ));
        lines.push(Line::from("b saves an ideal benchmark at this location."));
        lines.push(Line::from("c compares with a named prior survey."));
        lines.push(Line::from(
            "s saves signal, path and optional speed metrics.",
        ));
    }
    frame.render_widget(
        Paragraph::new(lines)
            .block(block(" Reference / comparison "))
            .wrap(Wrap { trim: true }),
        columns[1],
    );
}

fn bandwidth_card(frame: &mut Frame, app: &WalkApp, area: Rect) {
    let mut lines: Vec<_> = [
        ("Down ", "down_mbps", app.speed.down_mbps),
        ("Up   ", "up_mbps", app.speed.up_mbps),
    ]
    .into_iter()
    .map(|(label, key, value)| {
        Line::from(vec![
            Span::styled(label, Style::default().fg(DIM)),
            metric_value(key, value),
            Span::styled(" Mbps", Style::default().fg(DIM)),
        ])
        .style(Style::default().add_modifier(Modifier::BOLD))
    })
    .collect();
    lines.push(Line::styled(
        if app.pending > 0 {
            "Measuring…"
        } else {
            "Press t to probe"
        },
        Style::default().fg(DIM),
    ));
    frame.render_widget(Paragraph::new(lines).block(block(" Bandwidth ")), area);
}

fn metric_card(
    frame: &mut Frame,
    title: &str,
    value: String,
    note: &str,
    color: Color,
    area: Rect,
) {
    frame.render_widget(
        Paragraph::new(vec![
            Line::styled(
                value,
                Style::default().fg(color).add_modifier(Modifier::BOLD),
            ),
            Line::styled(note.to_string(), Style::default().fg(color)),
        ])
        .block(block(title.to_string())),
        area,
    );
}
