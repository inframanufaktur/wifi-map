//! CSV interchange and per-place analysis shared by CLI and terminal reports.
use crate::model::{PathFields, Reading, Signal, Speed};
use crate::store::{Filter, Store};
use anyhow::{Context, Result, bail, ensure};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

pub const REQUIRED_FIELDS: &[&str] = &[
    "id",
    "ts",
    "spot_id",
    "room_id",
    "location_id",
    "location_name",
    "room_name",
    "spot_name",
    "floor",
    "outdoors",
    "ssid",
    "bssid",
    "rssi",
    "noise",
    "snr",
    "channel",
    "phy",
    "tx_rate",
    "ping_ms",
    "down_mbps",
    "up_mbps",
    "server",
    "note",
    "delta_rssi",
    "delta_snr",
    "delta_down_mbps",
    "delta_up_mbps",
];
pub const OPTIONAL_AP_FIELDS: &[&str] = &["ap_name"];
pub const OPTIONAL_PATH_FIELDS: &[&str] = &[
    "path_probe_count",
    "gateway_rtt_ms",
    "gateway_p95_ms",
    "gateway_loss_pct",
    "gateway_max_outage_ms",
    "internet_rtt_ms",
    "internet_p95_ms",
    "internet_loss_pct",
    "internet_max_outage_ms",
];
pub const OPTIONAL_WALK_FIELDS: &[&str] =
    &["walk_id", "walk_name", "walk_started_at", "walk_ended_at"];

#[derive(Debug, Clone, Copy)]
pub struct MetricSpec {
    pub key: &'static str,
    pub label: &'static str,
    pub unit: &'static str,
    pub higher_is_better: bool,
    pub decimals: usize,
}

macro_rules! metric {
    ($key:literal,$label:literal,$unit:literal,$higher:literal,$precision:literal) => {
        MetricSpec {
            key: $key,
            label: $label,
            unit: $unit,
            higher_is_better: $higher,
            decimals: $precision,
        }
    };
}
pub const METRICS: &[MetricSpec] = &[
    metric!("rssi", "RSSI", "dBm", true, 0),
    metric!("noise", "noise", "dBm", false, 0),
    metric!("snr", "SNR", "dB", true, 0),
    metric!("tx_rate", "TX rate", "Mbps", true, 0),
    metric!("ping_ms", "ping", "ms", false, 1),
    metric!("down_mbps", "down", "Mbps", true, 1),
    metric!("up_mbps", "up", "Mbps", true, 1),
    metric!("delta_rssi", "ΔRSSI", "dB", true, 0),
    metric!("delta_snr", "ΔSNR", "dB", true, 0),
    metric!("delta_down_mbps", "Δdown", "Mbps", true, 1),
    metric!("delta_up_mbps", "Δup", "Mbps", true, 1),
    metric!("gateway_rtt_ms", "gateway RTT", "ms", false, 1),
    metric!("gateway_p95_ms", "gateway p95", "ms", false, 1),
    metric!("gateway_loss_pct", "gateway loss", "%", false, 1),
    metric!("gateway_max_outage_ms", "gateway outage", "ms", false, 0),
    metric!("internet_rtt_ms", "internet RTT", "ms", false, 1),
    metric!("internet_p95_ms", "internet p95", "ms", false, 1),
    metric!("internet_loss_pct", "internet loss", "%", false, 1),
    metric!("internet_max_outage_ms", "internet outage", "ms", false, 0),
];
pub const COMPARISON_METRICS: &[&str] = &[
    "rssi",
    "noise",
    "snr",
    "tx_rate",
    "ping_ms",
    "down_mbps",
    "up_mbps",
    "gateway_rtt_ms",
    "gateway_p95_ms",
    "gateway_loss_pct",
    "gateway_max_outage_ms",
    "internet_rtt_ms",
    "internet_p95_ms",
    "internet_loss_pct",
    "internet_max_outage_ms",
];

pub fn metric_spec(key: &str) -> Result<&'static MetricSpec> {
    METRICS
        .iter()
        .find(|spec| spec.key == key)
        .with_context(|| format!("unknown metric: {key:?}"))
}

pub fn reading_metric(reading: &Reading, key: &str) -> Option<f64> {
    match key {
        "rssi" => reading.signal.rssi,
        "noise" => reading.signal.noise,
        "snr" => reading.signal.snr,
        "tx_rate" => reading
            .signal
            .tx_rate
            .as_deref()
            .and_then(|v| v.parse().ok()),
        "ping_ms" => reading.speed.ping_ms,
        "down_mbps" => reading.speed.down_mbps,
        "up_mbps" => reading.speed.up_mbps,
        "delta_rssi" => reading.delta_rssi,
        "delta_snr" => reading.delta_snr,
        "delta_down_mbps" => reading.delta_down_mbps,
        "delta_up_mbps" => reading.delta_up_mbps,
        "gateway_rtt_ms" => reading.path.gateway_rtt_ms,
        "gateway_p95_ms" => reading.path.gateway_p95_ms,
        "gateway_loss_pct" => reading.path.gateway_loss_pct,
        "gateway_max_outage_ms" => reading.path.gateway_max_outage_ms.map(|v| v as f64),
        "internet_rtt_ms" => reading.path.internet_rtt_ms,
        "internet_p95_ms" => reading.path.internet_p95_ms,
        "internet_loss_pct" => reading.path.internet_loss_pct,
        "internet_max_outage_ms" => reading.path.internet_max_outage_ms.map(|v| v as f64),
        _ => None,
    }
    .filter(|v| v.is_finite())
}

pub fn load_csv(path: impl AsRef<Path>) -> Result<Vec<Reading>> {
    let path = path.as_ref();
    let data = std::fs::read_to_string(path)
        .with_context(|| format!("cannot read CSV {}", path.display()))?;
    let mut reader = csv::ReaderBuilder::new()
        .flexible(false)
        .from_reader(data.as_bytes());
    let headers = reader.headers().context("cannot read CSV headers")?.clone();
    let missing = REQUIRED_FIELDS
        .iter()
        .filter(|field| !headers.iter().any(|h| h == **field))
        .copied()
        .collect::<Vec<_>>();
    ensure!(
        missing.is_empty(),
        "CSV is missing required fields: {}",
        missing.join(", ")
    );
    let mut readings = Vec::new();
    for (index, record) in reader.records().enumerate() {
        let row = index + 2;
        let record = record.with_context(|| format!("cannot parse CSV row {row}"))?;
        let fields = headers
            .iter()
            .zip(record.iter())
            .collect::<BTreeMap<_, _>>();
        let text = |key: &str| fields.get(key).copied().unwrap_or_default();
        let optional = |key: &str| {
            let value = text(key);
            (!value.is_empty()).then(|| value.to_owned())
        };
        let number = |key: &str| parse_number(text(key), key, row);
        let integer = |key: &str| parse_integer(text(key), key, row);
        let outdoors = integer("outdoors")?.unwrap_or(0);
        ensure!(
            matches!(outdoors, 0 | 1),
            "row {row} field outdoors must be 0 or 1"
        );
        number("tx_rate")?;
        readings.push(Reading {
            id: integer("id")?.unwrap_or(0),
            ts: text("ts").into(),
            spot_id: integer("spot_id")?.unwrap_or(0),
            ssid_id: integer("ssid_id")?,
            room_id: integer("room_id")?.unwrap_or(0),
            location_id: integer("location_id")?.unwrap_or(0),
            location: text("location_name").into(),
            room: text("room_name").into(),
            spot: text("spot_name").into(),
            floor: integer("floor")?.unwrap_or(0),
            outdoors: outdoors == 1,
            signal: Signal {
                ssid: optional("ssid"),
                bssid: optional("bssid"),
                rssi: number("rssi")?,
                noise: number("noise")?,
                snr: number("snr")?,
                channel: optional("channel"),
                phy: optional("phy"),
                tx_rate: optional("tx_rate"),
                ..Default::default()
            },
            speed: Speed {
                ping_ms: number("ping_ms")?,
                down_mbps: number("down_mbps")?,
                up_mbps: number("up_mbps")?,
                server: optional("server"),
            },
            path: PathFields {
                path_probe_count: integer("path_probe_count")?,
                gateway_rtt_ms: number("gateway_rtt_ms")?,
                gateway_p95_ms: number("gateway_p95_ms")?,
                gateway_loss_pct: number("gateway_loss_pct")?,
                gateway_max_outage_ms: integer("gateway_max_outage_ms")?,
                internet_rtt_ms: number("internet_rtt_ms")?,
                internet_p95_ms: number("internet_p95_ms")?,
                internet_loss_pct: number("internet_loss_pct")?,
                internet_max_outage_ms: integer("internet_max_outage_ms")?,
            },
            note: optional("note"),
            ap_name: optional("ap_name"),
            walk_id: integer("walk_id")?,
            walk_name: optional("walk_name"),
            walk_started_at: optional("walk_started_at"),
            walk_ended_at: optional("walk_ended_at"),
            delta_rssi: number("delta_rssi")?,
            delta_snr: number("delta_snr")?,
            delta_down_mbps: number("delta_down_mbps")?,
            delta_up_mbps: number("delta_up_mbps")?,
        });
    }
    Ok(readings)
}

fn parse_number(value: &str, field: &str, row: usize) -> Result<Option<f64>> {
    if value.is_empty() {
        return Ok(None);
    }
    let number = value
        .parse::<f64>()
        .with_context(|| format!("row {row} field {field} is not numeric: {value:?}"))?;
    ensure!(number.is_finite(), "row {row} field {field} must be finite");
    Ok(Some(number))
}
fn parse_integer(value: &str, field: &str, row: usize) -> Result<Option<i64>> {
    if value.is_empty() {
        return Ok(None);
    }
    if let Ok(integer) = value.parse::<i64>() {
        return Ok(Some(integer));
    }
    let number = parse_number(value, field, row)?.unwrap_or(0.0);
    ensure!(
        number.fract() == 0.0 && number >= i64::MIN as f64 && number < (i64::MAX as f64),
        "row {row} field {field} is not an integer: {value:?}"
    );
    Ok(Some(number as i64))
}

pub fn export_csv(path: impl AsRef<Path>, readings: &[Reading]) -> Result<()> {
    let fields = REQUIRED_FIELDS
        .iter()
        .chain(OPTIONAL_AP_FIELDS)
        .chain(OPTIONAL_PATH_FIELDS)
        .chain(OPTIONAL_WALK_FIELDS)
        .copied()
        .collect::<Vec<_>>();
    let mut writer = csv::Writer::from_path(path.as_ref())
        .with_context(|| format!("cannot write CSV {}", path.as_ref().display()))?;
    writer.write_record(&fields)?;
    for r in readings {
        writer.write_record(fields.iter().map(|field| csv_value(r, field)))?;
    }
    writer.flush()?;
    Ok(())
}

fn csv_value(r: &Reading, field: &str) -> String {
    fn optional<T: std::fmt::Display>(value: Option<T>) -> String {
        value.map(|v| v.to_string()).unwrap_or_default()
    }
    match field {
        "id" => r.id.to_string(),
        "ts" => r.ts.clone(),
        "spot_id" => r.spot_id.to_string(),
        "room_id" => r.room_id.to_string(),
        "location_id" => r.location_id.to_string(),
        "location_name" => r.location.clone(),
        "room_name" => r.room.clone(),
        "spot_name" => r.spot.clone(),
        "floor" => r.floor.to_string(),
        "outdoors" => i32::from(r.outdoors).to_string(),
        "ssid" => optional(r.signal.ssid.as_deref()),
        "bssid" => optional(r.signal.bssid.as_deref()),
        "channel" => optional(r.signal.channel.as_deref()),
        "phy" => optional(r.signal.phy.as_deref()),
        "tx_rate" => optional(r.signal.tx_rate.as_deref()),
        "server" => optional(r.speed.server.as_deref()),
        "note" => optional(r.note.as_deref()),
        "ap_name" => optional(r.ap_name.as_deref()),
        "walk_id" => optional(r.walk_id),
        "walk_name" => optional(r.walk_name.as_deref()),
        "walk_started_at" => optional(r.walk_started_at.as_deref()),
        "walk_ended_at" => optional(r.walk_ended_at.as_deref()),
        "path_probe_count" => optional(r.path.path_probe_count),
        key => optional(reading_metric(r, key)),
    }
}

pub fn load_db(path: impl AsRef<Path>) -> Result<Vec<Reading>> {
    let readings = Store::open_readonly(path)?.list_readings(&Filter::default())?;
    for (index, reading) in readings.iter().enumerate() {
        let row = index + 1;
        for (field, value) in [
            ("rssi", reading.signal.rssi),
            ("noise", reading.signal.noise),
            ("snr", reading.signal.snr),
            ("ping_ms", reading.speed.ping_ms),
            ("down_mbps", reading.speed.down_mbps),
            ("up_mbps", reading.speed.up_mbps),
            ("delta_rssi", reading.delta_rssi),
            ("delta_snr", reading.delta_snr),
            ("delta_down_mbps", reading.delta_down_mbps),
            ("delta_up_mbps", reading.delta_up_mbps),
            ("gateway_rtt_ms", reading.path.gateway_rtt_ms),
            ("gateway_p95_ms", reading.path.gateway_p95_ms),
            ("gateway_loss_pct", reading.path.gateway_loss_pct),
            ("internet_rtt_ms", reading.path.internet_rtt_ms),
            ("internet_p95_ms", reading.path.internet_p95_ms),
            ("internet_loss_pct", reading.path.internet_loss_pct),
        ] {
            ensure!(
                value.is_none_or(f64::is_finite),
                "row {row} field {field} must be finite"
            );
        }
        if let Some(tx) = reading.signal.tx_rate.as_deref() {
            parse_number(tx, "tx_rate", row)?;
        }
    }
    Ok(readings)
}

#[derive(Debug, Clone)]
pub struct ScopeOption {
    pub kind: String,
    pub value: Option<String>,
    pub label: String,
    pub count: usize,
}

pub fn scope_options(readings: &[Reading], kind: &str) -> Result<Vec<ScopeOption>> {
    ensure!(
        matches!(kind, "location" | "ssid"),
        "unknown scope kind: {kind:?}"
    );
    let mut options = Vec::<ScopeOption>::new();
    for r in readings {
        let value = if kind == "location" {
            Some(r.location.clone())
        } else {
            r.signal.ssid.clone()
        };
        if let Some(option) = options.iter_mut().find(|o| o.value == value) {
            option.count += 1;
        } else {
            options.push(ScopeOption {
                kind: kind.into(),
                label: value.clone().unwrap_or_else(|| "Unknown".into()),
                value,
                count: 1,
            });
        }
    }
    Ok(options)
}
pub fn filter_scope(readings: &[Reading], option: &ScopeOption) -> Result<Vec<Reading>> {
    ensure!(
        matches!(option.kind.as_str(), "location" | "ssid"),
        "unknown scope kind: {:?}",
        option.kind
    );
    Ok(readings
        .iter()
        .filter(|r| {
            if option.kind == "location" {
                Some(&r.location) == option.value.as_ref()
            } else {
                r.signal.ssid == option.value
            }
        })
        .cloned()
        .collect())
}

#[derive(Debug, Clone)]
pub struct AnalysisRow {
    pub reading: Reading,
    pub count: usize,
    pub latest_ts: String,
    pub metrics: BTreeMap<String, Option<f64>>,
    pub readings: Vec<Reading>,
}
impl AnalysisRow {
    pub fn metric(&self, key: &str) -> Option<f64> {
        self.metrics.get(key).copied().flatten()
    }
    pub fn available(&self, key: &str) -> usize {
        self.readings
            .iter()
            .filter(|r| reading_metric(r, key).is_some())
            .count()
    }
    /// A count label, not a statistical confidence interval.
    pub fn confidence(&self, key: &str) -> &'static str {
        match self.available(key) {
            0 => "missing",
            1 | 2 => "limited",
            _ => "supported",
        }
    }
}

fn median(mut values: Vec<f64>) -> Option<f64> {
    values.retain(|v| v.is_finite());
    if values.is_empty() {
        return None;
    }
    values.sort_by(f64::total_cmp);
    let mid = values.len() / 2;
    Some(if values.len().is_multiple_of(2) {
        values[mid - 1] / 2.0 + values[mid] / 2.0
    } else {
        values[mid]
    })
}

fn aggregate(readings: &[Reading]) -> AnalysisRow {
    let mut reading = readings[0].clone();
    let common = |getter: fn(&Reading) -> Option<&String>| {
        let value = getter(&readings[0]);
        if readings.iter().all(|r| getter(r) == value) {
            value.cloned()
        } else {
            Some("Mixed".into())
        }
    };
    reading.signal.ssid = common(|r| r.signal.ssid.as_ref());
    reading.signal.bssid = common(|r| r.signal.bssid.as_ref());
    reading.signal.channel = common(|r| r.signal.channel.as_ref());
    reading.signal.phy = common(|r| r.signal.phy.as_ref());
    reading.speed.server = common(|r| r.speed.server.as_ref());
    reading.note = common(|r| r.note.as_ref());
    reading.ap_name = common(|r| r.ap_name.as_ref());
    let metrics = METRICS
        .iter()
        .map(|spec| {
            (
                spec.key.to_owned(),
                median(
                    readings
                        .iter()
                        .filter_map(|r| reading_metric(r, spec.key))
                        .collect(),
                ),
            )
        })
        .collect::<BTreeMap<_, _>>();
    // Each metric aggregates only the readings that actually measured it.
    reading.speed.ping_ms = metrics["ping_ms"];
    reading.speed.down_mbps = metrics["down_mbps"];
    reading.speed.up_mbps = metrics["up_mbps"];
    reading.signal.tx_rate = metrics["tx_rate"].map(|v| v.to_string());
    reading.signal.rssi = metrics["rssi"];
    reading.signal.noise = metrics["noise"];
    reading.signal.snr = metrics["snr"];
    reading.path.gateway_rtt_ms = metrics["gateway_rtt_ms"];
    reading.path.gateway_p95_ms = metrics["gateway_p95_ms"];
    reading.path.gateway_loss_pct = metrics["gateway_loss_pct"];
    reading.path.gateway_max_outage_ms = metrics["gateway_max_outage_ms"].map(|v| v.round() as i64);
    reading.path.internet_rtt_ms = metrics["internet_rtt_ms"];
    reading.path.internet_p95_ms = metrics["internet_p95_ms"];
    reading.path.internet_loss_pct = metrics["internet_loss_pct"];
    reading.path.internet_max_outage_ms =
        metrics["internet_max_outage_ms"].map(|v| v.round() as i64);
    reading.delta_rssi = metrics["delta_rssi"];
    reading.delta_snr = metrics["delta_snr"];
    reading.delta_down_mbps = metrics["delta_down_mbps"];
    reading.delta_up_mbps = metrics["delta_up_mbps"];
    let latest_ts = readings
        .iter()
        .map(|r| &r.ts)
        .max()
        .cloned()
        .unwrap_or_default();
    reading.ts = latest_ts.clone();
    AnalysisRow {
        reading,
        count: readings.len(),
        latest_ts,
        metrics,
        readings: readings.to_owned(),
    }
}

pub fn analysis_rows(readings: &[Reading], grouped: bool) -> Vec<AnalysisRow> {
    if !grouped {
        return readings
            .iter()
            .map(|r| aggregate(std::slice::from_ref(r)))
            .collect();
    }
    let mut groups = Vec::<Vec<Reading>>::new();
    let mut indices = BTreeMap::new();
    for reading in readings {
        let key = (
            &reading.location,
            &reading.room,
            &reading.spot,
            reading.floor,
            reading.outdoors,
        );
        let index = *indices.entry(key).or_insert_with(|| {
            groups.push(Vec::new());
            groups.len() - 1
        });
        groups[index].push(reading.clone());
    }
    groups.iter().map(|g| aggregate(g)).collect()
}

pub fn rank_rows(rows: &[AnalysisRow], key: &str, reverse: bool) -> Result<Vec<AnalysisRow>> {
    let spec = metric_spec(key)?;
    let descending = if reverse {
        spec.higher_is_better
    } else {
        !spec.higher_is_better
    };
    let mut rows = rows.to_owned();
    rows.sort_by(|a, b| match (a.metric(key), b.metric(key)) {
        (Some(x), Some(y)) => {
            let order = x
                .total_cmp(&y)
                .then_with(|| analysis_sort_key(a).cmp(&analysis_sort_key(b)));
            if descending { order.reverse() } else { order }
        }
        (Some(_), None) => std::cmp::Ordering::Less,
        (None, Some(_)) => std::cmp::Ordering::Greater,
        (None, None) => analysis_sort_key(a).cmp(&analysis_sort_key(b)),
    });
    Ok(rows)
}
fn analysis_sort_key(row: &AnalysisRow) -> (&str, &str, &str, &str) {
    (
        &row.reading.location,
        &row.reading.room,
        &row.reading.spot,
        &row.latest_ts,
    )
}

#[derive(Debug, Clone, Default)]
pub struct MetricSummary {
    pub available: usize,
    pub missing: usize,
    pub minimum: Option<f64>,
    pub median: Option<f64>,
    pub maximum: Option<f64>,
}
pub fn metric_summary(rows: &[AnalysisRow], key: &str) -> Result<MetricSummary> {
    metric_spec(key)?;
    let values = rows
        .iter()
        .filter_map(|r| r.metric(key))
        .collect::<Vec<_>>();
    Ok(MetricSummary {
        available: values.len(),
        missing: rows.len() - values.len(),
        minimum: values.iter().copied().min_by(f64::total_cmp),
        median: median(values.clone()),
        maximum: values.iter().copied().max_by(f64::total_cmp),
    })
}

#[derive(Debug, Clone)]
pub struct WalkOption {
    pub walk_id: i64,
    pub name: String,
    pub location_id: Option<i64>,
    pub location_name: String,
    pub started_at: Option<String>,
    pub ended_at: Option<String>,
    pub reading_count: usize,
    pub spot_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct PlaceKey {
    pub spot_id: Option<i64>,
    pub location_name: String,
    pub room_name: String,
    pub floor: i64,
    pub spot_name: String,
}

#[derive(Debug, Clone)]
pub struct ComparisonRow {
    pub place: PlaceKey,
    pub status: String,
    pub before: Option<AnalysisRow>,
    pub after: Option<AnalysisRow>,
}
impl ComparisonRow {
    pub fn confidence(&self, key: &str) -> &'static str {
        match (&self.before, &self.after) {
            (Some(before), Some(after))
                if before.available(key) > 0 && after.available(key) > 0 =>
            {
                if before.available(key) >= 3 && after.available(key) >= 3 {
                    "supported"
                } else {
                    "limited"
                }
            }
            _ => "missing",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
enum SpotIdentity {
    Id(i64),
    Place(String, String, i64, String),
}
fn spot_identity(r: &Reading) -> SpotIdentity {
    if r.spot_id != 0 {
        SpotIdentity::Id(r.spot_id)
    } else {
        SpotIdentity::Place(r.location.clone(), r.room.clone(), r.floor, r.spot.clone())
    }
}
fn place_key(r: &Reading) -> PlaceKey {
    PlaceKey {
        spot_id: (r.spot_id != 0).then_some(r.spot_id),
        location_name: r.location.clone(),
        room_name: r.room.clone(),
        floor: r.floor,
        spot_name: r.spot.clone(),
    }
}

fn walk_location(readings: &[Reading], id: i64) -> Result<(Option<i64>, String)> {
    let ids = readings
        .iter()
        .filter_map(|r| (r.location_id != 0).then_some(r.location_id))
        .collect::<BTreeSet<_>>();
    let names = readings
        .iter()
        .map(|r| r.location.clone())
        .collect::<BTreeSet<_>>();
    ensure!(
        ids.len() <= 1 && names.len() <= 1,
        "walk {id} spans multiple locations"
    );
    Ok((
        ids.into_iter().next(),
        names.into_iter().next().unwrap_or_default(),
    ))
}
pub fn walk_options(readings: &[Reading]) -> Result<Vec<WalkOption>> {
    let mut groups = BTreeMap::<i64, Vec<Reading>>::new();
    for r in readings {
        if let (Some(id), Some(_)) = (r.walk_id, &r.walk_name) {
            groups.entry(id).or_default().push(r.clone());
        }
    }
    let mut options = Vec::new();
    for (id, group) in groups {
        let (location_id, location_name) = walk_location(&group, id)?;
        options.push(WalkOption {
            walk_id: id,
            name: group[0]
                .walk_name
                .clone()
                .unwrap_or_else(|| format!("Walk #{id}")),
            location_id,
            location_name,
            started_at: group.iter().filter_map(|r| r.walk_started_at.clone()).min(),
            ended_at: group.iter().filter_map(|r| r.walk_ended_at.clone()).max(),
            reading_count: group.len(),
            spot_count: group
                .iter()
                .map(spot_identity)
                .collect::<BTreeSet<_>>()
                .len(),
        });
    }
    options.sort_by(|a, b| {
        (&b.started_at, b.walk_id, &b.name).cmp(&(&a.started_at, a.walk_id, &a.name))
    });
    Ok(options)
}

pub fn compare_walks(
    readings: &[Reading],
    before_id: i64,
    after_id: i64,
) -> Result<Vec<ComparisonRow>> {
    ensure!(
        before_id != after_id,
        "before and after walks must be different"
    );
    let before = readings
        .iter()
        .filter(|r| r.walk_id == Some(before_id))
        .cloned()
        .collect::<Vec<_>>();
    let after = readings
        .iter()
        .filter(|r| r.walk_id == Some(after_id))
        .cloned()
        .collect::<Vec<_>>();
    ensure!(
        !before.is_empty(),
        "unknown or empty before walk: {before_id}"
    );
    ensure!(!after.is_empty(), "unknown or empty after walk: {after_id}");
    let a = walk_location(&before, before_id)?;
    let b = walk_location(&after, after_id)?;
    let same = match (a.0, b.0) {
        (Some(a), Some(b)) => a == b,
        _ => a.1 == b.1,
    };
    ensure!(same, "cannot compare walks from different locations");
    let group = |readings: &[Reading]| {
        let mut groups = BTreeMap::<SpotIdentity, Vec<Reading>>::new();
        for r in readings {
            groups.entry(spot_identity(r)).or_default().push(r.clone());
        }
        groups
            .into_iter()
            .map(|(id, group)| (id, aggregate(&group)))
            .collect::<BTreeMap<_, _>>()
    };
    let before = group(&before);
    let after = group(&after);
    let keys = before
        .keys()
        .chain(after.keys())
        .cloned()
        .collect::<BTreeSet<_>>();
    let mut rows = keys
        .into_iter()
        .map(|key| {
            let before = before.get(&key).cloned();
            let after = after.get(&key).cloned();
            let status = match (&before, &after) {
                (Some(_), Some(_)) => "matched",
                (Some(_), None) => "before_only",
                _ => "after_only",
            };
            let place = place_key(
                &before
                    .as_ref()
                    .or(after.as_ref())
                    .expect("spot union has a reading")
                    .reading,
            );
            ComparisonRow {
                place,
                status: status.into(),
                before,
                after,
            }
        })
        .collect::<Vec<_>>();
    rows.sort_by(|a, b| comparison_sort_key(a).cmp(&comparison_sort_key(b)));
    Ok(rows)
}

fn comparison_spec(key: &str) -> Result<&'static MetricSpec> {
    ensure!(
        COMPARISON_METRICS.contains(&key),
        "unknown comparison metric: {key:?}"
    );
    metric_spec(key)
}
pub fn comparison_delta(row: &ComparisonRow, key: &str) -> Result<Option<f64>> {
    comparison_spec(key)?;
    Ok(row
        .before
        .as_ref()
        .and_then(|r| r.metric(key))
        .zip(row.after.as_ref().and_then(|r| r.metric(key)))
        .map(|(before, after)| after - before))
}
pub fn comparison_direction(row: &ComparisonRow, key: &str) -> Result<&'static str> {
    let spec = comparison_spec(key)?;
    let Some(delta) = comparison_delta(row, key)? else {
        return Ok("missing");
    };
    let improvement = if spec.higher_is_better { delta } else { -delta };
    Ok(if improvement > 0.0 {
        "improved"
    } else if improvement < 0.0 {
        "worse"
    } else {
        "unchanged"
    })
}
fn comparison_sort_key(row: &ComparisonRow) -> (&str, i64, &str, &str, Option<i64>) {
    (
        &row.place.location_name,
        row.place.floor,
        &row.place.room_name,
        &row.place.spot_name,
        row.place.spot_id,
    )
}
pub fn rank_comparison_rows(
    rows: &[ComparisonRow],
    key: &str,
    reverse: bool,
) -> Result<Vec<ComparisonRow>> {
    let spec = comparison_spec(key)?;
    let categorize = |row: &ComparisonRow| {
        if row.status == "before_only" {
            0
        } else if row.status == "after_only" {
            2
        } else if comparison_delta(row, key).ok().flatten().is_some() {
            1
        } else {
            3
        }
    };
    let mut rows = rows.to_owned();
    rows.sort_by(|a, b| {
        let group = categorize(a).cmp(&categorize(b));
        if !group.is_eq() {
            return group;
        }
        if categorize(a) == 1 {
            let improvement = |row: &ComparisonRow| {
                let delta = comparison_delta(row, key)
                    .ok()
                    .flatten()
                    .unwrap_or_default();
                if spec.higher_is_better { delta } else { -delta }
            };
            let order = improvement(a).total_cmp(&improvement(b));
            let order = if reverse { order.reverse() } else { order };
            if !order.is_eq() {
                return order;
            }
        }
        comparison_sort_key(a).cmp(&comparison_sort_key(b))
    });
    Ok(rows)
}

#[derive(Debug, Clone, Default)]
pub struct ComparisonSummary {
    pub before_spots: usize,
    pub after_spots: usize,
    pub matched_spots: usize,
    pub before_only: usize,
    pub after_only: usize,
    pub comparable: usize,
    pub improved: usize,
    pub unchanged: usize,
    pub worse: usize,
    pub metric_missing: usize,
    pub before_median: Option<f64>,
    pub after_median: Option<f64>,
    pub median_delta: Option<f64>,
}
pub fn comparison_summary(rows: &[ComparisonRow], key: &str) -> Result<ComparisonSummary> {
    comparison_spec(key)?;
    let mut summary = ComparisonSummary::default();
    let mut before = Vec::new();
    let mut after = Vec::new();
    let mut deltas = Vec::new();
    for row in rows {
        summary.before_spots += usize::from(row.before.is_some());
        summary.after_spots += usize::from(row.after.is_some());
        match row.status.as_str() {
            "before_only" => summary.before_only += 1,
            "after_only" => summary.after_only += 1,
            "matched" => summary.matched_spots += 1,
            _ => bail!("unknown comparison status: {:?}", row.status),
        }
        if let Some(delta) = comparison_delta(row, key)? {
            summary.comparable += 1;
            match comparison_direction(row, key)? {
                "improved" => summary.improved += 1,
                "unchanged" => summary.unchanged += 1,
                "worse" => summary.worse += 1,
                _ => {}
            }
            before.push(
                row.before
                    .as_ref()
                    .and_then(|r| r.metric(key))
                    .unwrap_or_default(),
            );
            after.push(
                row.after
                    .as_ref()
                    .and_then(|r| r.metric(key))
                    .unwrap_or_default(),
            );
            deltas.push(delta);
        } else if row.status == "matched" {
            summary.metric_missing += 1;
        }
    }
    summary.before_median = median(before);
    summary.after_median = median(after);
    summary.median_delta = median(deltas);
    Ok(summary)
}
