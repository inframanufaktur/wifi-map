use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct Signal {
    pub ssid: Option<String>,
    pub bssid: Option<String>,
    pub rssi: Option<f64>,
    pub noise: Option<f64>,
    pub snr: Option<f64>,
    pub channel: Option<String>,
    pub phy: Option<String>,
    pub tx_rate: Option<String>,
    pub mcs: Option<i64>,
    pub band: Option<String>,
    pub security: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct Speed {
    pub ping_ms: Option<f64>,
    pub down_mbps: Option<f64>,
    pub up_mbps: Option<f64>,
    pub server: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct PathFields {
    pub path_probe_count: Option<i64>,
    pub gateway_rtt_ms: Option<f64>,
    pub gateway_p95_ms: Option<f64>,
    pub gateway_loss_pct: Option<f64>,
    pub gateway_max_outage_ms: Option<i64>,
    pub internet_rtt_ms: Option<f64>,
    pub internet_p95_ms: Option<f64>,
    pub internet_loss_pct: Option<f64>,
    pub internet_max_outage_ms: Option<i64>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Reading {
    pub id: i64,
    pub ts: String,
    pub spot_id: i64,
    pub ssid_id: Option<i64>,
    pub location_id: i64,
    pub location: String,
    pub room_id: i64,
    pub room: String,
    pub floor: i64,
    pub outdoors: bool,
    pub spot: String,
    #[serde(flatten)]
    pub signal: Signal,
    #[serde(flatten)]
    pub speed: Speed,
    #[serde(flatten)]
    pub path: PathFields,
    pub note: Option<String>,
    pub ap_name: Option<String>,
    pub walk_id: Option<i64>,
    pub walk_name: Option<String>,
    pub walk_started_at: Option<String>,
    pub walk_ended_at: Option<String>,
    pub delta_rssi: Option<f64>,
    pub delta_snr: Option<f64>,
    pub delta_down_mbps: Option<f64>,
    pub delta_up_mbps: Option<f64>,
}

pub fn timestamp() -> String {
    chrono::Utc::now().to_rfc3339()
}

pub fn display<T: std::fmt::Display>(value: Option<T>) -> String {
    value.map(|v| v.to_string()).unwrap_or_else(|| "-".into())
}
