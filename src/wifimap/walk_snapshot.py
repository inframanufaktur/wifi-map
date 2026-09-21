"""Snapshot and benchmark workers used by walk mode."""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from wifimap import signal as signal_mod
from wifimap import speed as speed_mod
from wifimap import store as store_mod
from wifimap.path_monitor import PathSnapshot
from wifimap.walk_ui import fmt_mbps


@dataclass
class SnapshotResult:
    ok: bool
    reading_id: Optional[int] = None
    message: str = ""
    ping_ms: Optional[float] = None
    down_mbps: Optional[float] = None
    up_mbps: Optional[float] = None
    rssi: Optional[int] = None
    snr: Optional[int] = None


@dataclass(frozen=True)
class SpotBaseline:
    """Median values for one spot within the selected baseline walk."""

    spot_id: int
    rssi: Optional[float] = None
    noise: Optional[float] = None
    snr: Optional[float] = None
    tx_rate: Optional[float] = None
    ping_ms: Optional[float] = None
    down_mbps: Optional[float] = None
    up_mbps: Optional[float] = None
    ssid: Optional[str] = None
    bssid: Optional[str] = None
    channel: Optional[str] = None
    server: Optional[str] = None


@dataclass(frozen=True)
class MetricComparison:
    """Current, baseline and arithmetic delta for one metric."""

    current: Optional[float]
    baseline: Optional[float]
    delta: Optional[float]


@dataclass(frozen=True)
class SpeedProbeResult:
    """Transient speedtest result; never persisted as a reading."""

    ok: bool
    spot_id: int
    generation: int
    ping_ms: Optional[float] = None
    down_mbps: Optional[float] = None
    up_mbps: Optional[float] = None
    server: Optional[str] = None
    message: str = ""


def _sample_guarded() -> Tuple[Optional[signal_mod.Signal], str]:
    """Return a sampled signal and an optional no-WiFi error message."""
    try:
        return (signal_mod.sample_signal(), "")
    except signal_mod.NoWiFiError as exc:
        return (None, "NO-WIFI: %s" % (exc,))
    except Exception:
        return (signal_mod.Signal(), "")


def finish_snapshot(
    db_path: str,
    spot_id: int,
    ssid_override: Optional[str] = None,
    run_speedtest_fn: Optional[Callable[[], speed_mod.Speed]] = None,
    no_speedtest: bool = False,
    walk_id: Optional[int] = None,
    ssid_id: Optional[int] = None,
    path_snapshot: Optional[PathSnapshot] = None,
) -> SnapshotResult:
    """Sample signal and speed, then store a reading on a private connection."""
    sig, err = _sample_guarded()
    if sig is None:
        return SnapshotResult(ok=False, message=err)
    if ssid_override is not None:
        sig.ssid = ssid_override
    ping_ms = down = up = None
    server: Optional[str] = None
    notice = ""
    if not no_speedtest:
        fn = run_speedtest_fn or speed_mod.run_speedtest
        try:
            speed = fn()
            ping_ms, down, up, server = (
                speed.ping_ms, speed.down_mbps, speed.up_mbps, speed.server)
        except speed_mod.SpeedtestUnavailableError as exc:
            notice = "Warning: %s; signal-only" % (exc,)
        except speed_mod.SpeedtestFailedError:
            ping_ms, down, up, server = None, None, None, "ERROR"
            notice = "speedtest failed; signal kept"
    try:
        conn = store_mod.get_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        return SnapshotResult(ok=False, message="DB error: %s" % (exc,))
    try:
        try:
            path_fields = (path_snapshot.reading_fields()
                           if path_snapshot is not None else {})
            reading_id = store_mod.add_reading(
                conn, spot_id,
                ssid=None if ssid_id is not None else sig.ssid,
                ssid_id=ssid_id,
                bssid=sig.bssid,
                rssi=sig.rssi,
                noise=sig.noise,
                snr=sig.snr,
                channel=sig.channel,
                phy=sig.phy,
                tx_rate=sig.tx_rate,
                ping_ms=ping_ms,
                down_mbps=down,
                up_mbps=up,
                server=server,
                walk_id=walk_id,
                **path_fields,
            )
        except (sqlite3.Error, OSError, ValueError) as exc:
            return SnapshotResult(ok=False, message="DB error: %s" % (exc,))
    finally:
        conn.close()
    message = "saved #%d" % reading_id
    if not no_speedtest:
        message += " down %s up %s Mbps" % (fmt_mbps(down), fmt_mbps(up))
    if notice:
        message += " (%s)" % notice
    return SnapshotResult(
        ok=True,
        reading_id=reading_id,
        message=message,
        ping_ms=ping_ms,
        down_mbps=down,
        up_mbps=up,
        rssi=sig.rssi,
        snr=sig.snr,
    )


def start_snapshot_thread(
    db_path: str,
    location_id: int,
    no_speedtest: bool = False,
    ssid_override: Optional[str] = None,
    on_done: Optional[Callable[[SnapshotResult], None]] = None,
    run_speedtest_fn: Optional[Callable[[], speed_mod.Speed]] = None,
    walk_id: Optional[int] = None,
    ssid_id: Optional[int] = None,
    path_snapshot: Optional[PathSnapshot] = None,
) -> "threading.Thread":
    """Sample and store a reading on a new daemon thread."""

    def _work() -> None:
        result = finish_snapshot(
            db_path, location_id,
            ssid_override=ssid_override,
            ssid_id=ssid_id,
            run_speedtest_fn=run_speedtest_fn,
            no_speedtest=no_speedtest,
            walk_id=walk_id,
            path_snapshot=path_snapshot,
        )
        if on_done is not None:
            on_done(result)

    thread = threading.Thread(target=_work, daemon=True)
    thread.start()
    return thread


def _finish_benchmark(
    db_path: str,
    location_id: int,
    note: str = "",
    ssid_override: Optional[str] = None,
    run_speedtest_fn: Optional[Callable[[], speed_mod.Speed]] = None,
    no_speedtest: bool = False,
    ssid_id: Optional[int] = None,
) -> SnapshotResult:
    """Sample signal and speed, then store the location benchmark."""
    sig, err = _sample_guarded()
    if sig is None:
        return SnapshotResult(ok=False, message=err)
    if ssid_override is not None:
        sig.ssid = ssid_override
    ping_ms = down = up = None
    server: Optional[str] = None
    notice = ""
    if not no_speedtest:
        fn = run_speedtest_fn or speed_mod.run_speedtest
        try:
            speed = fn()
            ping_ms, down, up, server = (
                speed.ping_ms, speed.down_mbps, speed.up_mbps, speed.server)
        except speed_mod.SpeedtestUnavailableError as exc:
            notice = "Warning: %s; signal-only" % (exc,)
        except speed_mod.SpeedtestFailedError:
            ping_ms, down, up, server = None, None, None, "ERROR"
            notice = "speedtest failed; signal kept"
    try:
        conn = store_mod.get_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        return SnapshotResult(ok=False, message="DB error: %s" % (exc,))
    try:
        try:
            store_mod.set_benchmark(
                conn, location_id,
                ssid=None if ssid_id is not None else sig.ssid,
                ssid_id=ssid_id,
                bssid=sig.bssid,
                rssi=sig.rssi,
                noise=sig.noise,
                snr=sig.snr,
                channel=sig.channel,
                phy=sig.phy,
                tx_rate=sig.tx_rate,
                ping_ms=ping_ms,
                down_mbps=down,
                up_mbps=up,
                server=server,
                note=note or None,
            )
        except (sqlite3.Error, OSError, ValueError) as exc:
            return SnapshotResult(ok=False, message="DB error: %s" % (exc,))
    finally:
        conn.close()

    def _display(value: object) -> str:
        return "-" if value is None else str(value)

    message = "benchmark set for #%d rssi=%s snr=%s down=%s up=%s" % (
        location_id, _display(sig.rssi), _display(sig.snr),
        fmt_mbps(down), fmt_mbps(up))
    if notice:
        message += " (%s)" % notice
    return SnapshotResult(
        ok=True,
        message=message,
        ping_ms=ping_ms,
        down_mbps=down,
        up_mbps=up,
        rssi=sig.rssi,
        snr=sig.snr,
    )
