//! Shared place picker and keyboard help overlays.
use ratatui::{
    Frame,
    layout::{Constraint, Layout},
    style::Style,
    text::{Line, Span},
    widgets::{Clear, List, ListItem, ListState, Paragraph, Wrap},
};

use super::{ACCENT, BAD, DIM, GOOD, PANEL, WARN, block, popup};
use crate::app::Picker;

pub(super) fn draw_picker(frame: &mut Frame, picker: &Picker, create: bool) {
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

pub(super) fn help(frame: &mut Frame, evaluation: bool) {
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
