//! Observed changes on the current connection, with contemporaneous diagnostics.
//! Observation intervals bound when a change was noticed; they are not roam latency.

use std::{collections::VecDeque, time::Duration};

use crate::model::Signal;

const SAMPLE_LIMIT: usize = 120;
const EVENT_LIMIT: usize = 60;

#[derive(Debug, Clone, PartialEq)]
pub struct TimelineSample {
    pub observed_at: Duration,
    /// Raw radio identity, never replaced by a selected survey network name.
    pub signal: Option<Signal>,
    /// Missing RTT may mean a failed probe or an unavailable probe target.
    pub gateway_ms: Option<f64>,
    pub internet_ms: Option<f64>,
    /// Observed receive/transmit traffic rates, in Mbps.
    pub rates_mbps: Option<(f64, f64)>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TimelineEventKind {
    Roam,
    ApChange,
    NetworkChange,
    ChannelChange,
    SampleUnavailable,
    SampleRecovered,
}

#[derive(Debug, Clone, PartialEq)]
pub struct TimelineEvent {
    pub kind: TimelineEventKind,
    pub before: Option<TimelineSample>,
    pub after: TimelineSample,
    pub observed_interval: Duration,
    /// Identity or sampling was unavailable between the compared observations.
    pub identity_gap: bool,
}

#[derive(Debug, Clone, Default)]
pub struct Timeline {
    pub samples: VecDeque<TimelineSample>,
    pub events: VecDeque<TimelineEvent>,
    last_ap: Option<TimelineSample>,
    last_network: Option<TimelineSample>,
    ap_since: Option<Duration>,
    identity_gap: bool,
}

impl Timeline {
    pub fn reset(&mut self) {
        *self = Self::default();
    }

    pub fn record(
        &mut self,
        observed_at: Duration,
        signal: Option<&Signal>,
        gateway_ms: Option<f64>,
        internet_ms: Option<f64>,
        rates_mbps: Option<(f64, f64)>,
    ) {
        let sample = TimelineSample {
            observed_at,
            signal: signal.cloned(),
            gateway_ms,
            internet_ms,
            rates_mbps,
        };
        let previous = self.samples.back().cloned();
        if let Some(before) = previous.as_ref() {
            match (before.signal.is_some(), signal.is_some()) {
                (true, false) => {
                    self.push_event(TimelineEventKind::SampleUnavailable, before, &sample);
                    self.ap_since = None;
                }
                (false, true) => {
                    self.push_event(TimelineEventKind::SampleRecovered, before, &sample);
                }
                _ => {}
            }
        }

        if let Some(signal) = signal {
            let ssid = known_ssid(signal.ssid.as_deref());
            let bssid = known_bssid(signal.bssid.as_deref());
            let network_change = self
                .last_network
                .as_ref()
                .filter(|before| {
                    ssid.is_some()
                        && known_ssid(before.signal.as_ref().and_then(|s| s.ssid.as_deref()))
                            != ssid
                })
                .cloned();
            let ap_change = self
                .last_ap
                .as_ref()
                .filter(|before| {
                    bssid.is_some()
                        && known_bssid(before.signal.as_ref().and_then(|s| s.bssid.as_deref()))
                            != bssid
                })
                .cloned();

            if let Some(before) = network_change.as_ref() {
                self.push_event(TimelineEventKind::NetworkChange, before, &sample);
                self.last_ap = None;
                self.ap_since = bssid.as_ref().map(|_| observed_at);
            } else if let Some(before) = ap_change.as_ref() {
                let same_network = ssid.is_some()
                    && known_ssid(before.signal.as_ref().and_then(|s| s.ssid.as_deref())) == ssid;
                let kind = if same_network && !self.identity_gap {
                    TimelineEventKind::Roam
                } else {
                    TimelineEventKind::ApChange
                };
                self.push_event(kind, before, &sample);
                self.ap_since = Some(observed_at);
            } else if let Some(before) = previous.as_ref()
                && let Some(previous_signal) = before.signal.as_ref()
                && let (Some(old), Some(new)) = (
                    known_channel(previous_signal.channel.as_deref()),
                    known_channel(signal.channel.as_deref()),
                )
                && old != new
            {
                self.push_event(TimelineEventKind::ChannelChange, before, &sample);
            }

            if bssid.is_some() {
                self.last_ap = Some(sample.clone());
                self.ap_since.get_or_insert(observed_at);
            }
            if ssid.is_some() {
                self.last_network = Some(sample.clone());
            }
            self.identity_gap = ssid.is_none() || bssid.is_none();
        } else {
            self.identity_gap = true;
        }

        self.samples.push_back(sample);
        if self.samples.len() > SAMPLE_LIMIT {
            self.samples.pop_front();
        }
    }

    /// Duration since this AP was first observed, rather than association age.
    pub fn current_ap_dwell(&self) -> Option<Duration> {
        let latest = self.samples.back()?;
        known_bssid(latest.signal.as_ref()?.bssid.as_deref())?;
        Some(latest.observed_at.saturating_sub(self.ap_since?))
    }

    fn push_event(
        &mut self,
        kind: TimelineEventKind,
        before: &TimelineSample,
        after: &TimelineSample,
    ) {
        self.events.push_back(TimelineEvent {
            kind,
            before: Some(before.clone()),
            after: after.clone(),
            observed_interval: after.observed_at.saturating_sub(before.observed_at),
            identity_gap: self.identity_gap
                || after.signal.as_ref().is_none_or(|signal| {
                    known_ssid(signal.ssid.as_deref()).is_none()
                        || known_bssid(signal.bssid.as_deref()).is_none()
                }),
        });
        if self.events.len() > EVENT_LIMIT {
            self.events.pop_front();
        }
    }
}

fn known_ssid(value: Option<&str>) -> Option<&str> {
    let value = value?.trim();
    (!value.is_empty()
        && !["<hidden>", "<redacted>"]
            .iter()
            .any(|v| value.eq_ignore_ascii_case(v)))
    .then_some(value)
}

fn known_channel(value: Option<&str>) -> Option<&str> {
    let value = value?.trim();
    (!value.is_empty()).then_some(value)
}

fn known_bssid(value: Option<&str>) -> Option<String> {
    let value = crate::store::normalize_bssid(value?).ok()?;
    if value == "00:00:00:00:00:00" || u8::from_str_radix(&value[..2], 16).ok()? & 1 != 0 {
        None
    } else {
        Some(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn radio(ssid: Option<&str>, bssid: Option<&str>, channel: &str) -> Signal {
        Signal {
            ssid: ssid.map(str::to_owned),
            bssid: bssid.map(str::to_owned),
            channel: Some(channel.into()),
            rssi: Some(-61.),
            tx_rate: Some("780 Mbps".into()),
            ..Default::default()
        }
    }

    fn record(timeline: &mut Timeline, second: u64, signal: Option<&Signal>) {
        timeline.record(
            Duration::from_secs(second),
            signal,
            Some(3.),
            Some(12.),
            None,
        );
    }

    #[test]
    fn classifies_roams_network_switches_and_channel_changes() {
        let mut timeline = Timeline::default();
        record(
            &mut timeline,
            1,
            Some(&radio(Some("mesh"), Some("aa:bb:cc:dd:ee:01"), "36")),
        );
        record(
            &mut timeline,
            4,
            Some(&radio(Some("mesh"), Some("aa:bb:cc:dd:ee:02"), "44")),
        );
        assert_eq!(
            timeline.events.back().unwrap().kind,
            TimelineEventKind::Roam
        );
        assert_eq!(
            timeline.events.back().unwrap().observed_interval,
            Duration::from_secs(3)
        );
        record(
            &mut timeline,
            7,
            Some(&radio(Some("mesh"), Some("aa:bb:cc:dd:ee:02"), "48")),
        );
        assert_eq!(
            timeline.events.back().unwrap().kind,
            TimelineEventKind::ChannelChange
        );
        record(
            &mut timeline,
            10,
            Some(&radio(Some("guest"), Some("aa:bb:cc:dd:ee:03"), "48")),
        );
        assert_eq!(
            timeline.events.back().unwrap().kind,
            TimelineEventKind::NetworkChange
        );
        assert_eq!(timeline.events.len(), 3);
    }

    #[test]
    fn unknown_network_ap_changes_do_not_claim_roaming() {
        let mut timeline = Timeline::default();
        record(
            &mut timeline,
            1,
            Some(&radio(None, Some("aa:bb:cc:dd:ee:01"), "36")),
        );
        record(
            &mut timeline,
            2,
            Some(&radio(None, Some("aa:bb:cc:dd:ee:02"), "36")),
        );
        assert_eq!(
            timeline.events.back().unwrap().kind,
            TimelineEventKind::ApChange
        );
        assert!(timeline.events.back().unwrap().identity_gap);
    }

    #[test]
    fn redacted_and_missing_identity_are_not_disconnects_or_roams() {
        for missing in [
            None,
            Some("<redacted>"),
            Some("00:00:00:00:00:00"),
            Some("ff:ff:ff:ff:ff:ff"),
            Some("01:00:5e:00:00:01"),
        ] {
            let mut timeline = Timeline::default();
            record(
                &mut timeline,
                1,
                Some(&radio(Some("mesh"), Some("aa:bb:cc:dd:ee:01"), "36")),
            );
            record(&mut timeline, 2, Some(&radio(Some("mesh"), missing, "36")));
            assert!(timeline.events.is_empty());
            record(
                &mut timeline,
                3,
                Some(&radio(Some("mesh"), Some("aa:bb:cc:dd:ee:02"), "36")),
            );
            let event = timeline.events.back().unwrap();
            assert_eq!(event.kind, TimelineEventKind::ApChange);
            assert!(event.identity_gap);
            assert_eq!(event.observed_interval, Duration::from_secs(2));
        }
    }

    #[test]
    fn mac_case_and_separator_changes_do_not_create_transitions() {
        let mut timeline = Timeline::default();
        record(
            &mut timeline,
            1,
            Some(&radio(Some("mesh"), Some("AA-BB-CC-DD-EE-01"), "36")),
        );
        record(
            &mut timeline,
            2,
            Some(&radio(Some("mesh"), Some("aa:bb:cc:dd:ee:01"), "36")),
        );
        assert!(timeline.events.is_empty());
        assert_eq!(timeline.current_ap_dwell(), Some(Duration::from_secs(1)));
    }

    #[test]
    fn missing_ssid_does_not_borrow_a_network_label_for_roaming() {
        let mut timeline = Timeline::default();
        record(
            &mut timeline,
            1,
            Some(&radio(Some("mesh"), Some("aa:bb:cc:dd:ee:01"), "36")),
        );
        record(
            &mut timeline,
            2,
            Some(&radio(None, Some("aa:bb:cc:dd:ee:01"), "36")),
        );
        record(
            &mut timeline,
            3,
            Some(&radio(Some("mesh"), Some("aa:bb:cc:dd:ee:02"), "36")),
        );
        assert_eq!(
            timeline.events.back().unwrap().kind,
            TimelineEventKind::ApChange
        );
        assert!(timeline.samples[1].signal.as_ref().unwrap().ssid.is_none());
    }

    #[test]
    fn reports_sample_gaps_and_recovery_without_repeating_them() {
        let mut timeline = Timeline::default();
        let signal = radio(Some("mesh"), Some("aa:bb:cc:dd:ee:01"), "36");
        record(&mut timeline, 1, Some(&signal));
        record(&mut timeline, 2, None);
        record(&mut timeline, 3, None);
        assert_eq!(timeline.events.len(), 1);
        assert_eq!(timeline.current_ap_dwell(), None);
        record(&mut timeline, 4, Some(&signal));
        assert_eq!(timeline.events.len(), 2);
        assert_eq!(
            timeline.events[0].kind,
            TimelineEventKind::SampleUnavailable
        );
        assert!(timeline.events[0].identity_gap);
        assert_eq!(timeline.events[1].kind, TimelineEventKind::SampleRecovered);
        assert_eq!(timeline.current_ap_dwell(), Some(Duration::ZERO));
    }

    #[test]
    fn recovery_on_a_different_ap_is_an_uncertain_ap_change() {
        let mut timeline = Timeline::default();
        record(
            &mut timeline,
            1,
            Some(&radio(Some("mesh"), Some("aa:bb:cc:dd:ee:01"), "36")),
        );
        record(&mut timeline, 2, None);
        record(
            &mut timeline,
            3,
            Some(&radio(Some("mesh"), Some("aa:bb:cc:dd:ee:02"), "44")),
        );
        assert_eq!(timeline.events.len(), 3);
        assert_eq!(
            timeline.events.back().unwrap().kind,
            TimelineEventKind::ApChange
        );
        assert!(timeline.events.back().unwrap().identity_gap);
    }

    #[test]
    fn switching_to_a_network_with_unknown_ap_does_not_reuse_the_old_ap() {
        let mut timeline = Timeline::default();
        record(
            &mut timeline,
            1,
            Some(&radio(Some("mesh"), Some("aa:bb:cc:dd:ee:01"), "36")),
        );
        record(&mut timeline, 2, Some(&radio(Some("guest"), None, "44")));
        assert_eq!(timeline.current_ap_dwell(), None);
        record(
            &mut timeline,
            3,
            Some(&radio(Some("guest"), Some("aa:bb:cc:dd:ee:02"), "44")),
        );
        assert_eq!(timeline.events.len(), 1);
        assert_eq!(timeline.events[0].kind, TimelineEventKind::NetworkChange);
        assert!(timeline.events[0].identity_gap);
        assert_eq!(timeline.current_ap_dwell(), Some(Duration::ZERO));
    }

    #[test]
    fn transition_snapshots_preserve_latency_loss_signal_and_traffic_context() {
        let mut timeline = Timeline::default();
        let before = radio(Some("mesh"), Some("aa:bb:cc:dd:ee:01"), "36");
        let mut after = radio(Some("mesh"), Some("aa:bb:cc:dd:ee:02"), "44");
        after.rssi = Some(-78.);
        after.tx_rate = Some("130 Mbps".into());
        timeline.record(
            Duration::from_secs(1),
            Some(&before),
            Some(3.),
            Some(12.),
            Some((5., 2.)),
        );
        timeline.record(
            Duration::from_secs(2),
            Some(&after),
            None,
            Some(90.),
            Some((1., 0.5)),
        );
        let event = timeline.events.back().unwrap();
        assert_eq!(event.before.as_ref().unwrap().gateway_ms, Some(3.));
        assert_eq!(event.after.gateway_ms, None);
        assert_eq!(event.after.internet_ms, Some(90.));
        assert_eq!(event.after.rates_mbps, Some((1., 0.5)));
        assert_eq!(event.after.signal, Some(after));
    }

    #[test]
    fn bounds_retention_and_reset_clears_identity_context() {
        let mut timeline = Timeline::default();
        for second in 0..300 {
            let bssid = if second % 2 == 0 {
                "aa:bb:cc:dd:ee:01"
            } else {
                "aa:bb:cc:dd:ee:02"
            };
            record(
                &mut timeline,
                second,
                Some(&radio(Some("mesh"), Some(bssid), "36")),
            );
        }
        assert_eq!(timeline.samples.len(), 120);
        assert_eq!(timeline.events.len(), 60);
        timeline.reset();
        assert!(timeline.samples.is_empty());
        assert!(timeline.events.is_empty());
        record(
            &mut timeline,
            1,
            Some(&radio(Some("guest"), Some("aa:bb:cc:dd:ee:03"), "36")),
        );
        assert!(timeline.events.is_empty());
    }
}
