"""Path-quality probing and rolling-window tests; no live network."""
from __future__ import annotations

import subprocess

from wifimap.path_monitor import (
    PathMonitor,
    PathTracker,
    parse_ping_rtt,
    ping_once,
)


def test_parse_ping_rtt_accepts_normal_and_submillisecond_replies():
    assert parse_ping_rtt("64 bytes: time=12.345 ms") == 12.345
    assert parse_ping_rtt("64 bytes: time<1 ms") == 1.0


def test_ping_once_uses_an_argv_and_parses_success():
    seen = []

    def run(args, **kwargs):
        seen.append((args, kwargs))
        return subprocess.CompletedProcess(
            args, 0, stdout="64 bytes: time=4.25 ms\n", stderr="")

    assert ping_once(
        "192.0.2.1", timeout=1.0, runner=run,
        executable="/sbin/ping", platform="darwin") == 4.25
    assert seen[0][0] == [
        "/sbin/ping", "-n", "-c", "1", "-W", "1000", "192.0.2.1"]


def test_tracker_summarizes_latency_loss_and_longest_outage():
    tracker = PathTracker(window_size=6, interval=1.0)
    for gateway, internet in (
        (3.0, 20.0), (4.0, 30.0), (None, None),
        (None, None), (5.0, 25.0), (6.0, 100.0),
    ):
        tracker.record_values(gateway, internet)

    snapshot = tracker.snapshot()

    assert snapshot.gateway.median_ms == 4.5
    assert snapshot.gateway.p95_ms == 6.0
    assert snapshot.gateway.loss_pct == 100.0 / 3.0
    assert snapshot.gateway.max_outage_ms == 2000
    assert snapshot.internet.p95_ms == 100.0
    assert "××" in snapshot.gateway.sparkline(6)


def test_path_sparkline_leaves_vertical_space_at_its_peak():
    tracker = PathTracker(window_size=1)
    tracker.record_values(100.0, 100.0)

    assert tracker.snapshot().gateway.sparkline(1) == "▇"


def test_outage_uses_elapsed_time_when_timeouts_slow_the_probe_loop():
    tracker = PathTracker(window_size=3, interval=1.0)
    tracker.record_values(3.0, 20.0, observed_at=0.0)
    tracker.record_values(None, None, observed_at=2.2)
    tracker.record_values(None, None, observed_at=4.4)

    assert tracker.snapshot().internet.max_outage_ms == 4400


def test_tracker_reset_starts_a_new_spot_window():
    tracker = PathTracker(window_size=60, interval=1.0)
    tracker.record_values(None, 20.0)

    tracker.reset()
    tracker.record_values(2.0, 10.0)

    snapshot = tracker.snapshot()
    assert snapshot.gateway.count == 1
    assert snapshot.gateway.loss_pct == 0.0
    assert snapshot.internet.median_ms == 10.0
    assert snapshot.reading_fields()["gateway_max_outage_ms"] == 0


def test_primary_failure_uses_independent_confirmation_target():
    replies = {
        "192.0.2.1": 3.0,
        "1.1.1.1": None,
        "8.8.8.8": 18.0,
    }
    monitor = PathMonitor(
        gateway="192.0.2.1", on_sample=lambda sample: None,
        probe=lambda target: replies[target],
    )

    sample = monitor.sample_once()

    assert sample.gateway_ms == 3.0
    assert sample.internet_ms == 18.0
    assert sample.internet_target == "8.8.8.8"
    assert sample.primary_failed is True
