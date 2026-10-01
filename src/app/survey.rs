//! Survey sessions, places, comparison choices, and editing.
use std::{sync::atomic::Ordering, time::Instant};

use anyhow::{Result, bail};

use crate::{
    evaluation,
    model::{PathFields, Reading, Signal, Speed},
    path_diagnostics::PathDiagnostics,
    store::Filter,
};

use super::{Choice, Edit, Editor, Level, Picker, WalkApp, WorkKind};

impl WalkApp {
    pub(super) fn finish_session(&mut self) -> Result<()> {
        if let Some(id) = self.walk_id.take() {
            self.store.finish_walk(id)?;
        }
        Ok(())
    }

    fn reset_live(&mut self) {
        self.generation.fetch_add(1, Ordering::Relaxed);
        self.speed = Speed::default();
        self.path = PathFields::default();
        self.path_diagnostics = PathDiagnostics::default();
        self.load_latency = None;
        self.load_running = false;
        self.rates = None;
        self.history.clear();
    }

    fn reset_diagnostics_session(&mut self) {
        self.timeline.reset();
        self.diagnostics_epoch = Instant::now();
        self.diagnostics_offset = 0;
        self.last_association = Signal::default();
        self.ssid_observed_at = None;
        self.bssid_observed_at = None;
    }

    pub(super) fn set_ssid(&mut self, name: &str) -> Result<()> {
        let id = self.store.resolve_ssid(self.active.location_id, name)?;
        let ssid = self
            .store
            .get_ssid(id)?
            .ok_or_else(|| anyhow::anyhow!("network was removed"))?;
        self.active.ssid_id = Some(id);
        self.active.signal.ssid = Some(ssid.name);
        self.start_session()
    }

    pub(super) fn start_session(&mut self) -> Result<()> {
        if self.active.location_id == 0 || self.active.ssid_id.is_none() {
            return Ok(());
        }
        self.finish_session()?;
        self.reset_live();
        self.reset_diagnostics_session();
        self.walk_name = self.initial_name.take().unwrap_or_else(|| {
            format!("walk {}", chrono::Local::now().format("%Y-%m-%d %H:%M:%S"))
        });
        self.walk_id = Some(self.store.create_walk(
            self.active.location_id,
            self.active.ssid_id,
            &self.walk_name,
        )?);
        self.active.walk_id = self.walk_id;
        self.active.walk_name = Some(self.walk_name.clone());
        if let Some(target) = self.initial_compare.take() {
            let id = if target == "latest" {
                self.store
                    .list_walks(self.active.location_id)?
                    .into_iter()
                    .filter(|w| Some(w.id) != self.walk_id)
                    .find(|w| {
                        self.store
                            .list_readings(&Filter {
                                walk_id: Some(w.id),
                                ..Default::default()
                            })
                            .is_ok_and(|r| !r.is_empty())
                    })
                    .map(|w| w.id)
                    .ok_or_else(|| anyhow::anyhow!("no prior walk available for comparison"))?
            } else {
                self.store.resolve_walk(self.active.location_id, &target)?
            };
            self.select_baseline(id)?;
        }
        Ok(())
    }

    pub(super) fn select_location(&mut self, id: i64) -> Result<()> {
        let location = self
            .store
            .list_locations()?
            .into_iter()
            .find(|l| l.id == id)
            .ok_or_else(|| anyhow::anyhow!("location was removed"))?;
        if self.active.location_id != id {
            self.finish_session()?;
            self.reset_live();
            self.reset_diagnostics_session();
            self.baseline_id = None;
            self.baseline_name = None;
            self.baseline = None;
            self.active = Reading {
                location_id: id,
                location: location.name,
                ..Default::default()
            };
        }
        self.benchmark = self.store.get_benchmark(id)?;
        self.reload_places()?;
        if let Some(first) = self.places.first().cloned() {
            self.select_spot(first.spot_id)?;
        }
        if let Some(name) = self.initial_ssid.take() {
            self.set_ssid(&name)?;
        } else if self.demo {
            if let Some(ssid) = self.store.list_ssids(id)?.first() {
                self.active.ssid_id = Some(ssid.id);
                self.active.signal.ssid = Some(ssid.name.clone());
                self.start_session()?;
            }
        } else if self.active.ssid_id.is_none() {
            self.open_picker(Level::Ssid)?;
        }
        Ok(())
    }

    fn reload_places(&mut self) -> Result<()> {
        self.places.clear();
        for room in self.store.list_rooms(self.active.location_id)? {
            for spot in self.store.list_spots(room.id)? {
                self.places.push(Reading {
                    location_id: self.active.location_id,
                    location: self.active.location.clone(),
                    room_id: room.id,
                    room: room.name.clone(),
                    floor: room.floor,
                    outdoors: room.outdoors,
                    spot_id: spot.id,
                    spot: spot.name,
                    ..Default::default()
                });
            }
        }
        self.selected = self.selected.min(self.places.len().saturating_sub(1));
        Ok(())
    }

    pub(super) fn select_spot(&mut self, id: i64) -> Result<()> {
        if let Some((index, place)) = self
            .places
            .iter()
            .enumerate()
            .find(|(_, p)| p.spot_id == id)
            .map(|(index, place)| (index, place.clone()))
        {
            if self.active.spot_id != id {
                self.reset_live();
            }
            self.selected = index;
            self.active.room_id = place.room_id;
            self.active.room = place.room.clone();
            self.active.floor = place.floor;
            self.active.outdoors = place.outdoors;
            self.active.spot_id = place.spot_id;
            self.active.spot = place.spot.clone();
            self.speed = Speed::default();
            self.path = PathFields::default();
            self.reload_baseline()?;
        }
        Ok(())
    }

    pub(super) fn select_baseline(&mut self, id: i64) -> Result<()> {
        if id == 0 {
            self.baseline_id = None;
            self.baseline_name = None;
            self.baseline = None;
            return Ok(());
        }
        let walk = self
            .store
            .get_walk(id)?
            .ok_or_else(|| anyhow::anyhow!("walk was removed"))?;
        if walk.location_id != self.active.location_id || Some(id) == self.walk_id {
            bail!("choose a prior walk in this location");
        }
        if self
            .store
            .list_readings(&Filter {
                walk_id: Some(id),
                limit: Some(1),
                ..Default::default()
            })?
            .is_empty()
        {
            bail!("selected walk has no readings");
        }
        self.baseline_id = Some(id);
        self.baseline_name = Some(walk.name);
        self.reload_baseline()
    }

    fn reload_baseline(&mut self) -> Result<()> {
        self.baseline = if let Some(id) = self.baseline_id {
            let readings: Vec<_> = self
                .store
                .list_readings(&Filter {
                    walk_id: Some(id),
                    ..Default::default()
                })?
                .into_iter()
                .filter(|r| r.spot_id == self.active.spot_id)
                .collect();
            evaluation::analysis_rows(&readings, true)
                .first()
                .map(|row| {
                    let mut reading = row.reading.clone();
                    reading.signal.rssi = row.metric("rssi");
                    reading.signal.noise = row.metric("noise");
                    reading.signal.snr = row.metric("snr");
                    reading.speed.ping_ms = row.metric("ping_ms");
                    reading.speed.down_mbps = row.metric("down_mbps");
                    reading.speed.up_mbps = row.metric("up_mbps");
                    reading
                })
        } else {
            None
        };
        Ok(())
    }

    pub(super) fn open_picker(&mut self, level: Level) -> Result<()> {
        let (title, items) = match level {
            Level::Location => (
                "Location",
                self.store
                    .list_locations()?
                    .into_iter()
                    .map(|l| Choice {
                        id: l.id,
                        label: l.name,
                    })
                    .collect(),
            ),
            Level::Room => {
                if self.active.location_id == 0 {
                    return self.open_picker(Level::Location);
                }
                (
                    "Room",
                    self.store
                        .list_rooms(self.active.location_id)?
                        .into_iter()
                        .map(|r| Choice {
                            id: r.id,
                            label: format!(
                                "{} · floor {}{}",
                                r.name,
                                r.floor,
                                if r.outdoors { " · outdoors" } else { "" }
                            ),
                        })
                        .collect(),
                )
            }
            Level::Spot => {
                if self.active.room_id == 0 {
                    return self.open_picker(Level::Room);
                }
                (
                    "Spot",
                    self.store
                        .list_spots(self.active.room_id)?
                        .into_iter()
                        .map(|s| Choice {
                            id: s.id,
                            label: s.name,
                        })
                        .collect(),
                )
            }
            Level::Ssid => {
                if self.active.location_id == 0 {
                    return self.open_picker(Level::Location);
                }
                (
                    "Network / SSID",
                    self.store
                        .list_ssids(self.active.location_id)?
                        .into_iter()
                        .map(|s| Choice {
                            id: s.id,
                            label: s.name,
                        })
                        .collect(),
                )
            }
            Level::Baseline => {
                let mut rows: Vec<_> = self
                    .store
                    .list_walks(self.active.location_id)?
                    .into_iter()
                    .filter(|w| Some(w.id) != self.walk_id)
                    .filter(|w| {
                        self.store
                            .list_readings(&Filter {
                                walk_id: Some(w.id),
                                ..Default::default()
                            })
                            .is_ok_and(|r| !r.is_empty())
                    })
                    .map(|w| Choice {
                        id: w.id,
                        label: format!("{} · {} · #{}", w.name, w.started_at, w.id),
                    })
                    .collect();
                rows.push(Choice {
                    id: 0,
                    label: "Off".into(),
                });
                ("Compare with prior walk", rows)
            }
            _ => return Ok(()),
        };
        self.picker = Some(Picker::new(level, title, items));
        Ok(())
    }

    pub(super) fn begin_create(&mut self, level: Level) {
        let (title, action) = match level {
            Level::Location => ("New location", Edit::Location),
            Level::Room => ("New room name", Edit::RoomName),
            Level::Ssid => ("Network / SSID name", Edit::Ssid),
            Level::Spot => ("New spot name", Edit::Spot),
            _ => return,
        };
        self.picker = None;
        self.editor = Some(Editor {
            title: title.into(),
            text: String::new(),
            action,
        });
    }

    pub(super) fn submit_editor(&mut self, editor: Editor) -> Result<()> {
        let text = editor.text.trim();
        if text.is_empty() {
            bail!("enter a value, or press Esc to cancel");
        }
        match editor.action {
            Edit::Location => {
                let id = self.store.create_location(text)?;
                self.select_location(id)?;
            }
            Edit::RoomName => {
                self.editor = Some(Editor {
                    title: "Room floor (integer, e.g. 0 or -1)".into(),
                    text: "0".into(),
                    action: Edit::RoomFloor(text.into()),
                })
            }
            Edit::RoomFloor(name) => {
                self.editor = Some(Editor {
                    title: "Outdoors? (yes / no)".into(),
                    text: "no".into(),
                    action: Edit::RoomOutdoors(name, text.parse()?),
                })
            }
            Edit::RoomOutdoors(name, floor) => {
                let outdoors = match text.to_lowercase().as_str() {
                    "y" | "yes" | "1" => true,
                    "n" | "no" | "0" => false,
                    _ => bail!("enter yes or no"),
                };
                let id = self
                    .store
                    .create_room(self.active.location_id, &name, floor, outdoors)?;
                self.active.room_id = id;
                self.active.room = name;
                self.active.floor = floor;
                self.active.outdoors = outdoors;
                self.active.spot_id = 0;
                self.active.spot.clear();
                self.reset_live();
                self.baseline = None;
                self.open_picker(Level::Spot)?;
            }
            Edit::Spot => {
                let id = self.store.create_spot(self.active.room_id, text)?;
                self.reload_places()?;
                self.select_spot(id)?;
            }
            Edit::Ssid => {
                self.set_ssid(text)?;
                if self.active.room_id == 0 {
                    self.open_picker(Level::Room)?;
                }
            }
            Edit::Alias => {
                let bssid = self
                    .signal
                    .bssid
                    .as_deref()
                    .ok_or_else(|| anyhow::anyhow!("access point address unavailable"))?;
                self.store.set_access_point_name(bssid, text)?;
                self.active.ap_name = Some(text.into());
            }
        }
        Ok(())
    }

    pub(super) fn apply_choice(&mut self, level: Level, id: i64) -> Result<()> {
        match level {
            Level::Location => self.select_location(id)?,
            Level::Room => {
                let room = self
                    .store
                    .list_rooms(self.active.location_id)?
                    .into_iter()
                    .find(|r| r.id == id)
                    .ok_or_else(|| anyhow::anyhow!("room was removed"))?;
                self.active.room_id = room.id;
                self.active.room = room.name;
                self.active.floor = room.floor;
                self.active.outdoors = room.outdoors;
                self.active.spot_id = 0;
                self.active.spot.clear();
                self.reset_live();
                self.baseline = None;
                self.open_picker(Level::Spot)?;
            }
            Level::Spot => self.select_spot(id)?,
            Level::Ssid => {
                let ssid = self
                    .store
                    .list_ssids(self.active.location_id)?
                    .into_iter()
                    .find(|s| s.id == id)
                    .ok_or_else(|| anyhow::anyhow!("network was removed"))?;
                self.active.ssid_id = Some(ssid.id);
                self.active.signal.ssid = Some(ssid.name);
                self.start_session()?;
                if self.active.room_id == 0 {
                    self.open_picker(Level::Room)?;
                }
            }
            Level::Baseline => self.select_baseline(id)?,
            Level::BenchmarkConfirmation if id == 1 => self.start_work(WorkKind::Benchmark)?,
            _ => {}
        }
        Ok(())
    }
}
