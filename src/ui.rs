//! Shared terminal palette and rendering helpers.
mod diagnostics;
mod evaluation;
mod overlays;
mod reading;
#[cfg(test)]
mod tests;
mod walk;

pub use evaluation::draw_eval;
pub use walk::draw_walk;

use ratatui::{
    Frame,
    layout::{Constraint, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Paragraph, SparklineBar, Wrap},
};

use overlays::help;
#[cfg(test)]
use {
    crate::app::WalkApp,
    crate::model::display,
    evaluation::{comparison_table, ranking_table},
    ratatui::widgets::Sparkline,
    reading::reading_detail,
};

const ACCENT: Color = Color::Rgb(74, 222, 203);
const DIM: Color = Color::Rgb(139, 153, 170);
const BG: Color = Color::Rgb(15, 21, 30);
const PANEL: Color = Color::Rgb(23, 32, 44);
const WARN: Color = Color::Rgb(250, 196, 96);
const GOOD: Color = Color::Rgb(74, 222, 128);
const BAD: Color = Color::Rgb(248, 113, 113);

fn block(title: impl Into<Line<'static>>) -> Block<'static> {
    Block::default()
        .title(title)
        .borders(Borders::ALL)
        .border_style(Style::default().fg(DIM))
        .style(Style::default().bg(BG).fg(Color::White))
}

fn base(frame: &mut Frame, label: &str, demo: bool, error: bool, live: bool) -> Option<Vec<Rect>> {
    let area = frame.area();
    frame.render_widget(
        Block::default().style(Style::default().bg(BG).fg(Color::White)),
        area,
    );
    if area.width < 76 || area.height < 24 {
        frame.render_widget(Paragraph::new(format!("WIFIMAP · {label}\n\nResize terminal to at least 76 × 24.\nq quits · ? opens help{}", if demo { "\nDEMO / SIMULATED · in-memory data" } else { "" }))
            .style(Style::default().fg(ACCENT)).wrap(Wrap { trim: true }),area);
        return None;
    }
    let regions = Layout::vertical([
        Constraint::Length(3),
        Constraint::Length(2),
        Constraint::Min(7),
        Constraint::Length(8),
        Constraint::Length(3),
    ])
    .split(area);
    frame.render_widget(
        Paragraph::new(Line::from(vec![
            Span::styled(
                " WIFIMAP ",
                Style::default().fg(ACCENT).add_modifier(Modifier::BOLD),
            ),
            Span::raw(label.to_string()),
            Span::styled(
                format!(
                    "    {}",
                    if demo {
                        "DEMO / SIMULATED"
                    } else if error {
                        "WIFI UNAVAILABLE"
                    } else if !live {
                        "SAVED / REPORT"
                    } else {
                        "LIVE"
                    }
                ),
                Style::default().fg(if demo {
                    WARN
                } else if error {
                    BAD
                } else {
                    ACCENT
                }),
            ),
        ]))
        .block(block("")),
        regions[0],
    );
    Some(regions.to_vec())
}

fn footer(frame: &mut Frame, message: &str, keys: &str, area: Rect) {
    frame.render_widget(
        Paragraph::new(vec![
            Line::styled(format!(" {message}"), Style::default().fg(DIM)),
            Line::styled(format!(" {keys}"), Style::default().fg(ACCENT)),
        ])
        .wrap(Wrap { trim: true }),
        area,
    );
}

fn popup(area: Rect, width: u16, height: u16) -> Rect {
    let width = width.min(area.width.saturating_sub(2));
    let height = height.min(area.height.saturating_sub(2));
    Rect::new(
        area.x + (area.width - width) / 2,
        area.y + (area.height - height) / 2,
        width,
        height,
    )
}

fn metric_color(key: &str, value: Option<f64>) -> Color {
    let Some(value) = value else {
        return DIM;
    };
    match key {
        "rssi" => quality_color(value, -60.0, -70.0),
        "snr" => quality_color(value, 25.0, 15.0),
        "gateway_loss_pct" | "internet_loss_pct" if value > 0.0 => WARN,
        key if key.starts_with("delta_") => change_color(Some(value), true),
        _ => ACCENT,
    }
}

fn quality_color(value: f64, good_min: f64, fair_min: f64) -> Color {
    if value >= good_min {
        GOOD
    } else if value >= fair_min {
        WARN
    } else {
        BAD
    }
}

// Color follows the displayed precision: +0.0 is unchanged, including -0.04.
fn change_color(value: Option<f64>, higher_is_better: bool) -> Color {
    match value {
        None => DIM,
        Some(value) if value.abs() < 0.05 => DIM,
        Some(value) if (value > 0.0) == higher_is_better => GOOD,
        Some(_) => BAD,
    }
}

fn metric_value(key: &str, value: Option<f64>) -> Span<'static> {
    Span::styled(number(value), Style::default().fg(metric_color(key, value)))
}

fn change_value(value: Option<f64>, higher_is_better: bool) -> Span<'static> {
    Span::styled(
        signed(value),
        Style::default().fg(change_color(value, higher_is_better)),
    )
}

fn support_color(confidence: &str) -> Color {
    if confidence == "limited" { WARN } else { DIM }
}

fn radio_summary(rssi: Option<f64>, noise: Option<f64>, snr: Option<f64>) -> Line<'static> {
    Line::from(vec![
        Span::raw("RSSI "),
        metric_value("rssi", rssi),
        Span::raw(" dBm · noise "),
        metric_value("noise", noise),
        Span::raw(" dBm · SNR "),
        metric_value("snr", snr),
        Span::raw(" dB"),
    ])
}

fn speed_summary(ping: Option<f64>, down: Option<f64>, up: Option<f64>) -> Line<'static> {
    Line::from(vec![
        Span::raw("Ping "),
        metric_value("ping_ms", ping),
        Span::raw(" ms · down "),
        metric_value("down_mbps", down),
        Span::raw(" Mbps · up "),
        metric_value("up_mbps", up),
        Span::raw(" Mbps"),
    ])
}

fn path_summary(rtt: Option<f64>, loss: Option<f64>) -> Line<'static> {
    // RTT is a window median. Missing RTT alone cannot establish a timeout.
    let color = if rtt.is_some() {
        GOOD
    } else if loss == Some(100.0) {
        BAD
    } else {
        DIM
    };
    let mut spans = vec![Span::styled(number(rtt), Style::default().fg(color))];
    spans.extend([
        Span::raw(" ms · loss "),
        Span::styled(
            number(loss),
            Style::default().fg(if loss.is_some_and(|v| v > 0.0) {
                WARN
            } else {
                DIM
            }),
        ),
        Span::raw("%"),
    ]);
    Line::from(spans)
}

fn labeled_path(
    label: &str,
    rtt: Option<f64>,
    p95: Option<f64>,
    loss: Option<f64>,
) -> Line<'static> {
    let mut line = path_summary(rtt, loss);
    line.spans.splice(
        1..1,
        [
            Span::raw(" / p95 "),
            Span::styled(number(p95), Style::default().fg(DIM)),
        ],
    );
    line.spans.insert(0, Span::raw(label.to_owned()));
    line
}

fn live_comparison(
    label: &str,
    key: &str,
    now: Option<f64>,
    before: Option<f64>,
    unit: &str,
) -> Line<'static> {
    Line::from(vec![
        Span::raw(format!("{label} now ")),
        metric_value(key, now),
        Span::raw(" / before "),
        metric_value(key, before),
        Span::raw(" / Δ "),
        change_value(now.zip(before).map(|(a, b)| a - b), true),
        Span::raw(format!(" {unit}")),
    ])
}

fn signal_history(
    history: &std::collections::VecDeque<Option<f64>>,
    area: Rect,
) -> Vec<SparklineBar> {
    let columns = history.len().min(area.width as usize);
    // Keep a visible sliver for every actual reading, even at or below -100 dBm.
    let minimum_height = 80_u64.div_ceil(8 * u64::from(area.height.max(1)));
    (0..columns)
        .map(|column| {
            let start = column * history.len() / columns;
            let end = (column + 1) * history.len() / columns;
            let values: Vec<_> = history.range(start..end).filter_map(|&v| v).collect();
            let average =
                (!values.is_empty()).then(|| values.iter().sum::<f64>() / values.len() as f64);
            SparklineBar::from(
                average.map(|v| ((v + 100.0).max(0.0) as u64).clamp(minimum_height, 80)),
            )
            .style(Style::default().fg(metric_color("rssi", average)))
        })
        .collect()
}

fn text(value: &Option<String>) -> String {
    value.clone().unwrap_or_else(|| "—".into())
}
fn number(value: Option<f64>) -> String {
    value
        .map(|v| format!("{v:.1}"))
        .unwrap_or_else(|| "—".into())
}
fn signed(value: Option<f64>) -> String {
    value
        .map(|v| format!("{:+.1}", if v.abs() < 0.05 { 0.0 } else { v }))
        .unwrap_or_else(|| "—".into())
}
fn fallback<'a>(value: &'a str, missing: &'a str) -> &'a str {
    if value.is_empty() { missing } else { value }
}
