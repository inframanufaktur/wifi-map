//! Terminal application state. Measurement workers never own the database.
use std::{
    collections::VecDeque,
    io::{self, IsTerminal},
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
        mpsc::{self, Receiver, Sender},
    },
    time::{Duration, Instant},
};

use anyhow::{Result, bail};
use crossterm::{
    event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers},
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use ratatui::{Terminal, backend::CrosstermBackend};

mod eval;
mod live;
mod measurement;
mod survey;

pub use eval::EvalApp;
#[cfg(test)]
use live::record_recent_probes;
use live::spawn_live;

use crate::{
    cli::{EvalArgs, WalkArgs},
    evaluation,
    measure::{self, CancelToken},
    model::{PathFields, Reading, Signal, Speed, timestamp},
    path_diagnostics::{LoadLatency, PathDiagnostics},
    store::{Filter, Store},
    timeline::Timeline,
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

#[derive(Debug)]
struct LiveSample {
    signal: Signal,
    path: PathFields,
    diagnostics: PathDiagnostics,
    latest_probe: Option<(Option<f64>, Option<f64>)>,
    rates: Option<(f64, f64)>,
}

type LiveResult = Result<LiveSample, String>;

#[derive(Debug)]
struct LoadReport {
    latency: LoadLatency,
    association: Signal,
    completed_at: Instant,
}

type MeasurementResult = Result<(Signal, Speed, Option<String>, Option<Box<LoadReport>>), String>;

#[derive(Debug)]
enum WorkerEvent {
    Live {
        generation: u64,
        observed_at: Instant,
        result: LiveResult,
    },
    Measured {
        target: Box<Reading>,
        generation: u64,
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
    pub diagnostics_open: bool,
    pub diagnostics_events: bool,
    pub diagnostics_offset: usize,
    pub timeline: Timeline,
    pub path_diagnostics: PathDiagnostics,
    pub load_latency: Option<LoadLatency>,
    pub diagnostics_epoch: Instant,
    pub load_running: bool,
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
    last_association: Signal,
    ssid_observed_at: Option<Instant>,
    bssid_observed_at: Option<Instant>,
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
            diagnostics_open: false,
            diagnostics_events: true,
            diagnostics_offset: 0,
            timeline: Timeline::default(),
            path_diagnostics: PathDiagnostics::default(),
            load_latency: None,
            diagnostics_epoch: Instant::now(),
            load_running: false,
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
            last_association: Signal::default(),
            ssid_observed_at: None,
            bssid_observed_at: None,
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
        } else if self.diagnostics_open {
            let count = if self.diagnostics_events {
                self.timeline.events.len()
            } else {
                self.timeline.samples.len()
            };
            match key.code {
                KeyCode::Esc | KeyCode::Char('d') => {
                    self.diagnostics_open = false;
                    Ok(())
                }
                KeyCode::Tab | KeyCode::BackTab => {
                    self.diagnostics_events = !self.diagnostics_events;
                    self.diagnostics_offset = 0;
                    Ok(())
                }
                KeyCode::Char('?') => {
                    self.help = true;
                    Ok(())
                }
                KeyCode::Char('t') => self.start_work(WorkKind::Probe),
                code @ (KeyCode::Up
                | KeyCode::Down
                | KeyCode::Home
                | KeyCode::End
                | KeyCode::Char('j')
                | KeyCode::Char('k')) => {
                    self.diagnostics_offset = move_cursor(self.diagnostics_offset, count, code);
                    Ok(())
                }
                _ => Ok(()),
            }
        } else {
            match key.code {
                KeyCode::Char('?') => {
                    self.help = true;
                    Ok(())
                }
                KeyCode::Char('d') => {
                    self.diagnostics_open = !self.diagnostics_open;
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

fn demo_load_latency() -> LoadLatency {
    use crate::path_diagnostics::LatencyPhase;
    let phase = |gateway, internet, jitter| LatencyPhase {
        path: PathFields {
            path_probe_count: Some(6),
            gateway_rtt_ms: Some(gateway),
            gateway_p95_ms: Some(gateway + jitter),
            gateway_loss_pct: Some(0.),
            internet_rtt_ms: Some(internet),
            internet_p95_ms: Some(internet + jitter),
            internet_loss_pct: Some(0.),
            ..Default::default()
        },
        diagnostics: PathDiagnostics {
            gateway_jitter_ms: Some(jitter),
            internet_jitter_ms: Some(jitter),
            gateway_latest_ms: Some(gateway),
            internet_latest_ms: Some(internet),
        },
    };
    LoadLatency {
        idle: phase(2., 12., 0.3),
        loaded: phase(24., 140., 18.),
        recovery: phase(3., 15., 1.),
        ..Default::default()
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
                    observed_at: Instant::now(),
                    result: Ok(LiveSample {
                        signal: measure::demo_signal(tick),
                        path: PathFields {
                            path_probe_count: Some(60),
                            gateway_rtt_ms: Some(2.0 + (tick % 4) as f64 / 10.0),
                            internet_rtt_ms: Some(12.0 + (tick % 6) as f64),
                            gateway_loss_pct: Some(0.0),
                            internet_loss_pct: Some(0.0),
                            ..Default::default()
                        },
                        diagnostics: PathDiagnostics {
                            gateway_jitter_ms: Some(0.15),
                            internet_jitter_ms: Some(1.5),
                            gateway_latest_ms: Some(2.0 + (tick % 4) as f64 / 10.0),
                            internet_latest_ms: Some(12.0 + (tick % 6) as f64),
                        },
                        latest_probe: Some((
                            Some(2.0 + (tick % 4) as f64 / 10.0),
                            Some(12.0 + (tick % 6) as f64),
                        )),
                        rates: Some((4.0 + (tick % 5) as f64, 1.2)),
                    }),
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
mod tests;
