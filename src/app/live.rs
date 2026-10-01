//! Current-connection identity, probe correlation, and live sampling.
use std::{
    sync::atomic::Ordering,
    thread,
    time::{Duration, Instant},
};

use crate::{
    measure::{self, CancelToken},
    model::Signal,
};

use super::{LiveSample, WalkApp, WorkerEvent};

impl WalkApp {
    pub(super) fn observe_association(&mut self, signal: &Signal, observed_at: Instant) {
        let (ssid, bssid) = known_association(signal);
        if let Some(ssid) = ssid
            && self
                .ssid_observed_at
                .is_none_or(|latest| latest <= observed_at)
        {
            if self
                .last_association
                .ssid
                .as_deref()
                .is_some_and(|old| old != ssid)
            {
                self.load_latency = None;
            }
            self.last_association.ssid = Some(ssid.into());
            self.ssid_observed_at = Some(observed_at);
        }
        if let Some(bssid) = bssid
            && self
                .bssid_observed_at
                .is_none_or(|latest| latest <= observed_at)
        {
            if self
                .last_association
                .bssid
                .as_ref()
                .is_some_and(|old| old != &bssid)
            {
                self.load_latency = None;
            }
            self.last_association.bssid = Some(bssid);
            self.bssid_observed_at = Some(observed_at);
        }
    }
}

fn known_association(signal: &Signal) -> (Option<&str>, Option<String>) {
    let ssid = signal.ssid.as_deref().map(str::trim).filter(|ssid| {
        !ssid.is_empty()
            && !["<hidden>", "<redacted>"]
                .iter()
                .any(|unknown| ssid.eq_ignore_ascii_case(unknown))
    });
    let bssid = signal
        .bssid
        .as_deref()
        .and_then(|bssid| crate::store::normalize_bssid(bssid).ok())
        .filter(|bssid| {
            bssid != "00:00:00:00:00:00"
                && u8::from_str_radix(&bssid[..2], 16).is_ok_and(|first| first & 1 == 0)
        });
    (ssid, bssid)
}

pub(super) fn association_changed(before: &Signal, after: &Signal) -> bool {
    let (before_ssid, before_bssid) = known_association(before);
    let (after_ssid, after_bssid) = known_association(after);
    before_ssid
        .zip(after_ssid)
        .is_some_and(|(before, after)| before != after)
        || before_bssid
            .zip(after_bssid)
            .is_some_and(|(before, after)| before != after)
}

fn retain_association(previous: &mut Signal, signal: &Signal) {
    let (ssid, bssid) = known_association(signal);
    if let Some(ssid) = ssid {
        previous.ssid = Some(ssid.into());
    }
    if bssid.is_some() {
        previous.bssid = bssid;
    }
}

pub(super) fn record_recent_probes(
    tracker: &mut measure::PathTracker,
    samples: impl IntoIterator<Item = (measure::PathSample, Instant)>,
    observed_at: Instant,
    boundary: Instant,
) -> Option<(Option<f64>, Option<f64>)> {
    let mut latest = None;
    for (sample, sampled_at) in samples {
        if sampled_at >= boundary
            && let Some(age) = observed_at.checked_duration_since(sampled_at)
        {
            tracker.record_sample(&sample);
            if age <= Duration::from_secs(2) {
                latest = Some((sample.gateway_ms, sample.internet_ms));
            }
        }
    }
    latest
}

struct LiveProbes {
    boundary: Instant,
    traffic: measure::TrafficSampler,
    tracker: measure::PathTracker,
    worker: measure::PathWorker,
}

impl LiveProbes {
    fn new(cancel: &CancelToken) -> Self {
        Self {
            boundary: Instant::now(),
            worker: measure::PathWorker::start_with_cancel(None, cancel),
            tracker: measure::PathTracker::default(),
            traffic: measure::TrafficSampler::default(),
        }
    }

    fn reset(&mut self, cancel: &CancelToken) {
        self.boundary = Instant::now();
        self.worker = measure::PathWorker::start_with_cancel(None, cancel);
        self.tracker.reset();
        self.traffic = measure::TrafficSampler::default();
    }

    fn drain(&self) -> Vec<(measure::PathSample, Instant)> {
        let mut samples = Vec::new();
        while let Some(sample) = self.worker.try_recv() {
            let sampled_at = self.worker.sample_instant(&sample);
            samples.push((sample, sampled_at));
        }
        samples
    }
}

pub(super) fn spawn_live(app: &WalkApp, interval: Duration) {
    let sender = app.sender.clone();
    let cancel = app.cancel.clone();
    let generation = app.generation.clone();
    thread::spawn(move || {
        let mut probes = LiveProbes::new(&cancel);
        let mut previous_generation = generation.load(Ordering::Relaxed);
        let mut known_identity = Signal::default();
        while !cancel.is_cancelled() {
            let current_generation = generation.load(Ordering::Relaxed);
            if previous_generation != current_generation {
                probes.reset(&cancel);
                previous_generation = current_generation;
            }
            let sampled_signal = measure::sample_signal_with_cancel(&cancel);
            let observed_at = Instant::now();
            let samples = probes.drain();
            let result = match sampled_signal {
                Ok(signal) => {
                    if association_changed(&known_identity, &signal) {
                        probes.reset(&cancel);
                    }
                    retain_association(&mut known_identity, &signal);
                    let latest_probe = record_recent_probes(
                        &mut probes.tracker,
                        samples,
                        observed_at,
                        probes.boundary,
                    );
                    Ok(LiveSample {
                        signal,
                        path: probes.tracker.fields(),
                        diagnostics: probes.tracker.diagnostics(),
                        latest_probe,
                        rates: probes.traffic.sample_with_cancel(&cancel),
                    })
                }
                Err(error) => {
                    let error = error.to_string();
                    probes.reset(&cancel);
                    Err(error)
                }
            };
            if sender
                .send(WorkerEvent::Live {
                    generation: current_generation,
                    observed_at,
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
