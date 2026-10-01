//! WiFi interface counter sampling and observed traffic rates.

use super::CancelToken;
use super::process::run_command;
use super::radio::wifi_interface;
use std::time::{Duration, Instant};

pub fn parse_netstat_ib(text: &str, interface: &str) -> Option<(u64, u64)> {
    for line in text.lines() {
        let tokens: Vec<_> = line.split_whitespace().collect();
        if tokens.first() != Some(&interface) || !line.contains("<Link") {
            continue;
        }
        if tokens.len() < 8 {
            return None;
        }
        return Some((
            tokens[tokens.len() - 5].parse().ok()?,
            tokens[tokens.len() - 2].parse().ok()?,
        ));
    }
    None
}

fn interface_counters(interface: &str, cancel: &CancelToken) -> Option<(u64, u64)> {
    let output = run_command(
        "/usr/sbin/netstat",
        &["-I", interface, "-b"],
        Duration::from_secs(2),
        cancel,
    )
    .ok()?;
    output
        .status
        .success()
        .then(|| parse_netstat_ib(&output.stdout, interface))
        .flatten()
}

pub struct TrafficSampler {
    interface: Option<String>,
    last: Option<((u64, u64), Duration)>,
    epoch: Instant,
}

impl Default for TrafficSampler {
    fn default() -> Self {
        Self {
            interface: None,
            last: None,
            epoch: Instant::now(),
        }
    }
}

impl TrafficSampler {
    pub fn sample(&mut self) -> Option<(f64, f64)> {
        self.sample_with_cancel(&CancelToken::default())
    }

    pub fn sample_with_cancel(&mut self, cancel: &CancelToken) -> Option<(f64, f64)> {
        if self.interface.is_none() {
            self.interface = wifi_interface(cancel);
        }
        let counters = self
            .interface
            .as_deref()
            .and_then(|interface| interface_counters(interface, cancel));
        if counters.is_none() {
            self.interface = None;
        }
        self.record(counters, self.epoch.elapsed())
    }

    pub fn record(
        &mut self,
        counters: Option<(u64, u64)>,
        observed_at: Duration,
    ) -> Option<(f64, f64)> {
        let previous = self.last.take();
        let counters = counters?;
        self.last = Some((counters, observed_at));
        let (previous, time) = previous?;
        let elapsed = observed_at.checked_sub(time)?.as_secs_f64();
        if elapsed <= 0. {
            return None;
        }
        Some((
            counters.0.checked_sub(previous.0)? as f64 / 125_000. / elapsed,
            counters.1.checked_sub(previous.1)? as f64 / 125_000. / elapsed,
        ))
    }
}

#[cfg(test)]
#[path = "tests/traffic.rs"]
mod tests;
