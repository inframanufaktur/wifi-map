//! Additional session-only path and load measurements.

use crate::model::PathFields;

#[derive(Debug, Clone, Default)]
pub struct PathDiagnostics {
    pub gateway_jitter_ms: Option<f64>,
    pub internet_jitter_ms: Option<f64>,
    pub gateway_latest_ms: Option<f64>,
    pub internet_latest_ms: Option<f64>,
}

#[derive(Debug, Clone, Default)]
pub struct LatencyPhase {
    pub path: PathFields,
    pub diagnostics: PathDiagnostics,
}

#[derive(Debug, Clone, Default)]
pub struct LoadLatency {
    pub idle: LatencyPhase,
    pub loaded: LatencyPhase,
    pub recovery: LatencyPhase,
    pub gateway_changed: bool,
    pub connection_changed: bool,
}
