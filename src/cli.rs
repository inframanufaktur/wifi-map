use crate::{
    app, evaluation, measure,
    model::{Reading, Speed, display, timestamp},
    store::{Filter, Store},
};
use anyhow::{Result, anyhow, bail};
use clap::{Args, CommandFactory, Parser, Subcommand};
use std::{
    io::{self, IsTerminal, Write},
    path::PathBuf,
};

#[derive(Debug, Parser)]
#[command(
    name = "wifimap",
    version,
    about = "Map and evaluate home WiFi.",
    after_help = "Exit codes: 0 success, 2 WiFi/signal or arguments, 3 storage/CSV."
)]
pub struct Cli {
    /// SQLite database (default: <project>/db/wifi-map.db)
    #[arg(long, global = true)]
    pub db: Option<PathBuf>,
    /// Explore simulated measurements in an isolated in-memory database
    #[arg(long, global = true)]
    pub demo: bool,
    #[command(subcommand)]
    pub command: Option<Command>,
}

#[derive(Debug, Subcommand)]
pub enum Command {
    /// Save one signal and optional throughput snapshot
    Scan(ScanArgs),
    /// Open the live survey dashboard
    Walk(WalkArgs),
    /// Rank saved measurements and compare walks
    Eval(EvalArgs),
    /// Manage locations
    Locations {
        #[command(subcommand)]
        command: Option<LocationCommand>,
    },
    /// Manage rooms
    Rooms {
        #[command(subcommand)]
        command: RoomCommand,
    },
    /// Manage spots
    Spots {
        #[command(subcommand)]
        command: SpotCommand,
    },
    /// Name and inspect access points
    Aps {
        #[command(subcommand)]
        command: ApCommand,
    },
    /// Print saved readings
    List(ListArgs),
    /// Export joined measurements to CSV
    Export(ExportArgs),
    /// Manage an ideal-conditions reference
    Benchmark {
        #[command(subcommand)]
        command: BenchmarkCommand,
    },
    /// Emit documentation capabilities from the compiled command tree
    #[command(hide = true)]
    Capabilities,
}

#[derive(Debug, Args)]
pub struct ScanArgs {
    #[arg(long)]
    pub location: String,
    #[arg(long)]
    pub room: String,
    #[arg(long)]
    pub spot: String,
    /// Select or create a network name; prompt when omitted
    #[arg(long)]
    pub ssid: Option<String>,
    #[arg(long, default_value_t = 0, allow_hyphen_values = true)]
    pub room_floor: i64,
    #[arg(long, default_value_t = 0, value_parser = clap::value_parser!(u8).range(0..=1))]
    pub room_outdoors: u8,
    #[arg(long)]
    pub no_speedtest: bool,
    #[arg(long)]
    pub note: Option<String>,
}

#[derive(Debug, Clone, Args)]
pub struct WalkArgs {
    #[arg(long, default_value_t = 1.0, value_parser = positive_interval)]
    pub interval: f64,
    #[arg(long)]
    pub no_speedtest: bool,
    #[arg(long)]
    pub location: Option<String>,
    #[arg(long)]
    pub ssid: Option<String>,
    #[arg(long)]
    pub name: Option<String>,
    #[arg(long)]
    pub compare_to: Option<String>,
    #[arg(skip)]
    pub demo: bool,
}

impl Default for WalkArgs {
    fn default() -> Self {
        Self {
            interval: 1.0,
            no_speedtest: false,
            location: None,
            ssid: None,
            name: None,
            compare_to: None,
            demo: false,
        }
    }
}

#[derive(Debug, Clone, Default, Args)]
pub struct EvalArgs {
    #[arg(long)]
    pub csv: Option<PathBuf>,
    #[arg(skip)]
    pub demo: bool,
}

#[derive(Debug, Subcommand)]
pub enum LocationCommand {
    List,
    Add {
        #[arg(long)]
        name: String,
    },
}
#[derive(Debug, Subcommand)]
pub enum RoomCommand {
    List {
        #[arg(long)]
        location: String,
    },
    Add {
        #[arg(long)]
        location: String,
        #[arg(long)]
        name: String,
        #[arg(long, default_value_t = 0, allow_hyphen_values = true)]
        floor: i64,
        #[arg(long)]
        outdoors: bool,
    },
}
#[derive(Debug, Subcommand)]
pub enum SpotCommand {
    List {
        #[arg(long)]
        location: String,
        #[arg(long)]
        room: String,
    },
    Add {
        #[arg(long)]
        location: String,
        #[arg(long)]
        room: String,
        #[arg(long)]
        name: String,
    },
}
#[derive(Debug, Subcommand)]
pub enum ApCommand {
    Current,
    List,
    Name {
        #[arg(long)]
        name: String,
        #[arg(long)]
        bssid: Option<String>,
    },
}
#[derive(Debug, Clone, Default, Args)]
pub struct FilterArgs {
    #[arg(long)]
    pub location: Option<String>,
    #[arg(long)]
    pub room: Option<String>,
    #[arg(long)]
    pub spot: Option<String>,
    #[arg(long, allow_hyphen_values = true)]
    pub floor: Option<i64>,
    #[arg(long)]
    pub ssid: Option<String>,
}
impl FilterArgs {
    fn filter(&self, limit: Option<usize>) -> Result<Filter> {
        let ssid = self.ssid.as_deref().map(str::trim);
        if ssid.is_some_and(str::is_empty) {
            bail!("--ssid must not be blank");
        }
        Ok(Filter {
            location: self.location.clone(),
            room: self.room.clone(),
            spot: self.spot.clone(),
            floor: self.floor,
            ssid: ssid.map(str::to_owned),
            limit,
            walk_id: None,
        })
    }
}
#[derive(Debug, Args)]
pub struct ListArgs {
    #[command(flatten)]
    pub filter: FilterArgs,
    #[arg(long, default_value_t = 50)]
    pub limit: usize,
}
#[derive(Debug, Args)]
pub struct ExportArgs {
    #[command(flatten)]
    pub filter: FilterArgs,
    #[arg(long)]
    pub csv: PathBuf,
}
#[derive(Debug, Subcommand)]
pub enum BenchmarkCommand {
    Set {
        #[arg(long)]
        location: String,
        #[arg(long)]
        no_speedtest: bool,
        #[arg(long)]
        note: Option<String>,
        #[arg(long)]
        force: bool,
    },
    Show {
        #[arg(long)]
        location: String,
    },
    Clear {
        #[arg(long)]
        location: String,
        #[arg(long)]
        force: bool,
    },
}

fn positive_interval(raw: &str) -> std::result::Result<f64, String> {
    let value: f64 = raw
        .parse()
        .map_err(|_| "interval must be a number".to_string())?;
    if !value.is_finite() || value < 0.1 {
        return Err("interval must be finite and at least 0.1 seconds".into());
    }
    Ok(value)
}

pub fn default_db() -> PathBuf {
    // The launcher supplies the checkout path; installed binaries use the current directory.
    std::env::var_os("WIFIMAP_PROJECT_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join("db/wifi-map.db")
}

pub fn run(mut cli: Cli) -> Result<()> {
    if matches!(cli.command, Some(Command::Capabilities)) {
        println!("{}", capabilities());
        return Ok(());
    }
    let db = cli.db.take().unwrap_or_else(default_db);
    match cli.command.unwrap_or(Command::Walk(WalkArgs::default())) {
        Command::Walk(mut args) => {
            for text in [&mut args.ssid, &mut args.name, &mut args.compare_to]
                .into_iter()
                .flatten()
            {
                *text = text.trim().to_string();
                if text.is_empty() {
                    bail!("SSID, walk name and comparison must not be blank");
                }
            }
            args.demo = cli.demo;
            app::run_walk(&db, &args)
        }
        Command::Eval(mut args) => {
            args.demo = cli.demo;
            app::run_eval(&db, &args)
        }
        command => {
            let store = if cli.demo {
                Store::memory()?
            } else {
                Store::open(&db)?
            };
            run_storage(&store, command, cli.demo)
        }
    }
}

fn run_storage(store: &Store, command: Command, demo: bool) -> Result<()> {
    match command {
        Command::Scan(args) => scan(store, &args, demo)?,
        Command::Locations { command } => match command.unwrap_or(LocationCommand::List) {
            LocationCommand::Add { name } => println!("{}", store.create_location(&name)?),
            LocationCommand::List => {
                println!("{:<4} name", "id");
                for row in store.list_locations()? {
                    println!("{:<4} {}", row.id, row.name);
                }
            }
        },
        Command::Rooms { command } => match command {
            RoomCommand::List { location } => {
                let id = store.lookup_location(&location)?;
                println!("{:<4} {:<20} {:<6} outdoors", "id", "name", "floor");
                for row in store.list_rooms(id)? {
                    println!(
                        "{:<4} {:<20} {:<6} {}",
                        row.id,
                        row.name,
                        row.floor,
                        u8::from(row.outdoors)
                    );
                }
            }
            RoomCommand::Add {
                location,
                name,
                floor,
                outdoors,
            } => {
                let id = store.resolve_location(&location)?;
                println!("{}", store.create_room(id, &name, floor, outdoors)?);
            }
        },
        Command::Spots { command } => match command {
            SpotCommand::List { location, room } => {
                let location = store.lookup_location(&location)?;
                let room = store.lookup_room(location, &room)?;
                println!("{:<4} name", "id");
                for row in store.list_spots(room)? {
                    println!("{:<4} {}", row.id, row.name);
                }
            }
            SpotCommand::Add {
                location,
                room,
                name,
            } => {
                let location = store.resolve_location(&location)?;
                let room = store.resolve_room(location, &room, 0, false)?;
                println!("{}", store.create_spot(room, &name)?);
            }
        },
        Command::Aps { command } => match command {
            ApCommand::List => {
                println!("{:<17}  name", "bssid");
                for ap in store.list_access_points()? {
                    println!("{}  {}", ap.bssid, ap.name);
                }
            }
            ApCommand::Name { name, bssid } => {
                let bssid = match bssid {
                    Some(value) => value,
                    None => current_bssid(demo)?,
                };
                let ap = store.set_access_point_name(&bssid, &name)?;
                println!("{}  {}", ap.bssid, ap.name);
            }
            ApCommand::Current => {
                let bssid = current_bssid(demo)?;
                println!(
                    "{}{}",
                    bssid,
                    store
                        .get_access_point_name(&bssid)?
                        .map(|n| format!("  {n}"))
                        .unwrap_or_default()
                );
            }
        },
        Command::List(args) => {
            print_readings(&store.list_readings(&args.filter.filter(Some(args.limit))?)?)
        }
        Command::Export(args) => {
            let readings = store.list_readings(&args.filter.filter(None)?)?;
            evaluation::export_csv(&args.csv, &readings)?;
            println!("wrote {} rows to {}", readings.len(), args.csv.display());
        }
        Command::Benchmark { command } => benchmark(store, command, demo)?,
        _ => unreachable!("terminal commands are dispatched before storage"),
    }
    Ok(())
}

fn current_bssid(demo: bool) -> Result<String> {
    let cancel = measure::CancelToken::default();
    let _interrupt = interruptible(demo, &cancel)?;
    let signal = if demo {
        measure::demo_signal(0)
    } else {
        measure::sample_signal_with_cancel(&cancel)?
    };
    signal
        .bssid
        .ok_or_else(|| anyhow!("current access point is unavailable; pass --bssid"))
}

fn selected_ssid(
    store: &Store,
    location: i64,
    requested: Option<&str>,
    detected: Option<&str>,
) -> Result<Option<i64>> {
    if let Some(name) = requested {
        if name.trim().is_empty() {
            bail!("--ssid must not be blank");
        }
        return Ok(Some(store.resolve_ssid(location, name)?));
    }
    if !io::stdin().is_terminal() {
        bail!("SSID selection requires a terminal; pass --ssid NAME");
    }
    let choices = store.list_ssids(location)?;
    println!("Select SSID:");
    for (index, ssid) in choices.iter().enumerate() {
        println!("{}. {}", index + 1, ssid.name);
    }
    if let Some(name) = detected {
        println!("Detected: {name} (Enter to use)");
    }
    let raw = prompt("SSID [number/name, q cancel]: ")?;
    if matches!(raw.as_str(), "q" | "quit") {
        return Ok(None);
    }
    let name = if raw.is_empty() {
        detected.ok_or_else(|| anyhow!("SSID name must not be blank"))?
    } else if let Ok(index) = raw.parse::<usize>() {
        choices
            .get(index.wrapping_sub(1))
            .map(|s| s.name.as_str())
            .ok_or_else(|| anyhow!("invalid SSID choice"))?
    } else {
        &raw
    };
    Ok(Some(store.resolve_ssid(location, name)?))
}

fn speed(no_speedtest: bool, demo: bool, cancel: &measure::CancelToken) -> Result<Speed> {
    if no_speedtest {
        return Ok(Speed::default());
    }
    if demo {
        return Ok(Speed {
            ping_ms: Some(8.0),
            down_mbps: Some(420.0),
            up_mbps: Some(90.0),
            server: Some("DEMO / SIMULATED".into()),
        });
    }
    eprintln!("speedtest running...");
    match measure::run_speedtest_with_cancel(cancel) {
        Ok(value) => Ok(value),
        Err(measure::MeasureError::Cancelled) => Err(measure::MeasureError::Cancelled.into()),
        Err(error) => {
            eprintln!("Warning: {error}; proceeding signal-only");
            Ok(Speed {
                server: (!matches!(error, measure::MeasureError::SpeedtestUnavailable))
                    .then(|| "ERROR".into()),
                ..Speed::default()
            })
        }
    }
}

fn scan(store: &Store, args: &ScanArgs, demo: bool) -> Result<()> {
    let location = store.resolve_location(&args.location)?;
    let room = store.resolve_room(
        location,
        &args.room,
        args.room_floor,
        args.room_outdoors != 0,
    )?;
    let spot = store.resolve_spot(room, &args.spot)?;
    let identity = if args.ssid.is_none() && !demo {
        let cancel = measure::CancelToken::default();
        let _interrupt = interruptible(false, &cancel)?;
        match measure::read_network_identity_with_cancel(&cancel) {
            Ok(value) => Some(value),
            Err(measure::MeasureError::Cancelled) => {
                return Err(measure::MeasureError::Cancelled.into());
            }
            Err(_) => None,
        }
    } else {
        None
    };
    let detected = identity.as_ref().and_then(|i| i.0.as_deref());
    let Some(ssid_id) = selected_ssid(store, location, args.ssid.as_deref(), detected)? else {
        println!("SSID selection cancelled");
        return Ok(());
    };
    let cancel = measure::CancelToken::default();
    let _interrupt = interruptible(demo, &cancel)?;
    let mut signal = capture_signal(demo, &cancel)?;
    signal.ssid = store.get_ssid(ssid_id)?.map(|s| s.name);
    if signal.bssid.is_none() {
        signal.bssid = identity.and_then(|i| i.1);
    }
    let reading = Reading {
        ts: timestamp(),
        spot_id: spot,
        ssid_id: Some(ssid_id),
        location_id: location,
        signal,
        speed: speed(args.no_speedtest, demo, &cancel)?,
        note: args.note.clone(),
        ..Reading::default()
    };
    let id = store.add_reading(&reading)?;
    println!(
        "#{id} {}/{}/{} rssi={} snr={} down={} up={} note={}{}",
        args.location,
        args.room,
        args.spot,
        display(reading.signal.rssi),
        display(reading.signal.snr),
        display(reading.speed.down_mbps),
        display(reading.speed.up_mbps),
        display(reading.note.as_deref()),
        if demo { " [DEMO / SIMULATED]" } else { "" }
    );
    Ok(())
}

fn benchmark(store: &Store, command: BenchmarkCommand, demo: bool) -> Result<()> {
    match command {
        BenchmarkCommand::Set {
            location,
            no_speedtest,
            note,
            force,
        } => {
            let id = store.resolve_location(&location)?;
            if store.get_benchmark(id)?.is_some()
                && !force
                && !confirm(&format!("Overwrite benchmark for {location}? [y/N] "))?
            {
                println!("benchmark kept");
                return Ok(());
            }
            let cancel = measure::CancelToken::default();
            let _interrupt = interruptible(demo, &cancel)?;
            let signal = capture_signal(demo, &cancel)?;
            let reading = Reading {
                ts: timestamp(),
                location_id: id,
                signal,
                speed: speed(no_speedtest, demo, &cancel)?,
                note,
                ..Reading::default()
            };
            store.set_benchmark(id, &reading)?;
            print_benchmark(&location, &reading);
        }
        BenchmarkCommand::Show { location } => {
            let id = store.lookup_location(&location)?;
            let reading = store
                .get_benchmark(id)?
                .ok_or_else(|| anyhow!("no benchmark for {location}"))?;
            print_benchmark(&location, &reading);
        }
        BenchmarkCommand::Clear { location, force } => {
            let id = store.lookup_location(&location)?;
            if store.get_benchmark(id)?.is_none() {
                bail!("no benchmark for {location}");
            }
            if !force && !confirm(&format!("Clear benchmark for {location}? [y/N] "))? {
                println!("benchmark kept");
                return Ok(());
            }
            store.clear_benchmark(id)?;
            println!("benchmark cleared for {location}");
        }
    }
    Ok(())
}
fn interruptible(
    demo: bool,
    cancel: &measure::CancelToken,
) -> Result<Option<measure::InterruptGuard>> {
    if demo {
        Ok(None)
    } else {
        Ok(Some(measure::install_interrupt_handler(cancel)?))
    }
}
fn capture_signal(demo: bool, cancel: &measure::CancelToken) -> Result<crate::model::Signal> {
    if demo {
        return Ok(measure::demo_signal(0));
    }
    eprintln!("sampling 5s...");
    Ok(measure::sample_signal_for(
        std::time::Duration::from_secs(5),
        cancel,
    )?)
}
fn print_benchmark(location: &str, reading: &Reading) {
    println!(
        "benchmark for {location} rssi={} snr={} down={} up={} note={}",
        display(reading.signal.rssi),
        display(reading.signal.snr),
        display(reading.speed.down_mbps),
        display(reading.speed.up_mbps),
        display(reading.note.as_deref())
    );
}
pub fn print_readings(rows: &[Reading]) {
    println!(
        "{:<4} {:<20} {:<20} {:<20} {:<6} {:<6} {:<9} {:<9} note",
        "id", "location", "room", "spot", "floor", "rssi", "down", "up"
    );
    for row in rows {
        println!(
            "{:<4} {:<20} {:<20} {:<20} {:<6} {:<6} {:<9} {:<9} {}",
            row.id,
            row.location,
            row.room,
            row.spot,
            row.floor,
            display(row.signal.rssi),
            display(row.speed.down_mbps),
            display(row.speed.up_mbps),
            display(row.note.as_deref())
        );
    }
}
fn prompt(message: &str) -> Result<String> {
    print!("{message}");
    io::stdout().flush()?;
    let mut value = String::new();
    io::stdin().read_line(&mut value)?;
    Ok(value.trim().to_string())
}
fn confirm(message: &str) -> Result<bool> {
    if !io::stdin().is_terminal() {
        bail!("confirmation requires a terminal; pass --force");
    }
    Ok(matches!(
        prompt(message)?.to_lowercase().as_str(),
        "y" | "yes"
    ))
}

pub fn capabilities() -> serde_json::Value {
    let mut command = Cli::command();
    command.build();
    let mut commands = Vec::new();
    command_docs(&command, &[], &mut commands);
    serde_json::json!({
        "package": {"name":"wifimap","version":env!("CARGO_PKG_VERSION"),"rust_requires":env!("CARGO_PKG_RUST_VERSION")},
        "cli": {"program":"wifimap","summary":"Map and evaluate home WiFi.","epilog":"Exit codes: 0 success; 2 WiFi/signal or arguments; 3 storage/CSV.","commands":commands},
        "walk": {"footer":"s snapshot · t speed probe · c compare · b benchmark · l switch · n new · q quit",
            "controls":[{"name":"snapshot","key":"s"},{"name":"speed_probe","key":"t"},{"name":"compare","key":"c"},{"name":"benchmark","key":"b"},{"name":"switch","key":"l"},{"name":"new","key":"n"},{"name":"quit","key":"q"}],
            "statuses":["GREAT","OK","WEAK","UNKNOWN"],
            "status_thresholds":[{"metric":"rssi","unit":"dBm","great_min":-60,"ok_min":-70},{"metric":"snr","unit":"dB","great_min":25,"ok_min":15}],"chart_glyphs":["▁","▂","▃","▄","▅","▆","▇","█"]},
        "evaluation": {"kinds":[{"key":"location","label":"Location"},{"key":"ssid","label":"SSID"}],
            "metrics":metric_docs(false),"comparison_metrics":metric_docs(true)}
    })
}
fn metric_docs(comparison: bool) -> Vec<serde_json::Value> {
    evaluation::METRICS.iter().filter(|metric| !comparison || evaluation::COMPARISON_METRICS.contains(&metric.key))
        .map(|metric| serde_json::json!({"key":metric.key,"label":metric.label,"unit":metric.unit,"higher_is_better":metric.higher_is_better,"decimals":metric.decimals})).collect()
}
fn command_docs(command: &clap::Command, path: &[String], output: &mut Vec<serde_json::Value>) {
    let options:Vec<_> = command.get_arguments().filter(|a| !a.is_hide_set()).map(|a| {
        let mut flags = Vec::new();
        if let Some(short) = a.get_short() { flags.push(format!("-{short}")); }
        if let Some(long) = a.get_long() { flags.push(format!("--{long}")); }
        let takes_value = a.get_action().takes_values();
        let defaults:Vec<_> = a.get_default_values().iter().map(|s| s.to_string_lossy().to_string()).collect();
        let default = if defaults.is_empty() { if takes_value { serde_json::Value::Null } else { serde_json::json!(false) } } else { serde_json::json!(defaults[0]) };
        let possible: Vec<_> = a.get_possible_values().iter().map(|v|v.get_name().to_string()).collect();
        serde_json::json!({"flags":flags,"destination":a.get_id().as_str(),"required":a.is_required_set(),"kind":if takes_value {"value"}else{"flag"},"choices":if possible.is_empty(){serde_json::Value::Null}else{serde_json::json!(possible)},"default":default,"metavar":null,"help":a.get_help().map(|s|s.to_string()).unwrap_or_default()})
    }).collect();
    output.push(serde_json::json!({"path":path,"summary":command.get_about().map(|s|s.to_string()).unwrap_or_default(),"options":options}));
    for child in command
        .get_subcommands()
        .filter(|c| !c.is_hide_set() && c.get_name() != "help")
    {
        let mut child_path = path.to_vec();
        child_path.push(child.get_name().to_string());
        command_docs(child, &child_path, output);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn clap_contract_is_valid() {
        Cli::command().debug_assert();
    }
    #[test]
    fn rejects_invalid_intervals_and_limits() {
        for value in ["0", "-1", "NaN", "inf"] {
            assert!(Cli::try_parse_from(["wifimap", "walk", "--interval", value]).is_err());
        }
        assert!(Cli::try_parse_from(["wifimap", "list", "--limit", "-1"]).is_err());
    }
    #[test]
    fn accepts_global_flags_after_subcommand_and_negative_floor() {
        let args = Cli::try_parse_from([
            "wifimap",
            "rooms",
            "add",
            "--location",
            "HOME",
            "--name",
            "CELLAR",
            "--floor",
            "-1",
            "--db",
            "x.db",
        ])
        .unwrap();
        assert_eq!(args.db, Some(PathBuf::from("x.db")));
    }
    #[test]
    fn documentation_comes_from_command_tree() {
        let doc = capabilities();
        let commands = doc["cli"]["commands"].as_array().unwrap();
        assert!(
            commands
                .iter()
                .any(|c| c["path"] == serde_json::json!(["rooms", "add"]))
        );
        assert!(
            !commands
                .iter()
                .any(|c| c["path"] == serde_json::json!(["capabilities"]))
        );
    }
}
