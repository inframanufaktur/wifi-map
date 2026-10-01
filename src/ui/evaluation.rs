//! Evaluation dashboard, rankings and walk comparisons.
use ratatui::{
    Frame,
    layout::{Constraint, Layout, Rect},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Cell, Paragraph, Row, Table, TableState, Wrap},
};

use super::{
    ACCENT, DIM, PANEL, base, block, change_value, footer, help, metric_value,
    overlays::draw_picker, radio_summary, reading::reading_detail, speed_summary, support_color,
    text,
};
use crate::app::EvalApp;

pub fn draw_eval(frame: &mut Frame, app: &EvalApp) {
    let Some(regions) = base(frame, "Survey evaluation", app.demo, false, false) else {
        return;
    };
    let metric = app.metric();
    frame.render_widget(
        Paragraph::new(format!(
            " {} readings   ·   {}   ·   {}   ·   {} {}   ·   {}",
            app.readings.len(),
            app.scope.as_deref().unwrap_or("All places / networks"),
            if app.grouped {
                "spot medians"
            } else {
                "individual readings"
            },
            metric.label,
            metric.unit,
            if app.reverse {
                "best first"
            } else {
                "worst first"
            }
        ))
        .style(Style::default().fg(DIM)),
        regions[1],
    );
    let columns = Layout::horizontal([
        Constraint::Length(if frame.area().width < 100 { 20 } else { 26 }),
        Constraint::Min(50),
    ])
    .split(regions[2]);
    eval_sidebar(frame, app, columns[0]);
    if app.comparing() {
        comparison_table(frame, app, columns[1]);
    } else {
        ranking_table(frame, app, columns[1]);
    }
    eval_details(frame, app, regions[3]);
    footer(
        frame,
        &app.message,
        "↑/↓ row  Tab metric  r order  m group  l location  c compare  Enter detail  ?  q",
        regions[4],
    );
    if app.help {
        help(frame, true);
    }
    if let Some(picker) = &app.picker {
        draw_picker(frame, picker, false);
    }
    if app.detail {
        reading_detail(frame, app);
    }
}

fn eval_sidebar(frame: &mut Frame, app: &EvalApp, area: Rect) {
    let metric = app.metric();
    let mut lines = vec![
        Line::styled("Rank by", Style::default().fg(DIM)),
        Line::styled(
            format!("{} · {}", metric.label, metric.unit),
            Style::default().fg(ACCENT).add_modifier(Modifier::BOLD),
        ),
        Line::from(""),
        Line::styled("Scope", Style::default().fg(DIM)),
        Line::from(app.scope.clone().unwrap_or_else(|| "All".into())),
        Line::from(""),
        Line::styled("Grouping", Style::default().fg(DIM)),
        Line::from(if app.grouped {
            "Median per spot"
        } else {
            "Each reading"
        }),
        Line::from(""),
    ];
    if app.comparing() {
        lines.push(Line::styled("Walk comparison", Style::default().fg(ACCENT)));
        lines.push(Line::from(format!(
            "#{} → #{}",
            app.before.unwrap(),
            app.after.unwrap()
        )));
        lines.push(Line::from(if app.show_unmatched {
            "All spots (f)"
        } else {
            "Matched spots (f)"
        }));
        lines.push(Line::from("x swap · Esc return"));
    } else {
        lines.push(Line::styled("Source", Style::default().fg(DIM)));
        lines.push(Line::from(app.source.clone()));
    }
    frame.render_widget(
        Paragraph::new(lines)
            .block(block(" Analysis "))
            .wrap(Wrap { trim: true }),
        area,
    );
}

pub(super) fn ranking_table(frame: &mut Frame, app: &EvalApp, area: Rect) {
    let rows = app.rows.iter().enumerate().map(|(i, r)| {
        Row::new([
            Cell::from(format!("{}", i + 1)).style(Style::default().fg(DIM)),
            Cell::from(format!("{} / {}", r.reading.room, r.reading.spot)),
            Cell::from(metric_value(app.metric().key, r.metric(app.metric().key))),
            Cell::from(format!("{}/{}", r.available(app.metric().key), r.count))
                .style(Style::default().fg(support_color(r.confidence(app.metric().key)))),
            Cell::from(
                r.reading
                    .signal
                    .ssid
                    .clone()
                    .unwrap_or_else(|| "Unknown".into()),
            ),
        ])
    });
    let mut state =
        TableState::default().with_selected((!app.rows.is_empty()).then_some(app.selected));
    frame.render_stateful_widget(
        Table::new(
            rows,
            [
                Constraint::Length(4),
                Constraint::Min(16),
                Constraint::Length(10),
                Constraint::Length(5),
                Constraint::Length(16),
            ],
        )
        .header(
            Row::new(["#", "Room / spot", app.metric().label, "valid/n", "SSID"])
                .style(Style::default().fg(DIM)),
        )
        .block(block(" Ranked readings "))
        .row_highlight_style(Style::default().bg(PANEL).add_modifier(Modifier::BOLD))
        .highlight_symbol("▸ "),
        area,
        &mut state,
    );
    if app.rows.is_empty() {
        frame.render_widget(Paragraph::new("No readings in this scope.\n\nCapture snapshots in walk mode, or open eval --demo to explore.").style(Style::default().fg(DIM)).wrap(Wrap { trim: true }),block("").inner(area));
    }
}

pub(super) fn comparison_table(frame: &mut Frame, app: &EvalApp, area: Rect) {
    let metric = app.metric();
    let rows = app.comparisons.iter().map(|r| {
        let before = r.before.as_ref().and_then(|r| r.metric(metric.key));
        let after = r.after.as_ref().and_then(|r| r.metric(metric.key));
        let delta = after.zip(before).map(|(a, b)| a - b);
        let place = r.after.as_ref().or(r.before.as_ref());
        let name = place
            .map(|p| format!("{} / {}", p.reading.room, p.reading.spot))
            .unwrap_or_default();
        Row::new([
            Cell::from(name),
            Cell::from(metric_value(metric.key, before)),
            Cell::from(metric_value(metric.key, after)),
            Cell::from(change_value(delta, metric.higher_is_better)),
            Cell::from(r.status.clone()).style(Style::default().fg(DIM)),
        ])
    });
    let mut state =
        TableState::default().with_selected((!app.comparisons.is_empty()).then_some(app.selected));
    frame.render_stateful_widget(
        Table::new(
            rows,
            [
                Constraint::Min(16),
                Constraint::Length(9),
                Constraint::Length(9),
                Constraint::Length(9),
                Constraint::Length(12),
            ],
        )
        .header(
            Row::new(["Room / spot", "Before", "After", "Change", "Coverage"])
                .style(Style::default().fg(DIM)),
        )
        .block(block(format!(
            " Walk comparison · {} {} ",
            metric.label, metric.unit
        )))
        .row_highlight_style(Style::default().bg(PANEL).add_modifier(Modifier::BOLD))
        .highlight_symbol("▸ "),
        area,
        &mut state,
    );
    if app.comparisons.is_empty() {
        frame.render_widget(
            Paragraph::new(
                "No matched spots. Press f to include spots captured in only one survey.",
            )
            .style(Style::default().fg(DIM))
            .wrap(Wrap { trim: true }),
            block("").inner(area),
        );
    }
}

fn eval_details(frame: &mut Frame, app: &EvalApp, area: Rect) {
    let row = if app.comparing() {
        app.comparisons
            .get(app.selected)
            .and_then(|r| r.after.as_ref().or(r.before.as_ref()))
    } else {
        app.rows.get(app.selected)
    };
    let lines = if let Some(row) = row {
        let r = &row.reading;
        let mut radio = radio_summary(row.metric("rssi"), row.metric("noise"), row.metric("snr"));
        radio.spans.extend([
            Span::raw(" · TX "),
            metric_value("tx_rate", row.metric("tx_rate")),
            Span::raw(" Mbps"),
        ]);
        vec![
            Line::styled(
                format!(
                    "{} / {} / {} · {} measurements",
                    r.location, r.room, r.spot, row.count
                ),
                Style::default().fg(ACCENT),
            ),
            radio,
            speed_summary(
                row.metric("ping_ms"),
                row.metric("down_mbps"),
                row.metric("up_mbps"),
            ),
            Line::from(format!(
                "AP {} · {} · channel {} · {}",
                text(&r.ap_name),
                text(&r.signal.bssid),
                text(&r.signal.channel),
                text(&r.signal.phy)
            )),
            Line::styled(
                format!(
                    "Latest {} · {} · Enter to inspect each reading",
                    row.latest_ts,
                    text(&r.speed.server)
                ),
                Style::default().fg(DIM),
            ),
        ]
    } else {
        vec![Line::styled(
            "Select a reading to see diagnostics.",
            Style::default().fg(DIM),
        )]
    };
    let mut lines = lines;
    if let Some(row) = row {
        let metric = app.metric();
        let support = if app.comparing() {
            app.comparisons
                .get(app.selected)
                .map(|comparison| {
                    let before = comparison
                        .before
                        .as_ref()
                        .map(|r| format!("{}/{}", r.available(metric.key), r.count))
                        .unwrap_or_else(|| "0/0".into());
                    let after = comparison
                        .after
                        .as_ref()
                        .map(|r| format!("{}/{}", r.available(metric.key), r.count))
                        .unwrap_or_else(|| "0/0".into());
                    format!(
                        "{} samples: before {before} · after {after} · {} support",
                        metric.label,
                        comparison.confidence(metric.key)
                    )
                })
                .unwrap_or_default()
        } else {
            format!(
                "{} samples: {}/{} · {} support",
                metric.label,
                row.available(metric.key),
                row.count,
                row.confidence(metric.key)
            )
        };
        let confidence = if app.comparing() {
            app.comparisons[app.selected].confidence(metric.key)
        } else {
            row.confidence(metric.key)
        };
        lines.push(Line::styled(
            support,
            Style::default().fg(support_color(confidence)),
        ));
    }
    frame.render_widget(
        Paragraph::new(lines)
            .block(block(" Selected place "))
            .wrap(Wrap { trim: true }),
        area,
    );
}
