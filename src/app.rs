//! Terminal application state. Measurement workers never own the database.
use std::{
    collections::{BTreeMap, VecDeque},
    io::{self, IsTerminal},
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
        mpsc::{self, Receiver, Sender},
    },
    thread,
    time::{Duration, Instant},
};

use anyhow::{Result, bail};
use crossterm::{
    event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers},
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use ratatui::{Terminal, backend::CrosstermBackend};

use crate::{
    cli::{EvalArgs, WalkArgs},
    evaluation::{self, AnalysisRow, ComparisonRow},
    measure::{self, CancelToken},
    model::{PathFields, Reading, Signal, Speed, timestamp},
    store::{Filter, Store},
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Level {
    Location,
    Room,
    Spot,
    Ssid,
    Baseline,
    BenchmarkConfirmation,
    Before,
    After,
    Scope,
}

#[derive(Debug, Clone)]
pub struct Choice {
    pub id: i64,
    pub label: String,
}

#[derive(Debug, Clone)]
pub struct Picker {
    pub level: Level,
    pub title: String,
    pub items: Vec<Choice>,
    pub selected: usize,
}

impl Picker {
    fn new(level: Level, title: &str, items: Vec<Choice>) -> Self {
        Self {
            level,
            title: title.into(),
            items,
            selected: 0,
        }
    }
    fn press(&mut self, code: KeyCode) {
        self.selected = move_cursor(self.selected, self.items.len(), code);
    }
}

#[derive(Debug, Clone)]
pub enum Edit {
    Location,
    RoomName,
    RoomFloor(String),
    RoomOutdoors(String, i64),
    Spot,
    Ssid,
    Alias,
}

#[derive(Debug, Clone)]
pub struct Editor {
    pub title: String,
    pub text: String,
    pub action: Edit,
}

type LiveResult = Result<(Signal, PathFields, Option<(f64, f64)>), String>;
type MeasurementResult = Result<(Signal, Speed, Option<String>), String>;

#[derive(Debug)]
enum WorkerEvent {
    Live {
        generation: u64,
        result: LiveResult,
    },
    Measured {
        target: Box<Reading>,
        kind: WorkKind,
        result: MeasurementResult,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum WorkKind {
    Snapshot,
    Benchmark,
    Probe,
}

pub struct WalkApp {
    pub demo: bool,
    pub signal: Signal,
    pub path: PathFields,
    pub speed: Speed,
    pub rates: Option<(f64, f64)>,
    pub history: VecDeque<Option<f64>>,
    pub places: Vec<Reading>,
    pub selected: usize,
    pub active: Reading,
    pub walk_id: Option<i64>,
    pub walk_name: String,
    pub baseline_name: Option<String>,
    pub baseline: Option<Reading>,
    pub benchmark: Option<Reading>,
    pub message: String,
    pub sample_error: bool,
    pub pending: usize,
    pub saved: usize,
    pub help: bool,
    pub picker: Option<Picker>,
    pub editor: Option<Editor>,
    store: Store,
    no_speedtest: bool,
    initial_name: Option<String>,
    initial_ssid: Option<String>,
    initial_compare: Option<String>,
    baseline_id: Option<i64>,
    sender: Sender<WorkerEvent>,
    receiver: Receiver<WorkerEvent>,
    cancel: CancelToken,
    generation: Arc<AtomicU64>,
    probe_running: bool,
}

impl WalkApp {
    pub(crate) fn new(store: Store, args: &WalkArgs) -> Result<Self> {
        let (sender, receiver) = mpsc::channel();
        let mut app = Self {
            demo: args.demo,
            signal: Signal::default(),
            path: PathFields::default(),
            speed: Speed::default(),
            rates: None,
            history: VecDeque::new(),
            places: vec![],
            selected: 0,
            active: Reading::default(),
            walk_id: None,
            walk_name: String::new(),
            baseline_name: None,
            baseline: None,
            benchmark: None,
            message: "Choose a location, network and spot to begin surveying.".into(),
            sample_error: false,
            pending: 0,
            saved: 0,
            help: false,
            picker: None,
            editor: None,
            store,
            no_speedtest: args.no_speedtest,
            initial_name: args.name.clone(),
            initial_ssid: args.ssid.clone(),
            initial_compare: args.compare_to.clone(),
            baseline_id: None,
            sender,
            receiver,
            cancel: CancelToken::default(),
            generation: Arc::new(AtomicU64::new(0)),
            probe_running: false,
        };
        if args.demo {
            let first = app.store.list_locations()?.first().map(|l| l.id);
            if let Some(location) = first {
                app.select_location(location)?;
            }
            app.signal = measure::demo_signal(0);
            app.message = "DEMO / SIMULATED · snapshots stay in memory.".into();
        } else if let Some(location) = args.location.as_deref() {
            let id = app.store.resolve_location(location)?;
            app.select_location(id)?;
        } else {
            app.open_picker(Level::Location)?;
        }
        Ok(app)
    }

    fn finish_session(&mut self) -> Result<()> {
        if let Some(id) = self.walk_id.take() {
            self.store.finish_walk(id)?;
        }
        Ok(())
    }

    fn reset_live(&mut self) {
        self.generation.fetch_add(1, Ordering::Relaxed);
        self.speed = Speed::default();
        self.path = PathFields::default();
        self.rates = None;
        self.history.clear();
    }

    fn set_ssid(&mut self, name: &str) -> Result<()> {
        let id = self.store.resolve_ssid(self.active.location_id, name)?;
        let ssid = self
            .store
            .get_ssid(id)?
            .ok_or_else(|| anyhow::anyhow!("network was removed"))?;
        self.active.ssid_id = Some(id);
        self.active.signal.ssid = Some(ssid.name);
        self.start_session()
    }

    fn start_session(&mut self) -> Result<()> {
        if self.active.location_id == 0 || self.active.ssid_id.is_none() {
            return Ok(());
        }
        self.finish_session()?;
        self.reset_live();
        self.walk_name = self.initial_name.take().unwrap_or_else(|| {
            format!("walk {}", chrono::Local::now().format("%Y-%m-%d %H:%M:%S"))
        });
        self.walk_id = Some(self.store.create_walk(
            self.active.location_id,
            self.active.ssid_id,
            &self.walk_name,
        )?);
        self.active.walk_id = self.walk_id;
        self.active.walk_name = Some(self.walk_name.clone());
        if let Some(target) = self.initial_compare.take() {
            let id = if target == "latest" {
                self.store
                    .list_walks(self.active.location_id)?
                    .into_iter()
                    .filter(|w| Some(w.id) != self.walk_id)
                    .find(|w| {
                        self.store
                            .list_readings(&Filter {
                                walk_id: Some(w.id),
                                ..Default::default()
                            })
                            .is_ok_and(|r| !r.is_empty())
                    })
                    .map(|w| w.id)
                    .ok_or_else(|| anyhow::anyhow!("no prior walk available for comparison"))?
            } else {
                self.store.resolve_walk(self.active.location_id, &target)?
            };
            self.select_baseline(id)?;
        }
        Ok(())
    }

    fn select_location(&mut self, id: i64) -> Result<()> {
        let location = self
            .store
            .list_locations()?
            .into_iter()
            .find(|l| l.id == id)
            .ok_or_else(|| anyhow::anyhow!("location was removed"))?;
        if self.active.location_id != id {
            self.finish_session()?;
            self.reset_live();
            self.baseline_id = None;
            self.baseline_name = None;
            self.baseline = None;
            self.active = Reading {
                location_id: id,
                location: location.name,
                ..Default::default()
            };
        }
        self.benchmark = self.store.get_benchmark(id)?;
        self.reload_places()?;
        if let Some(first) = self.places.first().cloned() {
            self.select_spot(first.spot_id)?;
        }
        if let Some(name) = self.initial_ssid.take() {
            self.set_ssid(&name)?;
        } else if self.demo {
            if let Some(ssid) = self.store.list_ssids(id)?.first() {
                self.active.ssid_id = Some(ssid.id);
                self.active.signal.ssid = Some(ssid.name.clone());
                self.start_session()?;
            }
        } else if self.active.ssid_id.is_none() {
            self.open_picker(Level::Ssid)?;
        }
        Ok(())
    }

    fn reload_places(&mut self) -> Result<()> {
        self.places.clear();
        for room in self.store.list_rooms(self.active.location_id)? {
            for spot in self.store.list_spots(room.id)? {
                self.places.push(Reading {
                    location_id: self.active.location_id,
                    location: self.active.location.clone(),
                    room_id: room.id,
                    room: room.name.clone(),
                    floor: room.floor,
                    outdoors: room.outdoors,
                    spot_id: spot.id,
                    spot: spot.name,
                    ..Default::default()
                });
            }
        }
        self.selected = self.selected.min(self.places.len().saturating_sub(1));
        Ok(())
    }

    fn select_spot(&mut self, id: i64) -> Result<()> {
        if let Some((index, place)) = self
            .places
            .iter()
            .enumerate()
            .find(|(_, p)| p.spot_id == id)
        {
            if self.active.spot_id != id {
                self.generation.fetch_add(1, Ordering::Relaxed);
                self.history.clear();
            }
            self.selected = index;
            self.active.room_id = place.room_id;
            self.active.room = place.room.clone();
            self.active.floor = place.floor;
            self.active.outdoors = place.outdoors;
            self.active.spot_id = place.spot_id;
            self.active.spot = place.spot.clone();
            self.speed = Speed::default();
            self.path = PathFields::default();
            self.reload_baseline()?;
        }
        Ok(())
    }

    fn select_baseline(&mut self, id: i64) -> Result<()> {
        if id == 0 {
            self.baseline_id = None;
            self.baseline_name = None;
            self.baseline = None;
            return Ok(());
        }
        let walk = self
            .store
            .get_walk(id)?
            .ok_or_else(|| anyhow::anyhow!("walk was removed"))?;
        if walk.location_id != self.active.location_id || Some(id) == self.walk_id {
            bail!("choose a prior walk in this location");
        }
        if self
            .store
            .list_readings(&Filter {
                walk_id: Some(id),
                limit: Some(1),
                ..Default::default()
            })?
            .is_empty()
        {
            bail!("selected walk has no readings");
        }
        self.baseline_id = Some(id);
        self.baseline_name = Some(walk.name);
        self.reload_baseline()
    }

    fn reload_baseline(&mut self) -> Result<()> {
        self.baseline = if let Some(id) = self.baseline_id {
            let readings: Vec<_> = self
                .store
                .list_readings(&Filter {
                    walk_id: Some(id),
                    ..Default::default()
                })?
                .into_iter()
                .filter(|r| r.spot_id == self.active.spot_id)
                .collect();
            evaluation::analysis_rows(&readings, true)
                .first()
                .map(|row| {
                    let mut reading = row.reading.clone();
                    reading.signal.rssi = row.metric("rssi");
                    reading.signal.noise = row.metric("noise");
                    reading.signal.snr = row.metric("snr");
                    reading.speed.ping_ms = row.metric("ping_ms");
                    reading.speed.down_mbps = row.metric("down_mbps");
                    reading.speed.up_mbps = row.metric("up_mbps");
                    reading
                })
        } else {
            None
        };
        Ok(())
    }

    fn open_picker(&mut self, level: Level) -> Result<()> {
        let (title, items) = match level {
            Level::Location => (
                "Location",
                self.store
                    .list_locations()?
                    .into_iter()
                    .map(|l| Choice {
                        id: l.id,
                        label: l.name,
                    })
                    .collect(),
            ),
            Level::Room => {
                if self.active.location_id == 0 {
                    return self.open_picker(Level::Location);
                }
                (
                    "Room",
                    self.store
                        .list_rooms(self.active.location_id)?
                        .into_iter()
                        .map(|r| Choice {
                            id: r.id,
                            label: format!(
                                "{} · floor {}{}",
                                r.name,
                                r.floor,
                                if r.outdoors { " · outdoors" } else { "" }
                            ),
                        })
                        .collect(),
                )
            }
            Level::Spot => {
                if self.active.room_id == 0 {
                    return self.open_picker(Level::Room);
                }
                (
                    "Spot",
                    self.store
                        .list_spots(self.active.room_id)?
                        .into_iter()
                        .map(|s| Choice {
                            id: s.id,
                            label: s.name,
                        })
                        .collect(),
                )
            }
            Level::Ssid => {
                if self.active.location_id == 0 {
                    return self.open_picker(Level::Location);
                }
                (
                    "Network / SSID",
                    self.store
                        .list_ssids(self.active.location_id)?
                        .into_iter()
                        .map(|s| Choice {
                            id: s.id,
                            label: s.name,
                        })
                        .collect(),
                )
            }
            Level::Baseline => {
                let mut rows: Vec<_> = self
                    .store
                    .list_walks(self.active.location_id)?
                    .into_iter()
                    .filter(|w| Some(w.id) != self.walk_id)
                    .filter(|w| {
                        self.store
                            .list_readings(&Filter {
                                walk_id: Some(w.id),
                                ..Default::default()
                            })
                            .is_ok_and(|r| !r.is_empty())
                    })
                    .map(|w| Choice {
                        id: w.id,
                        label: format!("{} · {} · #{}", w.name, w.started_at, w.id),
                    })
                    .collect();
                rows.push(Choice {
                    id: 0,
                    label: "Off".into(),
                });
                ("Compare with prior walk", rows)
            }
            _ => return Ok(()),
        };
        self.picker = Some(Picker::new(level, title, items));
        Ok(())
    }

    fn begin_create(&mut self, level: Level) {
        let (title, action) = match level {
            Level::Location => ("New location", Edit::Location),
            Level::Room => ("New room name", Edit::RoomName),
            Level::Ssid => ("Network / SSID name", Edit::Ssid),
            Level::Spot => ("New spot name", Edit::Spot),
            _ => return,
        };
        self.picker = None;
        self.editor = Some(Editor {
            title: title.into(),
            text: String::new(),
            action,
        });
    }

    fn submit_editor(&mut self, editor: Editor) -> Result<()> {
        let text = editor.text.trim();
        if text.is_empty() {
            bail!("enter a value, or press Esc to cancel");
        }
        match editor.action {
            Edit::Location => {
                let id = self.store.create_location(text)?;
                self.select_location(id)?;
            }
            Edit::RoomName => {
                self.editor = Some(Editor {
                    title: "Room floor (integer, e.g. 0 or -1)".into(),
                    text: "0".into(),
                    action: Edit::RoomFloor(text.into()),
                })
            }
            Edit::RoomFloor(name) => {
                self.editor = Some(Editor {
                    title: "Outdoors? (yes / no)".into(),
                    text: "no".into(),
                    action: Edit::RoomOutdoors(name, text.parse()?),
                })
            }
            Edit::RoomOutdoors(name, floor) => {
                let outdoors = match text.to_lowercase().as_str() {
                    "y" | "yes" | "1" => true,
                    "n" | "no" | "0" => false,
                    _ => bail!("enter yes or no"),
                };
                let id = self
                    .store
                    .create_room(self.active.location_id, &name, floor, outdoors)?;
                self.active.room_id = id;
                self.active.room = name;
                self.active.floor = floor;
                self.active.outdoors = outdoors;
                self.active.spot_id = 0;
                self.active.spot.clear();
                self.reset_live();
                self.baseline = None;
                self.open_picker(Level::Spot)?;
            }
            Edit::Spot => {
                let id = self.store.create_spot(self.active.room_id, text)?;
                self.reload_places()?;
                self.select_spot(id)?;
            }
            Edit::Ssid => {
                self.set_ssid(text)?;
                if self.active.room_id == 0 {
                    self.open_picker(Level::Room)?;
                }
            }
            Edit::Alias => {
                let bssid = self
                    .signal
                    .bssid
                    .as_deref()
                    .ok_or_else(|| anyhow::anyhow!("access point address unavailable"))?;
                self.store.set_access_point_name(bssid, text)?;
                self.active.ap_name = Some(text.into());
            }
        }
        Ok(())
    }

    fn apply_choice(&mut self, level: Level, id: i64) -> Result<()> {
        match level {
            Level::Location => self.select_location(id)?,
            Level::Room => {
                let room = self
                    .store
                    .list_rooms(self.active.location_id)?
                    .into_iter()
                    .find(|r| r.id == id)
                    .ok_or_else(|| anyhow::anyhow!("room was removed"))?;
                self.active.room_id = room.id;
                self.active.room = room.name;
                self.active.floor = room.floor;
                self.active.outdoors = room.outdoors;
                self.active.spot_id = 0;
                self.active.spot.clear();
                self.reset_live();
                self.baseline = None;
                self.open_picker(Level::Spot)?;
            }
            Level::Spot => self.select_spot(id)?,
            Level::Ssid => {
                let ssid = self
                    .store
                    .list_ssids(self.active.location_id)?
                    .into_iter()
                    .find(|s| s.id == id)
                    .ok_or_else(|| anyhow::anyhow!("network was removed"))?;
                self.active.ssid_id = Some(ssid.id);
                self.active.signal.ssid = Some(ssid.name);
                self.start_session()?;
                if self.active.room_id == 0 {
                    self.open_picker(Level::Room)?;
                }
            }
            Level::Baseline => self.select_baseline(id)?,
            Level::BenchmarkConfirmation if id == 1 => self.start_work(WorkKind::Benchmark)?,
            _ => {}
        }
        Ok(())
    }

    fn start_work(&mut self, kind: WorkKind) -> Result<()> {
        if self.pending > 0 {
            bail!("a measurement is already running; wait for it to finish");
        }
        if self.active.location_id == 0 || self.active.ssid_id.is_none() {
            bail!("select a location and network first (l / w)");
        }
        if kind != WorkKind::Benchmark && self.active.spot_id == 0 {
            bail!("select a spot first (p)");
        }
        if kind == WorkKind::Probe && self.probe_running {
            bail!("speed probe is already running");
        }
        if kind == WorkKind::Probe && self.no_speedtest {
            bail!("speed tests disabled by --no-speedtest");
        }
        let mut target = self.active.clone();
        target.ts = timestamp();
        target.signal = self.signal.clone();
        target.signal.ssid = self.active.signal.ssid.clone();
        target.path = self.path.clone();
        target.walk_id = self.walk_id;
        target.walk_name = Some(self.walk_name.clone());
        self.pending += 1;
        self.probe_running |= kind == WorkKind::Probe;
        self.message = match kind {
            WorkKind::Snapshot => "Capturing snapshot…",
            WorkKind::Benchmark => "Capturing ideal benchmark…",
            WorkKind::Probe => "Running speed probe…",
        }
        .into();
        if self.demo {
            let speed = if self.no_speedtest {
                Speed::default()
            } else {
                demo_speed(self.saved as u64)
            };
            let signal = self.signal.clone();
            self.apply_event(WorkerEvent::Measured {
                target: Box::new(target),
                kind,
                result: Ok((signal, speed, None)),
            });
        } else {
            let sender = self.sender.clone();
            let cancel = self.cancel.clone();
            let no_speedtest = self.no_speedtest;
            thread::spawn(move || {
                let result = (|| {
                    let mut signal = measure::sample_signal_for(Duration::from_secs(5), &cancel)
                        .map_err(|e| e.to_string())?;
                    let _ = measure::refresh_signal_identity(&mut signal, &cancel);
                    let (speed, warning) = if no_speedtest {
                        (Speed::default(), None)
                    } else {
                        match measure::run_speedtest_with_cancel(&cancel) {
                            Ok(speed) => (speed, None),
                            Err(error) => (
                                Speed {
                                    server: Some(format!("ERROR: {error}")),
                                    ..Default::default()
                                },
                                Some(error.to_string()),
                            ),
                        }
                    };
                    Ok((signal, speed, warning))
                })();
                if !cancel.is_cancelled() {
                    let _ = sender.send(WorkerEvent::Measured {
                        target: Box::new(target),
                        kind,
                        result,
                    });
                }
            });
        }
        Ok(())
    }

    fn apply_event(&mut self, event: WorkerEvent) {
        match event {
            WorkerEvent::Live { generation, result } => {
                if generation != self.generation.load(Ordering::Relaxed) {
                    return;
                }
                match result {
                    Ok((mut signal, path, rates)) => {
                        signal.ssid = self.active.signal.ssid.clone().or(signal.ssid);
                        self.active.ap_name = signal
                            .bssid
                            .as_deref()
                            .and_then(|b| self.store.get_access_point_name(b).ok().flatten());
                        self.history.push_back(signal.rssi);
                        while self.history.len() > 60 {
                            self.history.pop_front();
                        }
                        self.signal = signal;
                        self.path = path;
                        self.rates = rates;
                        self.sample_error = false;
                    }
                    Err(error) => {
                        self.message = format!("WiFi unavailable · {error}");
                        self.sample_error = true;
                        self.signal = Signal::default();
                        self.history.push_back(None);
                        while self.history.len() > 60 {
                            self.history.pop_front();
                        }
                    }
                }
            }
            WorkerEvent::Measured {
                mut target,
                kind,
                result,
            } => {
                self.pending = self.pending.saturating_sub(1);
                if kind == WorkKind::Probe {
                    self.probe_running = false;
                }
                let (mut signal, speed, warning) = match result {
                    Ok(value) => value,
                    Err(error) => {
                        self.message = error;
                        return;
                    }
                };
                signal.ssid = target.signal.ssid.clone().or(signal.ssid);
                target.signal = signal;
                target.speed = speed.clone();
                if kind == WorkKind::Probe {
                    if target.location_id == self.active.location_id
                        && target.spot_id == self.active.spot_id
                        && target.ssid_id == self.active.ssid_id
                        && target.walk_id == self.walk_id
                    {
                        self.speed = speed;
                    }
                    self.message = warning
                        .map(|e| format!("Speed probe failed · {e}"))
                        .unwrap_or_else(|| {
                            "Speed probe complete · press s to save a reading.".into()
                        });
                    return;
                }
                let result = if kind == WorkKind::Benchmark {
                    self.store
                        .set_benchmark(target.location_id, &target)
                        .map(|_| 0)
                } else {
                    self.store.add_reading(&target)
                };
                match result {
                    Ok(id) => {
                        if target.location_id == self.active.location_id
                            && target.spot_id == self.active.spot_id
                            && target.ssid_id == self.active.ssid_id
                            && target.walk_id == self.walk_id
                        {
                            self.speed = speed;
                        }
                        if kind == WorkKind::Benchmark {
                            if target.location_id == self.active.location_id {
                                self.benchmark = Some(*target);
                            }
                            self.message = "Ideal benchmark saved.".into();
                        } else {
                            self.saved += 1;
                            self.message =
                                format!("Saved #{id} · {} / {}", target.room, target.spot);
                        }
                        if let Some(warning) = warning {
                            self.message
                                .push_str(&format!(" · speed unavailable: {warning}"));
                        }
                    }
                    Err(error) => self.message = format!("Save failed · {error}"),
                }
            }
        }
    }

    fn drain(&mut self) {
        while let Ok(event) = self.receiver.try_recv() {
            self.apply_event(event);
        }
    }

    fn press(&mut self, key: KeyEvent) -> bool {
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
            return true;
        }
        if self.editor.is_some() {
            let mut editor = self.editor.take().unwrap();
            match key.code {
                KeyCode::Esc => return false,
                KeyCode::Enter => {
                    if let Err(error) = self.submit_editor(editor.clone()) {
                        self.message = error.to_string();
                        self.editor = Some(editor);
                    }
                    return false;
                }
                KeyCode::Backspace => {
                    editor.text.pop();
                }
                KeyCode::Char('u') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                    editor.text.clear()
                }
                KeyCode::Char(c) => editor.text.push(c),
                _ => {}
            }
            self.editor = Some(editor);
            return false;
        }
        if key.code == KeyCode::Char('q') || key.code == KeyCode::Char('Q') {
            return true;
        }
        if self.help {
            if matches!(key.code, KeyCode::Esc | KeyCode::Char('?')) {
                self.help = false;
            }
            return false;
        }
        let result = if let Some(mut picker) = self.picker.take() {
            match key.code {
                KeyCode::Esc => Ok(()),
                KeyCode::Char('+') | KeyCode::Char('n') => {
                    self.begin_create(picker.level);
                    Ok(())
                }
                KeyCode::Enter => {
                    if let Some(choice) = picker.items.get(picker.selected) {
                        self.apply_choice(picker.level, choice.id)
                    } else {
                        self.begin_create(picker.level);
                        Ok(())
                    }
                }
                _ => {
                    picker.press(key.code);
                    self.picker = Some(picker);
                    Ok(())
                }
            }
        } else {
            match key.code {
                KeyCode::Char('?') => {
                    self.help = true;
                    Ok(())
                }
                KeyCode::Char('l') => self.open_picker(Level::Location),
                KeyCode::Char('r') => self.open_picker(Level::Room),
                KeyCode::Char('p') => self.open_picker(Level::Spot),
                KeyCode::Char('w') => self.open_picker(Level::Ssid),
                KeyCode::Char('c') => self.open_picker(Level::Baseline),
                KeyCode::Char('n') => {
                    self.begin_create(if self.active.location_id == 0 {
                        Level::Location
                    } else if self.active.room_id == 0 {
                        Level::Room
                    } else {
                        Level::Spot
                    });
                    Ok(())
                }
                KeyCode::Char('a') => {
                    self.editor = Some(Editor {
                        title: "Access point name".into(),
                        text: self.active.ap_name.clone().unwrap_or_default(),
                        action: Edit::Alias,
                    });
                    Ok(())
                }
                KeyCode::Char('s') => self.start_work(WorkKind::Snapshot),
                KeyCode::Char('b') => {
                    if self.benchmark.is_some() {
                        self.picker = Some(Picker::new(
                            Level::BenchmarkConfirmation,
                            "Replace saved ideal benchmark?",
                            vec![
                                Choice {
                                    id: 0,
                                    label: "Keep existing benchmark".into(),
                                },
                                Choice {
                                    id: 1,
                                    label: "Replace with a new measurement".into(),
                                },
                            ],
                        ));
                        Ok(())
                    } else {
                        self.start_work(WorkKind::Benchmark)
                    }
                }
                KeyCode::Char('t') => self.start_work(WorkKind::Probe),
                code @ (KeyCode::Up
                | KeyCode::Down
                | KeyCode::Home
                | KeyCode::End
                | KeyCode::Char('j')
                | KeyCode::Char('k')) => {
                    let selected = move_cursor(self.selected, self.places.len(), code);
                    if let Some(place) = self.places.get(selected) {
                        self.select_spot(place.spot_id)
                    } else {
                        Ok(())
                    }
                }
                _ => Ok(()),
            }
        };
        if let Err(error) = result {
            self.message = error.to_string();
        }
        false
    }
}

impl Drop for WalkApp {
    fn drop(&mut self) {
        self.cancel.cancel();
        let _ = self.finish_session();
    }
}

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
    pending_scope_kind: Option<Level>,
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
    fn scoped(&self) -> Vec<Reading> {
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
    fn refresh(&mut self) -> Result<()> {
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
    fn open_scope(&mut self, kind: Level) {
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
    fn press(&mut self, key: KeyEvent) -> bool {
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

fn move_cursor(current: usize, count: usize, key: KeyCode) -> usize {
    if count == 0 {
        return 0;
    }
    match key {
        KeyCode::Up | KeyCode::Char('k') => current.saturating_sub(1),
        KeyCode::Down | KeyCode::Char('j') => (current + 1).min(count - 1),
        KeyCode::Home => 0,
        KeyCode::End => count - 1,
        _ => current.min(count - 1),
    }
}

fn demo_speed(tick: u64) -> Speed {
    Speed {
        ping_ms: Some(11.0 + (tick % 4) as f64),
        down_mbps: Some(245.0 + (tick % 9) as f64 * 7.0),
        up_mbps: Some(61.0 + (tick % 5) as f64),
        server: Some("Simulated server".into()),
    }
}

pub fn demo_store() -> Result<Store> {
    let store = Store::memory()?;
    let location = store.create_location("Demo home")?;
    let ssid = store.resolve_ssid(location, "Demo WiFi")?;
    let before = store.create_walk(location, Some(ssid), "Before moving router")?;
    let after = store.create_walk(location, Some(ssid), "After moving router")?;
    for (index, name) in ["Living room", "Office", "Kitchen"].iter().enumerate() {
        let room = store.create_room(location, name, index as i64 / 2, false)?;
        for (spot_index, spot_name) in ["Door", "Desk"].iter().enumerate() {
            let spot = store.create_spot(room, spot_name)?;
            for (walk_id, walk_name, delta) in [
                (before, "Before moving router", 0),
                (after, "After moving router", 7),
            ] {
                for sample in 0..3 {
                    let mut signal = measure::demo_signal((index * 3 + sample) as u64);
                    signal.ssid = Some("Demo WiFi".into());
                    signal.rssi =
                        Some(-53.0 - index as f64 * 8.0 - spot_index as f64 * 3.0 + delta as f64);
                    signal.snr = signal.rssi.map(|rssi| rssi + 92.0);
                    let reading = Reading {
                        ts: timestamp(),
                        location_id: location,
                        location: "Demo home".into(),
                        room_id: room,
                        room: (*name).into(),
                        floor: index as i64 / 2,
                        spot_id: spot,
                        spot: (*spot_name).into(),
                        ssid_id: Some(ssid),
                        signal,
                        speed: demo_speed(sample as u64),
                        walk_id: Some(walk_id),
                        walk_name: Some(walk_name.into()),
                        path: PathFields {
                            path_probe_count: Some(60),
                            gateway_rtt_ms: Some(2.1),
                            internet_rtt_ms: Some(12.7),
                            gateway_loss_pct: Some(0.0),
                            internet_loss_pct: Some(0.0),
                            ..Default::default()
                        },
                        ..Default::default()
                    };
                    store.add_reading(&reading)?;
                }
            }
        }
    }
    store.finish_walk(before)?;
    store.finish_walk(after)?;
    store.set_access_point_name("02:00:00:00:00:01", "Demo access point")?;
    Ok(store)
}

struct TerminalGuard;
impl Drop for TerminalGuard {
    fn drop(&mut self) {
        let _ = disable_raw_mode();
        let _ = execute!(io::stdout(), LeaveAlternateScreen, crossterm::cursor::Show);
    }
}

fn with_terminal(
    mut draw_loop: impl FnMut(&mut Terminal<CrosstermBackend<io::Stdout>>) -> Result<()>,
) -> Result<()> {
    if !io::stdin().is_terminal() || !io::stdout().is_terminal() {
        bail!("interactive dashboard requires a terminal; use history/export for saved readings");
    }
    enable_raw_mode()?;
    let _guard = TerminalGuard;
    execute!(io::stdout(), EnterAlternateScreen)?;
    let mut terminal = Terminal::new(CrosstermBackend::new(io::stdout()))?;
    terminal.clear()?;
    draw_loop(&mut terminal)
}

fn spawn_live(app: &WalkApp, interval: Duration) {
    let sender = app.sender.clone();
    let cancel = app.cancel.clone();
    let generation = app.generation.clone();
    thread::spawn(move || {
        let path_worker =
            measure::PathWorker::start_with_cancel(measure::gateway_with_cancel(&cancel), &cancel);
        let mut tracker = measure::PathTracker::default();
        let mut traffic = measure::TrafficSampler::default();
        let mut previous_generation = generation.load(Ordering::Relaxed);
        while !cancel.is_cancelled() {
            let current_generation = generation.load(Ordering::Relaxed);
            if previous_generation != current_generation {
                while path_worker.try_recv().is_some() {}
                tracker.reset();
                previous_generation = current_generation;
            }
            while let Some(sample) = path_worker.try_recv() {
                tracker.record_sample(&sample);
            }
            let result = measure::sample_signal_with_cancel(&cancel)
                .map(|signal| {
                    (
                        signal,
                        tracker.fields(),
                        traffic.sample_with_cancel(&cancel),
                    )
                })
                .map_err(|e| e.to_string());
            if sender
                .send(WorkerEvent::Live {
                    generation: current_generation,
                    result,
                })
                .is_err()
            {
                break;
            }
            let deadline = Instant::now() + interval;
            while !cancel.is_cancelled() && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(50));
            }
        }
    });
}

pub fn run_walk(db_path: &Path, args: &WalkArgs) -> Result<()> {
    let store = if args.demo {
        demo_store()?
    } else {
        Store::open(db_path)?
    };
    let mut app = WalkApp::new(store, args)?;
    let _interrupt = measure::install_interrupt_handler(&app.cancel)?;
    let interval = Duration::from_secs_f64(args.interval.clamp(0.1, 3600.0));
    if !app.demo {
        spawn_live(&app, interval);
    }
    let mut last_demo = Instant::now();
    let mut tick = 0;
    let result = with_terminal(|terminal| {
        loop {
            if app.cancel.is_cancelled() {
                break;
            }
            app.drain();
            if app.demo && last_demo.elapsed() >= interval {
                tick += 1;
                app.apply_event(WorkerEvent::Live {
                    generation: app.generation.load(Ordering::Relaxed),
                    result: Ok((
                        measure::demo_signal(tick),
                        PathFields {
                            path_probe_count: Some(60),
                            gateway_rtt_ms: Some(2.0 + (tick % 4) as f64 / 10.0),
                            internet_rtt_ms: Some(12.0 + (tick % 6) as f64),
                            gateway_loss_pct: Some(0.0),
                            internet_loss_pct: Some(0.0),
                            ..Default::default()
                        },
                        Some((4.0 + (tick % 5) as f64, 1.2)),
                    )),
                });
                last_demo = Instant::now();
            }
            terminal.draw(|frame| crate::ui::draw_walk(frame, &app))?;
            if event::poll(Duration::from_millis(80))?
                && let Event::Key(key) = event::read()?
                && key.kind != KeyEventKind::Release
                && app.press(key)
            {
                break;
            }
        }
        Ok(())
    });
    app.cancel.cancel();
    app.drain();
    app.finish_session()?;
    result
}

pub fn run_eval(db_path: &Path, args: &EvalArgs) -> Result<()> {
    let (readings, source) = if args.demo {
        (
            demo_store()?.list_readings(&Filter::default())?,
            "in-memory demo".into(),
        )
    } else if let Some(csv) = &args.csv {
        (evaluation::load_csv(csv)?, csv.display().to_string())
    } else {
        (evaluation::load_db(db_path)?, db_path.display().to_string())
    };
    if !io::stdin().is_terminal() || !io::stdout().is_terminal() {
        println!(
            "WIFIMAP evaluation{} · {} readings · {source}",
            if args.demo { " / DEMO SIMULATED" } else { "" },
            readings.len()
        );
        for row in
            evaluation::rank_rows(&evaluation::analysis_rows(&readings, true), "rssi", false)?
        {
            println!(
                "{} / {} / {} · RSSI {} dBm · {} readings",
                row.reading.location,
                row.reading.room,
                row.reading.spot,
                crate::model::display(row.metric("rssi")),
                row.count
            );
        }
        return Ok(());
    }
    let mut app = EvalApp::new(readings, source, args.demo)?;
    let cancel = CancelToken::default();
    let _interrupt = measure::install_interrupt_handler(&cancel)?;
    with_terminal(|terminal| {
        loop {
            if cancel.is_cancelled() {
                return Ok(());
            }
            terminal.draw(|frame| crate::ui::draw_eval(frame, &app))?;
            if event::poll(Duration::from_millis(100))?
                && let Event::Key(key) = event::read()?
                && key.kind != KeyEventKind::Release
                && app.press(key)
            {
                return Ok(());
            }
        }
    })
}

#[cfg(test)]
mod tests {
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
            kind: WorkKind::Snapshot,
            result: Ok((measure::demo_signal(0), demo_speed(0), None)),
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
        app.apply_event(WorkerEvent::Measured {
            target: Box::new(target),
            kind: WorkKind::Snapshot,
            result: Ok((
                measure::demo_signal(0),
                Speed {
                    server: Some("ERROR: no speedtest".into()),
                    ..Default::default()
                },
                Some("no speedtest".into()),
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
            result: Ok((
                measure::demo_signal(0),
                PathFields {
                    gateway_rtt_ms: Some(99.0),
                    ..Default::default()
                },
                None,
            )),
        });
        assert!(app.path.gateway_rtt_ms.is_none());
        assert!(app.speed.down_mbps.is_none());
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
            kind: WorkKind::Probe,
            result: Ok((measure::demo_signal(0), demo_speed(0), None)),
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
}
