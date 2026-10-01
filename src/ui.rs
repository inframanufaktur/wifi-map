//! Pure terminal rendering, using the sibling usmaybe dashboard palette.
mod diagnostics;

use ratatui::{
    Frame,
    layout::{Constraint, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{
        Block, Borders, Cell, Clear, List, ListItem, ListState, Paragraph, Row, Sparkline,
        SparklineBar, Table, TableState, Wrap,
    },
};

use crate::{
    app::{EvalApp, Level, Picker, WalkApp},
    model::display,
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

fn ranking_table(frame: &mut Frame, app: &EvalApp, area: Rect) {
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

fn comparison_table(frame: &mut Frame, app: &EvalApp, area: Rect) {
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

fn reading_detail(frame: &mut Frame, app: &EvalApp) {
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

fn draw_picker(frame: &mut Frame, picker: &Picker, create: bool) {
    let area = popup(
        frame.area(),
        70,
        18.min(frame.area().height.saturating_sub(4)),
    );
    frame.render_widget(Clear, area);
    let layout =
        Layout::vertical([Constraint::Min(3), Constraint::Length(2)]).split(block("").inner(area));
    frame.render_widget(block(format!(" {} ", picker.title)), area);
    let items: Vec<_> = picker
        .items
        .iter()
        .map(|choice| ListItem::new(choice.label.clone()))
        .collect();
    let mut state =
        ListState::default().with_selected((!items.is_empty()).then_some(picker.selected));
    frame.render_stateful_widget(
        List::new(items)
            .highlight_style(Style::default().fg(ACCENT).bg(PANEL))
            .highlight_symbol("▸ "),
        layout[0],
        &mut state,
    );
    if picker.items.is_empty() {
        frame.render_widget(
            Paragraph::new(if create {
                "No entries yet. Press n or + to create one."
            } else {
                "No available entries in this scope."
            })
            .style(Style::default().fg(DIM))
            .wrap(Wrap { trim: true }),
            layout[0],
        );
    }
    frame.render_widget(
        Paragraph::new(if create {
            "↑/↓ choose · Enter select · n / + create · Esc cancel"
        } else {
            "↑/↓ choose · Enter select · Esc cancel"
        })
        .style(Style::default().fg(DIM)),
        layout[1],
    );
}

fn help(frame: &mut Frame, evaluation: bool) {
    let area = popup(frame.area(), 78, 24);
    frame.render_widget(Clear, area);
    let content = if evaluation {
        "↑/↓ or j/k     Select a ranked row\nTab / Shift-Tab Change ranking metric\nr              Reverse best / worst order\nm              Toggle spot medians / individual readings\nl / w          Filter by location / SSID\nc              Choose before and after named walks\nf              Include spots missing from one walk\nx              Swap before and after\nEnter          Inspect readings, signal, path and metadata\n←/→ in detail  Switch before / after readings\nEsc            Close details or return from comparison\nq / Ctrl-C     Quit\n\nMissing metrics are shown as — and rank last.\nDemo data is simulated and stored only in memory."
    } else {
        "↑/↓ or j/k     Select a survey spot\nl / r / p      Choose location / room / spot\nw              Choose a network / SSID\nn or +         Create a place (also inside pickers)\ns              Save signal, path and optional speed snapshot\nt              Run a speed probe with loaded latency\nd              Open connection diagnostics / roaming timeline\nTab in diagnostics Switch events / samples; arrows scroll\nb              Capture an ideal benchmark for this location\nc              Compare this spot with a named prior walk\na              Name the connected access point\nEnter / Esc    Save / cancel an editor or picker\nCtrl-U         Clear editor text\nq / Ctrl-C     Finish the named walk and quit\n\nMeasurements run in the background; incomplete work is\ncancelled when quitting. Demo data stays in memory."
    };
    let mut lines: Vec<_> = content
        .lines()
        .map(|line| Line::from(line.to_owned()))
        .collect();
    lines.extend([
        Line::from(""),
        Line::from(vec![
            Span::raw("Signal: "),
            Span::styled("strong", Style::default().fg(GOOD)),
            Span::raw(" · "),
            Span::styled("fair", Style::default().fg(WARN)),
            Span::raw(" · "),
            Span::styled("weak", Style::default().fg(BAD)),
            Span::raw(" · "),
            Span::styled("missing", Style::default().fg(DIM)),
        ]),
        Line::from(vec![
            Span::raw("Changes: "),
            Span::styled("improved", Style::default().fg(GOOD)),
            Span::raw(" · "),
            Span::styled("worse", Style::default().fg(BAD)),
            Span::raw(" · "),
            Span::styled("unchanged / missing", Style::default().fg(DIM)),
        ]),
    ]);
    frame.render_widget(
        Paragraph::new(lines)
            .block(block(" Keyboard help · ? / Esc closes "))
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

#[cfg(test)]
mod tests {
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
}
