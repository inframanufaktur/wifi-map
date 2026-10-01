//! Background measurement scheduling and result application.
use std::{
    sync::atomic::Ordering,
    thread,
    time::{Duration, Instant},
};

use anyhow::{Result, bail};

use crate::{
    measure,
    model::{PathFields, Signal, Speed, timestamp},
    path_diagnostics::PathDiagnostics,
};

use super::{
    LiveSample, LoadReport, WalkApp, WorkKind, WorkerEvent, demo_load_latency, demo_speed,
    live::association_changed,
};

impl WalkApp {
    pub(super) fn start_work(&mut self, kind: WorkKind) -> Result<()> {
        if self.pending > 0 {
            bail!("a measurement is already running; wait for it to finish");
        }
        if kind != WorkKind::Probe
            && (self.active.location_id == 0 || self.active.ssid_id.is_none())
        {
            bail!("select a location and network first (l / w)");
        }
        if kind == WorkKind::Snapshot && self.active.spot_id == 0 {
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
        self.load_running = !self.no_speedtest;
        self.load_latency = None;
        let generation = self.generation.load(Ordering::Relaxed);
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
                generation,
                kind,
                result: Ok((
                    signal,
                    speed,
                    None,
                    (!self.no_speedtest).then(|| {
                        Box::new(LoadReport {
                            latency: demo_load_latency(),
                            association: self.signal.clone(),
                            completed_at: Instant::now(),
                        })
                    }),
                )),
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
                    let (speed, warning, load_latency) = if no_speedtest {
                        (Speed::default(), None, None)
                    } else {
                        let (result, load_latency) = measure::run_speedtest_diagnostics(&cancel);
                        let (ssid, bssid) =
                            measure::read_network_identity_with_cancel(&cancel).unwrap_or_default();
                        let load_latency = Box::new(LoadReport {
                            latency: load_latency,
                            association: Signal {
                                ssid,
                                bssid,
                                ..Default::default()
                            },
                            completed_at: Instant::now(),
                        });
                        match result {
                            Ok(speed) => (speed, None, Some(load_latency)),
                            Err(error) => (
                                Speed {
                                    server: Some(format!("ERROR: {error}")),
                                    ..Default::default()
                                },
                                Some(error.to_string()),
                                Some(load_latency),
                            ),
                        }
                    };
                    Ok((signal, speed, warning, load_latency))
                })();
                if !cancel.is_cancelled() {
                    let _ = sender.send(WorkerEvent::Measured {
                        target: Box::new(target),
                        generation,
                        kind,
                        result,
                    });
                }
            });
        }
        Ok(())
    }

    pub(super) fn apply_event(&mut self, event: WorkerEvent) {
        match event {
            WorkerEvent::Live {
                generation,
                observed_at,
                result,
            } => {
                if generation != self.generation.load(Ordering::Relaxed) {
                    return;
                }
                match result {
                    Ok(sample) => {
                        let LiveSample {
                            signal,
                            path,
                            diagnostics,
                            latest_probe,
                            rates,
                        } = sample;
                        self.observe_association(&signal, observed_at);
                        let (gateway, internet) = latest_probe.unwrap_or((None, None));
                        self.timeline.record(
                            observed_at.saturating_duration_since(self.diagnostics_epoch),
                            Some(&signal),
                            gateway,
                            internet,
                            rates,
                        );
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
                        self.path_diagnostics = diagnostics;
                        self.rates = rates;
                        self.sample_error = false;
                    }
                    Err(error) => {
                        self.timeline.record(
                            observed_at.saturating_duration_since(self.diagnostics_epoch),
                            None,
                            None,
                            None,
                            None,
                        );
                        self.message = format!("WiFi unavailable · {error}");
                        self.sample_error = true;
                        self.signal = Signal::default();
                        self.path = PathFields::default();
                        self.path_diagnostics = PathDiagnostics::default();
                        self.rates = None;
                        self.history.push_back(None);
                        while self.history.len() > 60 {
                            self.history.pop_front();
                        }
                    }
                }
            }
            WorkerEvent::Measured {
                mut target,
                generation,
                kind,
                result,
            } => {
                self.pending = self.pending.saturating_sub(1);
                let current_target = generation == self.generation.load(Ordering::Relaxed)
                    && target.location_id == self.active.location_id
                    && target.spot_id == self.active.spot_id
                    && target.ssid_id == self.active.ssid_id
                    && target.walk_id == self.walk_id;
                if self.pending == 0 {
                    self.load_running = false;
                }
                if kind == WorkKind::Probe {
                    self.probe_running = false;
                }
                let (mut signal, speed, warning, load_latency) = match result {
                    Ok(value) => value,
                    Err(error) => {
                        self.message = error;
                        return;
                    }
                };
                if current_target
                    && !self.no_speedtest
                    && let Some(report) = load_latency
                {
                    let current_since_completion = Signal {
                        ssid: self
                            .ssid_observed_at
                            .filter(|latest| *latest > report.completed_at)
                            .and(self.last_association.ssid.clone()),
                        bssid: self
                            .bssid_observed_at
                            .filter(|latest| *latest > report.completed_at)
                            .and(self.last_association.bssid.clone()),
                        ..Default::default()
                    };
                    let changed_since_completion =
                        association_changed(&report.association, &current_since_completion);
                    if !changed_since_completion {
                        self.observe_association(&report.association, report.completed_at);
                        self.load_latency = Some(report.latency);
                    }
                }
                signal.ssid = target.signal.ssid.clone().or(signal.ssid);
                target.signal = signal;
                target.speed = speed.clone();
                if kind == WorkKind::Probe {
                    if current_target {
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
                        if current_target {
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

    pub(super) fn drain(&mut self) {
        while let Ok(event) = self.receiver.try_recv() {
            self.apply_event(event);
        }
    }
}
