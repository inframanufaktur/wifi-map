//! WiFi-bound throughput and independent idle, load, and recovery probes.

use super::path::{PathSample, PathTracker, gateway_on_interface, probe_path};
use super::process::run_command;
use super::radio::{read_network_identity_with_cancel, wifi_interface};
use super::{CancelToken, MeasureError, Result};
use crate::model::Speed;
use crate::path_diagnostics::{LatencyPhase, LoadLatency};
use std::thread;
use std::time::{Duration, Instant};

pub fn run_speedtest() -> Result<Speed> {
    run_speedtest_with_cancel(&CancelToken::default())
}

pub fn run_speedtest_with_cancel(cancel: &CancelToken) -> Result<Speed> {
    cancel.check()?;
    let interface = wifi_interface(cancel)
        .ok_or_else(|| MeasureError::NoWifi("no WiFi interface available".into()))?;
    run_speedtest_on_interface(&interface, cancel)
}

fn run_speedtest_on_interface(interface: &str, cancel: &CancelToken) -> Result<Speed> {
    let output = run_command(
        "speedtest",
        &[
            "--format=json",
            "--accept-license",
            "--accept-gdpr",
            "--interface",
            interface,
        ],
        Duration::from_secs(120),
        cancel,
    )
    .map_err(|error| match error {
        MeasureError::Cancelled | MeasureError::SpeedtestUnavailable => error,
        _ => MeasureError::SpeedtestFailed(error.to_string()),
    })?;
    if !output.status.success() {
        return Err(MeasureError::SpeedtestFailed(
            output.stderr.trim().to_string(),
        ));
    }
    parse_speedtest(&output.stdout)
}

/// Compare independent idle, speedtest-running, and recovery probe windows.
/// The load interval spans Ookla's complete run, including server selection.
pub fn run_speedtest_diagnostics(cancel: &CancelToken) -> (Result<Speed>, LoadLatency) {
    if let Err(error) = cancel.check() {
        return (Err(error), LoadLatency::default());
    }
    let Some(interface) = wifi_interface(cancel) else {
        let error = if cancel.is_cancelled() {
            MeasureError::Cancelled
        } else {
            MeasureError::NoWifi("no WiFi interface available".into())
        };
        return (Err(error), LoadLatency::default());
    };
    let epoch = Instant::now();
    let mut internet_target = "1.1.1.1".to_string();
    let mut probe_count = 0;
    let mut previous_gateway = None;
    let mut gateway_changed = false;
    let (result, mut report) = run_connection_load_measurement(
        cancel,
        || run_speedtest_on_interface(&interface, cancel),
        || {
            probe_count += 1;
            let gateway = gateway_on_interface(&interface, cancel);
            if previous_gateway
                .as_ref()
                .is_some_and(|previous| previous != &gateway)
            {
                gateway_changed = true;
            }
            previous_gateway = Some(gateway.clone());
            probe_path(
                Some(&interface),
                gateway,
                &mut internet_target,
                epoch,
                probe_count <= 5,
                cancel,
            )
        },
        || {
            read_network_identity_with_cancel(cancel)
                .ok()
                .and_then(|(ssid, _)| ssid)
        },
        Duration::from_secs(1),
    );
    if gateway_changed && !cancel.is_cancelled() {
        invalidate_gateway_comparison(&mut report);
    }
    (result, report)
}

fn invalidate_gateway_comparison(report: &mut LoadLatency) {
    report.gateway_changed = true;
    for phase in [&mut report.idle, &mut report.loaded, &mut report.recovery] {
        phase.path.gateway_rtt_ms = None;
        phase.path.gateway_p95_ms = None;
        phase.path.gateway_loss_pct = None;
        phase.path.gateway_max_outage_ms = None;
        phase.diagnostics.gateway_jitter_ms = None;
        phase.diagnostics.gateway_latest_ms = None;
    }
}

fn run_connection_load_measurement(
    cancel: &CancelToken,
    speedtest: impl FnOnce() -> Result<Speed> + Send,
    mut probe: impl FnMut() -> PathSample,
    mut identity: impl FnMut() -> Option<String>,
    interval: Duration,
) -> (Result<Speed>, LoadLatency) {
    let mut known_ssid = None;
    let mut connection_changed = false;
    let (result, mut report) = run_load_measurement(
        cancel,
        speedtest,
        || {
            if let Some(ssid) = identity() {
                connection_changed |= known_ssid.as_ref().is_some_and(|known| known != &ssid);
                known_ssid = Some(ssid);
            }
            probe()
        },
        interval,
    );
    if connection_changed && !cancel.is_cancelled() {
        report.connection_changed = true;
        report.loaded = LatencyPhase::default();
        report.recovery = LatencyPhase::default();
    }
    (result, report)
}

fn collect_latency_phase(
    probe: &mut impl FnMut() -> PathSample,
    interval: Duration,
    cancel: &CancelToken,
) -> LatencyPhase {
    let mut tracker = PathTracker::new(5, interval);
    for index in 0..5 {
        if cancel.is_cancelled() {
            break;
        }
        let started = Instant::now();
        tracker.record_sample(&probe());
        if index < 4 && !cancel.wait(interval.saturating_sub(started.elapsed())) {
            break;
        }
    }
    tracker.phase()
}

fn run_load_measurement(
    cancel: &CancelToken,
    speedtest: impl FnOnce() -> Result<Speed> + Send,
    mut probe: impl FnMut() -> PathSample,
    interval: Duration,
) -> (Result<Speed>, LoadLatency) {
    if cancel.is_cancelled() {
        return (Err(MeasureError::Cancelled), LoadLatency::default());
    }
    let idle = collect_latency_phase(&mut probe, interval, cancel);
    if cancel.is_cancelled() {
        return (Err(MeasureError::Cancelled), LoadLatency::default());
    }
    let (result, loaded) = thread::scope(|scope| {
        let speedtest = scope.spawn(speedtest);
        let mut tracker = PathTracker::new(120, interval);
        while !speedtest.is_finished() && !cancel.is_cancelled() {
            let started = Instant::now();
            let sample = probe();
            // A probe completed after the load stopped belongs to neither the
            // loaded window nor its independently collected recovery window.
            if speedtest.is_finished() || cancel.is_cancelled() {
                break;
            }
            tracker.record_sample(&sample);
            if !cancel.wait(interval.saturating_sub(started.elapsed())) {
                break;
            }
        }
        let result = speedtest.join().unwrap_or_else(|_| {
            Err(MeasureError::SpeedtestFailed(
                "worker stopped unexpectedly".into(),
            ))
        });
        (result, tracker.phase())
    });
    if cancel.is_cancelled() {
        return (Err(MeasureError::Cancelled), LoadLatency::default());
    }
    if result.is_err() {
        return (
            result,
            LoadLatency {
                idle,
                ..LoadLatency::default()
            },
        );
    }
    let recovery = collect_latency_phase(&mut probe, interval, cancel);
    if cancel.is_cancelled() {
        return (Err(MeasureError::Cancelled), LoadLatency::default());
    }
    (
        result,
        LoadLatency {
            idle,
            loaded,
            recovery,
            ..Default::default()
        },
    )
}

fn parse_speedtest(text: &str) -> Result<Speed> {
    let value: serde_json::Value = serde_json::from_str(text)
        .map_err(|_| MeasureError::SpeedtestFailed("invalid JSON".into()))?;
    let metric = |section: &str, field: &str| -> Result<f64> {
        value[section][field]
            .as_f64()
            .filter(|v| v.is_finite() && *v >= 0.)
            .ok_or_else(|| {
                MeasureError::SpeedtestFailed(format!("missing or invalid {section}.{field}"))
            })
    };
    let ping_ms = metric("ping", "latency")?;
    // Ookla's bandwidth is bytes/sec, so one Mbps equals 125,000 bytes/sec.
    let down_mbps = metric("download", "bandwidth")? / 125_000.;
    let up_mbps = metric("upload", "bandwidth")? / 125_000.;
    let name = value["server"]["name"]
        .as_str()
        .filter(|name| !name.is_empty());
    let id = match &value["server"]["id"] {
        serde_json::Value::Null => None,
        serde_json::Value::String(value) => Some(value.clone()),
        serde_json::Value::Number(value) => Some(value.to_string()),
        _ => None,
    };
    let server = match (name, id) {
        (Some(name), Some(id)) => Some(format!("{name} ({id})")),
        (Some(name), None) => Some(name.into()),
        (None, Some(id)) => Some(format!("id:{id}")),
        _ => None,
    };
    Ok(Speed {
        ping_ms: Some(ping_ms),
        down_mbps: Some(down_mbps),
        up_mbps: Some(up_mbps),
        server,
    })
}

#[cfg(test)]
#[path = "tests/throughput.rs"]
mod tests;
