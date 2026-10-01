//! WiFi-scoped path probes, rolling summaries, and cancellable workers.

use super::CancelToken;
use super::process::run_command;
use super::radio::wifi_interface;
use crate::model::PathFields;
use crate::path_diagnostics::{LatencyPhase, PathDiagnostics};
use std::collections::VecDeque;
use std::net::IpAddr;
use std::sync::mpsc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

pub fn gateway() -> Option<String> {
    gateway_with_cancel(&CancelToken::default())
}

pub fn gateway_with_cancel(cancel: &CancelToken) -> Option<String> {
    gateway_on_interface(&wifi_interface(cancel)?, cancel)
}

pub(super) fn gateway_on_interface(interface: &str, cancel: &CancelToken) -> Option<String> {
    let output = run_command(
        "/sbin/route",
        &["-n", "get", "-ifscope", interface, "default"],
        Duration::from_secs(2),
        cancel,
    )
    .ok()?;
    output
        .status
        .success()
        .then(|| parse_route_field(&output.stdout, "interface"))
        .flatten()
        .filter(|route_interface| route_interface == interface)?;
    parse_route_field(&output.stdout, "gateway").filter(|gateway| gateway.parse::<IpAddr>().is_ok())
}

fn parse_route_field(text: &str, field: &str) -> Option<String> {
    text.lines().find_map(|line| {
        let (label, value) = line.trim().split_once(':')?;
        (label == field).then(|| value.trim().to_string())
    })
}

pub fn parse_ping_rtt(text: &str) -> Option<f64> {
    for (index, _) in text.match_indices("time") {
        let rest = text[index + 4..].trim_start();
        let operator = rest.chars().next()?;
        if !matches!(operator, '=' | '<') {
            continue;
        }
        let number = rest[1..].trim_start();
        let end = number
            .find(|c: char| !c.is_ascii_digit() && c != '.')
            .unwrap_or(number.len());
        let value: f64 = number[..end].parse().ok()?;
        if number[end..].trim_start().starts_with("ms") && value.is_finite() && value >= 0. {
            return Some(if operator == '<' {
                value.max(1.)
            } else {
                value
            });
        }
    }
    None
}

pub fn ping_once(target: &str, cancel: &CancelToken) -> Option<f64> {
    target.parse::<IpAddr>().ok()?;
    ping_on_interface(target, &wifi_interface(cancel)?, cancel)
}

fn ping_on_interface(target: &str, interface: &str, cancel: &CancelToken) -> Option<f64> {
    target.parse::<IpAddr>().ok()?;
    let wait = if cfg!(target_os = "macos") {
        "1000"
    } else {
        "1"
    };
    let output = run_command(
        "/sbin/ping",
        &["-n", "-c", "1", "-W", wait, "-b", interface, target],
        Duration::from_millis(1500),
        cancel,
    )
    .ok()?;
    output
        .status
        .success()
        .then(|| parse_ping_rtt(&output.stdout))
        .flatten()
}

pub(super) fn probe_path(
    interface: Option<&str>,
    gateway: Option<String>,
    internet_target: &mut String,
    epoch: Instant,
    allow_fallback: bool,
    cancel: &CancelToken,
) -> PathSample {
    let Some(interface) = interface else {
        return PathSample {
            gateway_target: None,
            gateway_ms: None,
            internet_target: String::new(),
            internet_ms: None,
            primary_failed: false,
            observed_at: epoch.elapsed(),
        };
    };
    thread::scope(|scope| {
        let probe_gateway = gateway.clone();
        let gateway_probe = scope.spawn(move || {
            probe_gateway
                .as_deref()
                .and_then(|target| ping_on_interface(target, interface, cancel))
        });
        let mut internet_ms = ping_on_interface(internet_target, interface, cancel);
        let primary_failed = internet_ms.is_none() && internet_target == "1.1.1.1";
        if allow_fallback
            && primary_failed
            && !cancel.is_cancelled()
            && let Some(reply) = ping_on_interface("8.8.8.8", interface, cancel)
        {
            // Keep a successful fallback target for this worker's remaining
            // samples instead of alternating different hosts' RTTs.
            internet_ms = Some(reply);
            *internet_target = "8.8.8.8".into();
        }
        let gateway_ms = gateway_probe.join().unwrap_or(None);
        PathSample {
            gateway_target: gateway,
            gateway_ms,
            internet_target: internet_target.clone(),
            internet_ms,
            primary_failed,
            observed_at: epoch.elapsed(),
        }
    })
}

#[derive(Clone, Debug)]
pub struct PathSample {
    pub gateway_target: Option<String>,
    pub gateway_ms: Option<f64>,
    pub internet_target: String,
    pub internet_ms: Option<f64>,
    pub primary_failed: bool,
    pub observed_at: Duration,
}

#[derive(Default)]
struct ProbeSummary {
    median: Option<f64>,
    p95: Option<f64>,
    loss: Option<f64>,
    outage_ms: Option<i64>,
}

struct ProbeWindow {
    samples: VecDeque<(Option<f64>, Duration)>,
    capacity: usize,
    interval: Duration,
}

impl ProbeWindow {
    fn new(capacity: usize, interval: Duration) -> Self {
        Self {
            samples: VecDeque::new(),
            capacity: capacity.max(1),
            interval,
        }
    }

    fn record(&mut self, value: Option<f64>, observed_at: Duration) {
        if self.samples.len() == self.capacity {
            self.samples.pop_front();
        }
        self.samples
            .push_back((value.filter(|v| v.is_finite() && *v >= 0.), observed_at));
    }

    fn summary(&self) -> ProbeSummary {
        if self.samples.is_empty() {
            return ProbeSummary::default();
        }
        let mut values: Vec<_> = self.samples.iter().filter_map(|(v, _)| *v).collect();
        values.sort_by(f64::total_cmp);
        let len = values.len();
        let median = if len == 0 {
            None
        } else if len % 2 == 0 {
            Some((values[len / 2 - 1] + values[len / 2]) / 2.)
        } else {
            Some(values[len / 2])
        };
        let p95 = if len == 0 {
            None
        } else {
            Some(values[(len * 95).div_ceil(100) - 1])
        };
        let (mut longest, mut streak, mut previous) = (Duration::ZERO, Duration::ZERO, None);
        for (value, observed_at) in &self.samples {
            let elapsed = previous
                .map(|time| observed_at.saturating_sub(time))
                .unwrap_or(self.interval)
                .max(self.interval);
            if value.is_none() {
                streak += elapsed;
            } else {
                streak = Duration::ZERO;
            }
            longest = longest.max(streak);
            previous = Some(*observed_at);
        }
        ProbeSummary {
            median,
            p95,
            loss: Some(100. * (self.samples.len() - len) as f64 / self.samples.len() as f64),
            outage_ms: Some((longest.as_secs_f64() * 1000.).round() as i64),
        }
    }
}

/// A spot-local rolling window. An unknown gateway is never counted as a loss.
pub struct PathTracker {
    gateway: ProbeWindow,
    internet: ProbeWindow,
    gateway_target: Option<String>,
    internet_target: Option<String>,
}

impl Default for PathTracker {
    fn default() -> Self {
        Self::new(60, Duration::from_secs(1))
    }
}

impl PathTracker {
    pub fn new(window_size: usize, interval: Duration) -> Self {
        Self {
            gateway: ProbeWindow::new(window_size, interval),
            internet: ProbeWindow::new(window_size, interval),
            gateway_target: None,
            internet_target: None,
        }
    }

    pub fn record(&mut self, gateway: Option<f64>, internet: Option<f64>, observed_at: Duration) {
        self.gateway.record(gateway, observed_at);
        self.internet.record(internet, observed_at);
    }

    pub fn record_sample(&mut self, sample: &PathSample) {
        if self.gateway_target != sample.gateway_target {
            self.gateway.samples.clear();
            self.gateway_target = sample.gateway_target.clone();
        }
        if self.internet_target.as_deref() != Some(&sample.internet_target) {
            self.internet.samples.clear();
            self.internet_target = Some(sample.internet_target.clone());
        }
        if sample.gateway_target.is_some() {
            self.gateway.record(sample.gateway_ms, sample.observed_at);
        }
        if !sample.internet_target.is_empty() {
            self.internet.record(sample.internet_ms, sample.observed_at);
        }
    }

    pub fn reset(&mut self) {
        self.gateway.samples.clear();
        self.internet.samples.clear();
        self.gateway_target = None;
        self.internet_target = None;
    }

    pub fn diagnostics(&self) -> PathDiagnostics {
        let jitter = |window: &ProbeWindow| {
            let mut total_delta = 0.;
            let mut pair_count = 0;
            for ((previous, _), (current, _)) in
                window.samples.iter().zip(window.samples.iter().skip(1))
            {
                if let Some((previous, current)) = previous.zip(*current) {
                    total_delta += (current - previous).abs();
                    pair_count += 1;
                }
            }
            (pair_count > 0).then(|| total_delta / pair_count as f64)
        };
        PathDiagnostics {
            gateway_jitter_ms: jitter(&self.gateway),
            internet_jitter_ms: jitter(&self.internet),
            gateway_latest_ms: self.gateway.samples.back().and_then(|(value, _)| *value),
            internet_latest_ms: self.internet.samples.back().and_then(|(value, _)| *value),
        }
    }

    pub(super) fn phase(&self) -> LatencyPhase {
        LatencyPhase {
            path: self.fields(),
            diagnostics: self.diagnostics(),
        }
    }

    pub fn fields(&self) -> PathFields {
        let gateway = self.gateway.summary();
        let internet = self.internet.summary();
        let count = self.gateway.samples.len().max(self.internet.samples.len());
        PathFields {
            path_probe_count: (count > 0).then_some(count as i64),
            gateway_rtt_ms: gateway.median,
            gateway_p95_ms: gateway.p95,
            gateway_loss_pct: gateway.loss,
            gateway_max_outage_ms: gateway.outage_ms,
            internet_rtt_ms: internet.median,
            internet_p95_ms: internet.p95,
            internet_loss_pct: internet.loss,
            internet_max_outage_ms: internet.outage_ms,
        }
    }
}

pub struct PathWorker {
    cancel: CancelToken,
    receiver: mpsc::Receiver<PathSample>,
    thread: Option<JoinHandle<()>>,
    epoch: Instant,
}

impl PathWorker {
    pub fn start(gateway: Option<String>) -> Self {
        Self::start_with_cancel(gateway, &CancelToken::default())
    }

    pub fn start_with_cancel(_gateway: Option<String>, parent: &CancelToken) -> Self {
        let cancel = parent.child();
        let worker_cancel = cancel.clone();
        let (sender, receiver) = mpsc::sync_channel(120);
        let epoch = Instant::now();
        let thread = thread::spawn(move || {
            let mut interface = wifi_interface(&worker_cancel);
            let mut internet_target = "1.1.1.1".to_string();
            while !worker_cancel.is_cancelled() {
                let start = Instant::now();
                if interface.is_none() {
                    interface = wifi_interface(&worker_cancel);
                }
                // Resolve the WiFi-scoped route each cycle, so moving between
                // networks cannot keep pinging the previous gateway.
                let gateway = interface
                    .as_deref()
                    .and_then(|interface| gateway_on_interface(interface, &worker_cancel));
                let sample = probe_path(
                    interface.as_deref(),
                    gateway,
                    &mut internet_target,
                    epoch,
                    true,
                    &worker_cancel,
                );
                if worker_cancel.is_cancelled() {
                    break;
                }
                match sender.try_send(sample) {
                    Ok(()) | Err(mpsc::TrySendError::Full(_)) => {}
                    Err(mpsc::TrySendError::Disconnected(_)) => break,
                }
                if !worker_cancel.wait(Duration::from_secs(1).saturating_sub(start.elapsed())) {
                    break;
                }
            }
        });
        Self {
            cancel,
            receiver,
            thread: Some(thread),
            epoch,
        }
    }

    pub fn try_recv(&self) -> Option<PathSample> {
        self.receiver.try_recv().ok()
    }

    /// The actual probe completion time, retained even while queued. The
    /// sample's elapsed timestamp is stamped after both path probes finish.
    pub fn sample_instant(&self, sample: &PathSample) -> Instant {
        self.epoch + sample.observed_at
    }
}

impl Drop for PathWorker {
    fn drop(&mut self) {
        self.cancel.cancel();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

#[cfg(test)]
#[path = "tests/path.rs"]
mod tests;
