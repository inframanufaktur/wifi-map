"""Tests for wifimap.speed — Ookla speedtest JSON parse (no live network)."""
import json
import subprocess

import pytest

from wifimap.speed import (
    Speed,
    SpeedtestFailedError,
    SpeedtestUnavailableError,
    _OOKLA_ARGV,
    run_speedtest,
)

# Minimal Ookla `speedtest --format=json` sample. Official binary reports
# throughput as BYTES/sec in download/upload.bandwidth (NOT bits), so
# Mbps = bandwidth / 125_000. ping.latency is already ms.
OOKLA_SAMPLE = {
    "type": "result",
    "timestamp": "2026-09-18T10:00:00Z",
    "ping": {"jitter": 0.5, "latency": 11.285},
    "download": {"bandwidth": 11750000, "bytes": 100000000, "elapsed": 8500},
    "upload": {"bandwidth": 1250000, "bytes": 10000000, "elapsed": 8000},
    "server": {"id": 1234, "name": "Testville", "location": "Testville",
               "country": "Nowhere"},
    "result": {"id": "abc-123", "url": "https://www.speedtest.net/result/c/abc-123"},
}


def _completed(payload):
    return subprocess.CompletedProcess(
        args=["speedtest", "--format=json"], returncode=0,
        stdout=json.dumps(payload), stderr="")


def test_run_speedtest_parses_units(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _completed(OOKLA_SAMPLE))
    sp = run_speedtest()
    assert isinstance(sp, Speed)
    # 11_750_000 B/s / 125_000 = 94.0 Mbps down
    assert sp.down_mbps == pytest.approx(94.0)
    # 1_250_000 B/s / 125_000 = 10.0 Mbps up
    assert sp.up_mbps == pytest.approx(10.0)
    assert sp.ping_ms == pytest.approx(11.285)
    assert "Testville" in (sp.server or "")


def test_run_speedtest_missing_binary(monkeypatch):
    def _missing(*a, **k):
        raise FileNotFoundError(2, "No such file or directory")
    monkeypatch.setattr(subprocess, "run", _missing)
    with pytest.raises(SpeedtestUnavailableError):
        run_speedtest()


def test_run_speedtest_timeout(monkeypatch):
    def _hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="speedtest", timeout=120)
    monkeypatch.setattr(subprocess, "run", _hang)
    with pytest.raises(SpeedtestFailedError):
        run_speedtest()


def test_run_speedtest_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(
            args=["speedtest"], returncode=1, stdout="", stderr="boom"))
    with pytest.raises(SpeedtestFailedError):
        run_speedtest()


def test_run_speedtest_passes_license_accept_flags(monkeypatch):
    seen = {}

    def _capture(args, **kwargs):
        seen["argv"] = list(args)
        return _completed(OOKLA_SAMPLE)

    monkeypatch.setattr(subprocess, "run", _capture)
    run_speedtest()
    assert "--accept-license" in seen["argv"]
    assert "--accept-gdpr" in seen["argv"]
    assert "--format=json" in seen["argv"]
    assert seen["argv"] == _OOKLA_ARGV
