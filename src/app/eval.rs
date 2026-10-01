//! Saved-reading evaluation state and keyboard controls.
use std::collections::BTreeMap;

use anyhow::Result;
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use crate::{
    evaluation::{self, AnalysisRow, ComparisonRow},
    model::Reading,
};

use super::{Choice, Level, Picker, move_cursor};

pub struct EvalApp {
    pub demo: bool,
    pub source: String,
    pub readings: Vec<Reading>,
    pub rows: Vec<AnalysisRow>,
    pub comparisons: Vec<ComparisonRow>,
    pub metric_index: usize,
    pub grouped: bool,
    pub reverse: bool,
    pub scope_kind: Level,
    pub scope: Option<String>,
    pub scope_missing_ssid: bool,
    pub selected: usize,
    pub message: String,
    pub help: bool,
    pub picker: Option<Picker>,
    pub detail: bool,
    pub detail_page: usize,
    pub detail_side_after: bool,
    pub detail_index: usize,
    pub before: Option<i64>,
    pub after: Option<i64>,
    pub show_unmatched: bool,
    pub(super) pending_scope_kind: Option<Level>,
}

impl EvalApp {
    pub(crate) fn new(readings: Vec<Reading>, source: String, demo: bool) -> Result<Self> {
        let mut app = Self {
            demo,
            source,
            readings,
            rows: vec![],
            comparisons: vec![],
            metric_index: 0,
            grouped: true,
            reverse: false,
            scope_kind: Level::Location,
            scope: None,
            scope_missing_ssid: false,
            selected: 0,
            message: "Tab metric · m group · l location · c compare walks · Enter details".into(),
            help: false,
            picker: None,
            detail: false,
            detail_page: 0,
            detail_side_after: false,
            detail_index: 0,
            before: None,
            after: None,
            show_unmatched: false,
            pending_scope_kind: None,
        };
        app.refresh()?;
        Ok(app)
    }
    pub fn metric(&self) -> evaluation::MetricSpec {
        if self.comparing() {
            *evaluation::metric_spec(evaluation::COMPARISON_METRICS[self.metric_index])
                .expect("canonical metric")
        } else {
            evaluation::METRICS[self.metric_index]
        }
    }
    pub(super) fn scoped(&self) -> Vec<Reading> {
        self.readings
            .iter()
            .filter(|r| {
                if self.scope_kind == Level::Ssid && self.scope_missing_ssid {
                    return r.signal.ssid.is_none();
                }
                self.scope
                    .as_ref()
                    .is_none_or(|scope| match self.scope_kind {
                        Level::Ssid => r.signal.ssid.as_ref() == Some(scope),
                        _ => &r.location == scope,
                    })
            })
            .cloned()
            .collect()
    }
    pub(super) fn refresh(&mut self) -> Result<()> {
        let readings = self.scoped();
        if let (Some(before), Some(after)) = (self.before, self.after) {
            self.comparisons.clear();
            self.comparisons = evaluation::rank_comparison_rows(
                &evaluation::compare_walks(&readings, before, after)?,
                self.metric().key,
                self.reverse,
            )?;
            if !self.show_unmatched {
                self.comparisons.retain(|r| r.status == "matched");
            }
        } else {
            self.rows = evaluation::rank_rows(
                &evaluation::analysis_rows(&readings, self.grouped),
                self.metric().key,
                self.reverse,
            )?;
        }
        self.selected = self.selected.min(self.row_count().saturating_sub(1));
        Ok(())
    }
    pub fn comparing(&self) -> bool {
        self.before.is_some() && self.after.is_some()
    }
    pub fn row_count(&self) -> usize {
        if self.comparing() {
            self.comparisons.len()
        } else {
            self.rows.len()
        }
    }
    pub fn selected_readings(&self) -> &[Reading] {
        if self.comparing() {
            self.comparisons
                .get(self.selected)
                .and_then(|r| {
                    if self.detail_side_after {
                        r.after.as_ref()
                    } else {
                        r.before.as_ref()
                    }
                })
                .map(|r| r.readings.as_slice())
                .unwrap_or(&[])
        } else {
            self.rows
                .get(self.selected)
                .map(|r| r.readings.as_slice())
                .unwrap_or(&[])
        }
    }
    pub(super) fn open_scope(&mut self, kind: Level) {
        self.pending_scope_kind = Some(kind);
        let names: BTreeMap<_, _> = self
            .readings
            .iter()
            .filter(|r| kind != Level::Ssid || r.signal.ssid.is_some())
            .map(|r| {
                let name = if kind == Level::Ssid {
                    r.signal.ssid.clone().unwrap_or_else(|| "Unknown".into())
                } else {
                    r.location.clone()
                };
                (name, ())
            })
            .collect();
        let mut choices = vec![Choice {
            id: 0,
            label: "All".into(),
        }];
        if kind == Level::Ssid && self.readings.iter().any(|r| r.signal.ssid.is_none()) {
            choices.push(Choice {
                id: -1,
                label: "(SSID unavailable)".into(),
            });
        }
        choices.extend(names.keys().enumerate().map(|(i, name)| Choice {
            id: i as i64 + 1,
            label: name.clone(),
        }));
        self.picker = Some(Picker::new(
            Level::Scope,
            if kind == Level::Ssid {
                "Filter by SSID"
            } else {
                "Filter by location"
            },
            choices,
        ));
    }
    fn open_walks(&mut self, after: bool) {
        let mut walks = BTreeMap::new();
        for r in self.scoped() {
            if let Some(id) = r.walk_id {
                if Some(id) == self.before && after {
                    continue;
                }
                if after
                    && self
                        .readings
                        .iter()
                        .find(|r| r.walk_id == self.before)
                        .is_some_and(|b| b.location != r.location)
                {
                    continue;
                }
                walks.insert(
                    id,
                    format!(
                        "{} · {} · #{}",
                        r.walk_name.as_deref().unwrap_or("Unnamed walk"),
                        r.location,
                        id
                    ),
                );
            }
        }
        self.picker = Some(Picker::new(
            if after { Level::After } else { Level::Before },
            if after {
                "Compare: after walk"
            } else {
                "Compare: before walk"
            },
            walks
                .into_iter()
                .map(|(id, label)| Choice { id, label })
                .collect(),
        ));
    }
    pub(super) fn press(&mut self, key: KeyEvent) -> bool {
        if matches!(key.code, KeyCode::Char('q') | KeyCode::Char('Q'))
            || key.code == KeyCode::Char('c') && key.modifiers.contains(KeyModifiers::CONTROL)
        {
            return true;
        }
        if self.help {
            if matches!(key.code, KeyCode::Esc | KeyCode::Char('?')) {
                self.help = false;
            }
            return false;
        }
        if let Some(mut picker) = self.picker.take() {
            match key.code {
                KeyCode::Esc => {
                    self.pending_scope_kind = None;
                }
                KeyCode::Enter => {
                    if let Some(choice) = picker.items.get(picker.selected) {
                        match picker.level {
                            Level::Scope => {
                                self.scope_kind =
                                    self.pending_scope_kind.take().unwrap_or(self.scope_kind);
                                self.scope_missing_ssid = choice.id == -1;
                                self.scope = (choice.id != 0).then(|| choice.label.clone());
                                self.before = None;
                                self.after = None;
                            }
                            Level::Before => {
                                self.before = Some(choice.id);
                                self.after = None;
                                self.open_walks(true);
                            }
                            Level::After => {
                                self.after = Some(choice.id);
                                self.metric_index = 0;
                            }
                            _ => {}
                        }
                        self.selected = 0;
                        if let Err(error) = self.refresh() {
                            self.message = error.to_string();
                        }
                    }
                }
                _ => {
                    picker.press(key.code);
                    self.picker = Some(picker);
                }
            }
            return false;
        }
        if self.detail {
            match key.code {
                KeyCode::Esc => self.detail = false,
                KeyCode::Tab => self.detail_page = (self.detail_page + 1) % 3,
                KeyCode::BackTab => self.detail_page = (self.detail_page + 2) % 3,
                KeyCode::Left => {
                    self.detail_side_after = false;
                    self.detail_index = 0;
                }
                KeyCode::Right => {
                    self.detail_side_after = true;
                    self.detail_index = 0;
                }
                code => {
                    self.detail_index =
                        move_cursor(self.detail_index, self.selected_readings().len(), code)
                }
            }
            return false;
        }
        let mut refresh = false;
        match key.code {
            KeyCode::Char('?') => self.help = true,
            KeyCode::Char('l') => self.open_scope(Level::Location),
            KeyCode::Char('w') => self.open_scope(Level::Ssid),
            KeyCode::Char('c') => self.open_walks(false),
            KeyCode::Char('r') => {
                self.reverse = !self.reverse;
                refresh = true;
            }
            KeyCode::Char('m') => {
                self.grouped = !self.grouped;
                refresh = true;
            }
            KeyCode::Char('f') => {
                self.show_unmatched = !self.show_unmatched;
                refresh = true;
            }
            KeyCode::Char('x') if self.comparing() => {
                std::mem::swap(&mut self.before, &mut self.after);
                refresh = true;
            }
            KeyCode::Esc => {
                self.before = None;
                self.after = None;
                refresh = true;
            }
            KeyCode::Tab | KeyCode::BackTab => {
                let count = if self.comparing() {
                    evaluation::COMPARISON_METRICS.len()
                } else {
                    evaluation::METRICS.len()
                };
                self.metric_index = (self.metric_index
                    + if key.code == KeyCode::Tab {
                        1
                    } else {
                        count - 1
                    })
                    % count;
                refresh = true;
            }
            KeyCode::Enter if self.row_count() > 0 => {
                self.detail = true;
                self.detail_index = 0;
                self.detail_page = 0;
                self.detail_side_after = false;
            }
            code => self.selected = move_cursor(self.selected, self.row_count(), code),
        }
        if refresh {
            self.selected = 0;
            if let Err(error) = self.refresh() {
                self.message = error.to_string();
            }
        }
        false
    }
}
