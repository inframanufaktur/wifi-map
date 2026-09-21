"""Walk TUI: live signal poll + snapshot/location keys (stdlib only).

Thread safety (M1 concern): ``store.list_readings`` toggles
``conn.row_factory`` on the passed connection, so connections are NOT
thread-safe to share. The snapshot background thread MUST open its own
``store.get_db(db_path)`` connection and close it; it never touches the
UI thread's connection. ``WalkState`` toast/pending mutations from worker
callbacks and UI-thread reads are serialized by ``WalkState._lock``.
"""
from __future__ import annotations

import shutil
import sqlite3
import statistics
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Dict, List, Literal, Optional, Tuple, Union

from wifimap import signal as signal_mod
from wifimap import ssid as ssid_mod
from wifimap import speed as speed_mod
from wifimap import store as store_mod
from wifimap import traffic as traffic_mod
from wifimap.walk_picker import (
    _active_room_id,
    _fallback_create,
    _fallback_create_room,
    _fallback_create_spot,
    _fallback_floor,
    _fallback_pick,
    _fallback_pick_level,
    _find_room_by_name,
)
from wifimap.walk_ui import (
    SparkHistory,
    ansi_wrap,
    attempt_read,
    fmt_mbps,
    fmt_rate_val,
    format_addr_line,
    format_extra_line,
    format_meter_left,
    format_meter_row,
    format_net_line,
    format_radio_line,
    format_signal_line,
    graph_width,
    grouped_graph_width,
    history_cap,
    layout_mode,
    meter_layout,
    parse_floor_input,
    picker_move,
    picker_press,
    picker_start_cursor,
    rate_rssi,
    rate_snr,
    rating_style,
    snapshot_payload,
    wide_graph_width,
)
from wifimap.walk_ui import METER_LABEL_W, METER_VAL_W, WIDE_MIN_WIDTH


# ---------------------------------------------------------------------------
# Module constants: walk keys, key codes, timeouts
# ---------------------------------------------------------------------------

KEY_SNAPSHOT = "s"
KEY_BENCHMARK = "b"
KEY_SWITCH = "l"
KEY_NEW = "n"
KEY_FLOOR = "f"
KEY_COMPARE = "c"
KEY_SPEED_PROBE = "t"
KEY_QUIT = "q"
KEY_QUIT_UPPER = "Q"
KEY_CREATE = "+"
QUIT_WORDS = ("q", "quit", "exit")

KEY_ESC = 27
KEY_CTRL_C = 3
KEY_NO_INPUT = -1
KEY_ENTER_CODES = (10, 13)  # LF / CR; curses.KEY_ENTER handled alongside

POLL_TIMEOUT_MIN_MS = 50
PICKER_TIMEOUT_RESTORE_MS = 1000
FALLBACK_SETTLE_SLEEP = 0.1
DB_OPEN_FAIL_SLEEP = 2.0
# Signal averaging (5s) + Ookla's 120s timeout + shutdown overhead.
WORKER_DRAIN_TIMEOUT = 130.0


def _poll_timeout_ms(interval: float) -> int:
    """Poll getch timeout for walk loop; floor keeps fast intervals usable."""
    return max(POLL_TIMEOUT_MIN_MS, int(interval * 1000))

#: Picker outcome: a location id, the ``"new"`` create row, or None (cancel).
PickerSelection = Union[int, Literal["new"]]


# ---------------------------------------------------------------------------
# Snapshot background worker (thread-safe: own DB connection)
# ---------------------------------------------------------------------------

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
    """sample_signal with attempt_read-style guards; (sig, error_msg).

    ``NoWiFiError`` → (None, "NO-WIFI: ...") — caller aborts the
    capture with that message. Any other error → (empty Signal, "")
    so the capture still stores an UNKNOWN row (walk must never crash).
    """
    try:
        return (signal_mod.sample_signal(), "")
    except signal_mod.NoWiFiError as exc:
        return (None, "NO-WIFI: %s" % (exc,))
    except Exception:  # noqa: BLE001 - walk must never crash
        return (signal_mod.Signal(), "")


def finish_snapshot(
    db_path: str,
    spot_id: int,
    ssid_override: Optional[str] = None,
    run_speedtest_fn: Optional[Callable[[], speed_mod.Speed]] = None,
    no_speedtest: bool = False,
    walk_id: Optional[int] = None,
    ssid_id: Optional[int] = None,
) -> SnapshotResult:
    """Sample signal, run speedtest (unless skipped), insert reading.

    Samples via ``signal_mod.sample_signal`` (5s average) BEFORE the
    speedtest. Always opens/closes its OWN ``store.get_db`` connection —
    never share the UI thread's connection (row_factory toggling is not
    thread-safe). ``NoWiFiError`` → ok=False + "NO-WIFI" message; other
    sampling errors → empty Signal (UNKNOWN row). Speedtest fail →
    NULLs + server="ERROR", signal kept (spec 4). Missing binary →
    signal-only row + warning message (same as scan). DB error →
    ok=False + error message (caller toasts, walk continues).
    """
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
            sp = fn()
            ping_ms, down, up, server = (
                sp.ping_ms, sp.down_mbps, sp.up_mbps, sp.server)
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
            rid = store_mod.add_reading(
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
                ping_ms=ping_ms, down_mbps=down,
                up_mbps=up,
                server=server,
                walk_id=walk_id,
            )
        except (sqlite3.Error, OSError, ValueError) as exc:
            return SnapshotResult(ok=False, message="DB error: %s" % (exc,))
    finally:
        conn.close()
    msg = "saved #%d" % rid
    if not no_speedtest:
        msg += " down %s up %s Mbps" % (fmt_mbps(down), fmt_mbps(up))
    if notice:
        msg += " (%s)" % notice
    return SnapshotResult(ok=True, reading_id=rid, message=msg,
                          ping_ms=ping_ms, down_mbps=down, up_mbps=up,
                          rssi=sig.rssi, snr=sig.snr)


def start_snapshot_thread(
    db_path: str,
    location_id: int,
    no_speedtest: bool = False,
    ssid_override: Optional[str] = None,
    on_done: Optional[Callable[[SnapshotResult], None]] = None,
    run_speedtest_fn: Optional[Callable[[], speed_mod.Speed]] = None,
    walk_id: Optional[int] = None,
    ssid_id: Optional[int] = None,
) -> "threading.Thread":
    """Spawn daemon thread: sample signal + insert on its own connection.

    The worker calls ``finish_snapshot`` (samples + opens its own DB
    connection), so the UI thread only hands over ids/overrides.
    """

    def _work() -> None:
        res = finish_snapshot(
            db_path, location_id,
            ssid_override=ssid_override,
            ssid_id=ssid_id,
            run_speedtest_fn=run_speedtest_fn,
            no_speedtest=no_speedtest,
            walk_id=walk_id,
        )
        if on_done is not None:
            on_done(res)

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    return t


def _finish_benchmark(
    db_path: str,
    location_id: int,
    note: str = "",
    ssid_override: Optional[str] = None,
    run_speedtest_fn: Optional[Callable[[], speed_mod.Speed]] = None,
    no_speedtest: bool = False,
    ssid_id: Optional[int] = None,
) -> SnapshotResult:
    """Sample signal, run speedtest (unless skipped), upsert benchmark.

    Mirrors ``finish_snapshot`` but writes the per-location benchmark row
    instead of a reading. Always opens/closes its OWN ``store.get_db``
    connection — never share the UI thread's connection.
    """
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
            sp = fn()
            ping_ms, down, up, server = (
                sp.ping_ms, sp.down_mbps, sp.up_mbps, sp.server)
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
                ping_ms=ping_ms, down_mbps=down,
                up_mbps=up,
                server=server,
                note=note or None,
            )
        except (sqlite3.Error, OSError, ValueError) as exc:
            return SnapshotResult(ok=False, message="DB error: %s" % (exc,))
    finally:
        conn.close()

    def _d(v: object) -> str:
        return "-" if v is None else str(v)

    msg = "benchmark set for #%d rssi=%s snr=%s down=%s up=%s" % (
        location_id, _d(sig.rssi), _d(sig.snr),
        fmt_mbps(down), fmt_mbps(up))
    if notice:
        msg += " (%s)" % notice
    return SnapshotResult(ok=True, message=msg, ping_ms=ping_ms,
                          down_mbps=down, up_mbps=up,
                          rssi=sig.rssi, snr=sig.snr)


# ---------------------------------------------------------------------------
# Walk state (shared by curses + fallback loops)
# ---------------------------------------------------------------------------

class WalkState:
    """Mutable walk session: active spot, last signal, toast, counters.

    ``active_location_id`` is the preset location scoping the room/spot
    drilldown; ``active_spot_id`` is the snapshot target. ``active_id``
    stays as an alias of ``active_spot_id`` for compat.

    ``toast``/``pending`` are shared between the UI thread and snapshot
    worker threads; all mutation and UI reads go through the lock.
    """

    def __init__(self, db_path: str, no_speedtest: bool = False,
                 ssid_override: Optional[str] = None,
                 history_max: int = 60,
                 traffic_fn: Optional[Callable[[], traffic_mod.Rates]] = None,
                 current_walk_id: Optional[int] = None,
                 comparison_window: int = 5,
                 ssid_id: Optional[int] = None,
                 ssid_name: Optional[str] = None) -> None:
        self.db_path = db_path
        self.no_speedtest = no_speedtest
        self.current_walk_id = current_walk_id
        selected_name = ssid_name if ssid_name is not None else ssid_override
        self.ssid_override = (
            selected_name.strip() or None
            if selected_name is not None else None
        )
        self.ssid_id = ssid_id
        self.history_max = max(1, history_max)
        self.comparison_window = max(1, comparison_window)
        self.hist_rssi: SparkHistory = SparkHistory(maxlen=self.history_max)
        self.hist_noise: SparkHistory = SparkHistory(maxlen=self.history_max)
        self.hist_snr: SparkHistory = SparkHistory(maxlen=self.history_max)
        self.hist_down: SparkHistory = SparkHistory(maxlen=self.history_max)
        self.hist_up: SparkHistory = SparkHistory(maxlen=self.history_max)
        self.last_rates: traffic_mod.Rates = None
        self._traffic_fn = traffic_fn
        self._traffic_sampler: Optional[traffic_mod.TrafficSampler] = (
            None if traffic_fn is not None else traffic_mod.TrafficSampler())
        self.active_location_id: Optional[int] = None
        self.active_spot_id: Optional[int] = None
        self.baseline_walk_id: Optional[int] = None
        self.baseline_walk_name: Optional[str] = None
        self._baseline_by_spot: Dict[int, SpotBaseline] = {}
        self._comparison_generation = 0
        self._comparison_signal: Dict[str, deque] = {
            key: deque(maxlen=self.comparison_window)
            for key in ("rssi", "noise", "snr", "tx_rate")
        }
        self._speed_probe: Optional[SpeedProbeResult] = None
        self._speed_probe_running = False
        self._workers: List[object] = []
        self.sig: signal_mod.Signal = signal_mod.Signal()
        self.no_wifi: bool = False
        self.no_wifi_msg: str = ""
        self.toast: str = ""
        self.pending: int = 0
        self.last_result: str = ""
        self.benchmark: Optional[dict] = None
        self.net_ssid: Optional[str] = None
        self.net_bssid: Optional[str] = None
        self.ip: Optional[str] = None
        self.router: Optional[str] = None
        self.mac: Optional[str] = None
        self._addrs_done: bool = False
        self._lock = threading.Lock()

    @property
    def active_id(self) -> Optional[int]:
        """Alias of ``active_spot_id`` (snapshot target)."""
        return self.active_spot_id

    @active_id.setter
    def active_id(self, value: Optional[int]) -> None:
        self.set_active_spot(value)

    def set_active_spot(self, spot_id: Optional[int]) -> None:
        """Change snapshot target and reset spot-specific live comparison."""
        with self._lock:
            if spot_id == self.active_spot_id:
                return
            self.active_spot_id = spot_id
            self._comparison_generation += 1
            for values in self._comparison_signal.values():
                values.clear()
            self._speed_probe = None
            if self.pending:
                self.toast = ""
            self.last_result = ""

    @staticmethod
    def _median(values) -> Optional[float]:
        numeric = []
        for value in values:
            if value is None or isinstance(value, bool):
                continue
            try:
                numeric.append(float(value))
            except (TypeError, ValueError):
                continue
        return (float(statistics.median(numeric)) if numeric else None)

    def select_baseline_walk(
        self,
        conn: sqlite3.Connection,
        id_or_name: Union[int, str],
    ) -> int:
        """Select and cache a prior walk in the active location.

        A duplicate name is rejected in favour of an explicit id. All
        readings are loaded once and reduced to per-spot medians so the live
        poll loop never queries SQLite.
        """
        location_id = self.active_location_id
        if location_id is None and self.current_walk_id is not None:
            current = store_mod.get_walk(conn, self.current_walk_id)
            if current is not None:
                location_id = current.location_id
                self.active_location_id = location_id
        if location_id is None:
            raise ValueError("comparison requires an active location")

        all_walks = store_mod.list_walks(conn, location_id=location_id)
        nonempty = _nonempty_walk_ids(conn)
        walks = [walk for walk in all_walks if walk.id in nonempty]
        selected = None
        if isinstance(id_or_name, bool):
            raise ValueError("invalid baseline walk: %r" % (id_or_name,))
        if isinstance(id_or_name, int):
            selected = next((walk for walk in walks
                             if walk.id == id_or_name), None)
        else:
            label = str(id_or_name).strip()
            try:
                walk_id = int(label)
            except ValueError:
                walk_id = None
            if walk_id is not None:
                selected = next((walk for walk in walks
                                 if walk.id == walk_id), None)
            if selected is None:
                named = [walk for walk in walks if walk.name == label]
                if len(named) > 1:
                    ids = ", ".join(str(walk.id) for walk in named)
                    raise ValueError(
                        "ambiguous baseline walk name %r; use IDs: %s"
                        % (label, ids))
                selected = named[0] if named else None
        if selected is None:
            known_empty = None
            if isinstance(id_or_name, int):
                known_empty = next((walk for walk in all_walks
                                    if walk.id == id_or_name), None)
            else:
                requested = str(id_or_name).strip()
                known_empty = next((walk for walk in all_walks if (
                    str(walk.id) == requested or walk.name == requested)),
                    None)
            if known_empty is not None and known_empty.id not in nonempty:
                raise ValueError("baseline walk has no readings: #%d" % (
                    known_empty.id,))
            raise ValueError("unknown baseline walk: %r" % (id_or_name,))
        if selected.id == self.current_walk_id:
            raise ValueError("current walk cannot be its own baseline")

        rows = store_mod.list_readings(
            conn, walk_id=selected.id, limit=1_000_000)
        grouped: Dict[int, List[dict]] = {}
        for row in rows:
            grouped.setdefault(int(row["spot_id"]), []).append(row)

        cache = {}
        numeric_keys = (
            "rssi", "noise", "snr", "tx_rate", "ping_ms",
            "down_mbps", "up_mbps",
        )
        for spot_id, spot_rows in grouped.items():
            newest = spot_rows[0]
            numeric = {
                key: self._median(row.get(key) for row in spot_rows)
                for key in numeric_keys
            }
            cache[spot_id] = SpotBaseline(
                spot_id=spot_id,
                ssid=newest.get("ssid"), bssid=newest.get("bssid"),
                channel=newest.get("channel"), server=newest.get("server"),
                **numeric,
            )
        with self._lock:
            self.baseline_walk_id = selected.id
            self.baseline_walk_name = selected.name
            self._baseline_by_spot = cache
            self._comparison_generation += 1
            self._speed_probe = None
        return selected.id

    def clear_baseline_walk(self) -> None:
        with self._lock:
            self.baseline_walk_id = None
            self.baseline_walk_name = None
            self._baseline_by_spot = {}
            self._comparison_generation += 1
            self._speed_probe = None

    def baseline_for_active_spot(self) -> Optional[SpotBaseline]:
        with self._lock:
            return self._baseline_by_spot.get(self.active_spot_id)

    def speed_probe_for_active_spot(self) -> Optional[SpeedProbeResult]:
        with self._lock:
            probe = self._speed_probe
            if probe is None or probe.spot_id != self.active_spot_id:
                return None
            return probe

    def wait_for_workers(self, timeout: float = WORKER_DRAIN_TIMEOUT) -> bool:
        """Drain tracked snapshot/probe workers within one total deadline."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._lock:
            workers = tuple(self._workers)
        for worker in workers:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                worker.join(timeout=remaining)  # type: ignore[attr-defined]
            except Exception:
                return False
            try:
                if worker.is_alive():  # type: ignore[attr-defined]
                    return False
            except AttributeError:
                pass
        return True

    def active_comparison(self) -> Dict[str, MetricComparison]:
        """Return live/probe values compared with the active spot baseline."""
        with self._lock:
            baseline = self._baseline_by_spot.get(self.active_spot_id)
            current = {
                key: self._median(values)
                for key, values in self._comparison_signal.items()
            }
            probe = self._speed_probe
            if probe is None or probe.spot_id != self.active_spot_id:
                probe = None
            current.update({
                "ping_ms": probe.ping_ms if probe else None,
                "down_mbps": probe.down_mbps if probe else None,
                "up_mbps": probe.up_mbps if probe else None,
            })
        out = {}
        for key, value in current.items():
            before = getattr(baseline, key) if baseline is not None else None
            delta = (value - before
                     if value is not None and before is not None else None)
            out[key] = MetricComparison(value, before, delta)
        return out

    def ensure_addrs(
        self,
        addrs_fn: Optional[Callable[
            [], Optional[Tuple[Optional[str], Optional[str], Optional[str]]]]] = None,
    ) -> Optional[Tuple[Optional[str], Optional[str], Optional[str]]]:
        """One-shot session addr lookup (ip, router, mac); never raise."""
        if self._addrs_done:
            return (self.ip, self.router, self.mac)
        self._addrs_done = True
        fn = addrs_fn or signal_mod.read_local_addrs
        try:
            found = fn()
        except Exception:  # noqa: BLE001 - addr lookup is best-effort
            found = None
        if found is None:
            self.ip, self.router, self.mac = None, None, None
            return None
        try:
            self.ip, self.router, self.mac = found[0], found[1], found[2]
        except Exception:
            self.ip, self.router, self.mac = None, None, None
            return None
        if not self.ip and not self.router and not self.mac:
            return None
        return (self.ip, self.router, self.mac)

    def ensure_identity(
        self,
        identity_fn: Optional[
            Callable[[], Optional[Tuple[str, Optional[str]]]]
        ] = None,
    ) -> Optional[Tuple[str, Optional[str]]]:
        """One-shot session lookup; backfills polls; abort → toast + None."""
        if self.ssid_override is not None:
            self.net_ssid = self.ssid_override
            self._backfill_identity()
            return (self.net_ssid, self.net_bssid)
        fn = identity_fn or signal_mod.read_network_identity
        try:
            ident = fn()
        except Exception:  # noqa: BLE001 - abort/failure means unknown
            ident = None
        if ident is None:
            self.net_ssid = None
            self.net_bssid = None
            self.set_toast(
                "network name unavailable (sudo skipped); showing Net: unknown")
            return None
        self.net_ssid, self.net_bssid = ident[0], ident[1]
        self._backfill_identity()
        return ident

    def _backfill_identity(self) -> None:
        if self.ssid_override is not None:
            self.sig.ssid = self.ssid_override
        elif self.net_ssid is not None and self.sig.ssid is None:
            self.sig.ssid = self.net_ssid
        if self.net_bssid is not None and self.sig.bssid is None:
            self.sig.bssid = self.net_bssid

    def set_toast(self, msg: str) -> None:
        with self._lock:
            self.toast = msg

    def ui_snapshot(self) -> Tuple[str, int]:
        """Locked (toast, pending) read for the render loop."""
        with self._lock:
            return (self.toast, self.pending)

    def on_snapshot_done(self, res: SnapshotResult) -> None:
        with self._lock:
            self.pending = max(0, self.pending - 1)
            self.toast = (res.message if res.message else (
                "saved #%s" % res.reading_id))
            self.last_result = self.toast

    def refresh_benchmark(self, conn: sqlite3.Connection) -> None:
        """Reload benchmark for the active location; keeps old on DB error."""
        lid = self.active_location_id
        if lid is None:
            with self._lock:
                self.benchmark = None
            return
        try:
            bench = store_mod.get_benchmark(conn, lid)
        except Exception:  # noqa: BLE001 - bench line is best-effort
            return
        with self._lock:
            self.benchmark = bench

    def poll(self, read_fn: Optional[Callable[[], signal_mod.Signal]] = None) -> None:
        fn = read_fn or signal_mod.read_signal
        try:
            self.sig = attempt_read(fn)
            self.no_wifi = False
            self.no_wifi_msg = ""
            self._backfill_identity()
        except signal_mod.NoWiFiError as exc:
            self.no_wifi = True
            self.no_wifi_msg = str(exc)
            self.hist_rssi.append(None)
            self.hist_noise.append(None)
            self.hist_snr.append(None)
            return
        self.hist_rssi.append(self.sig.rssi)
        self.hist_noise.append(self.sig.noise)
        self.hist_snr.append(self.sig.snr)
        with self._lock:
            self._comparison_signal["rssi"].append(self.sig.rssi)
            self._comparison_signal["noise"].append(self.sig.noise)
            self._comparison_signal["snr"].append(self.sig.snr)
            self._comparison_signal["tx_rate"].append(self.sig.tx_rate)
        rates: traffic_mod.Rates = None
        try:
            if self._traffic_fn is not None:
                rates = self._traffic_fn()
            elif self._traffic_sampler is not None:
                rates = self._traffic_sampler.sample()
        except Exception:  # noqa: BLE001 - traffic graph is best-effort
            rates = None
        self.last_rates = rates
        self.hist_down.append(rates[0] if rates else None)
        self.hist_up.append(rates[1] if rates else None)

    def try_speed_probe(
        self,
        run_speedtest_fn: Optional[Callable[[], speed_mod.Speed]] = None,
    ) -> Optional["threading.Thread"]:
        """Run an asynchronous transient speedtest without storing a row."""
        with self._lock:
            if self.no_wifi:
                self.toast = "NO-WIFI: `t` blocked (WiFi off/not associated)"
                return None
            if self.active_spot_id is None:
                self.toast = "no active spot: press `l` to pick one"
                return None
            if self.no_speedtest:
                self.toast = "throughput probe disabled by --no-speedtest"
                return None
            if self._speed_probe_running or self.pending:
                self.toast = "another measurement is already running"
                return None
            spot_id = self.active_spot_id
            generation = self._comparison_generation
            self._speed_probe_running = True
            self.pending += 1
            self.toast = "throughput probe running; stay at this spot"
        fn = run_speedtest_fn or speed_mod.run_speedtest

        def _work() -> None:
            try:
                speed = fn()
                result = SpeedProbeResult(
                    ok=True, spot_id=spot_id, generation=generation,
                    ping_ms=speed.ping_ms, down_mbps=speed.down_mbps,
                    up_mbps=speed.up_mbps, server=speed.server,
                    message="probe down %s up %s Mbps" % (
                        fmt_mbps(speed.down_mbps), fmt_mbps(speed.up_mbps)),
                )
            except speed_mod.SpeedtestUnavailableError as exc:
                result = SpeedProbeResult(
                    ok=False, spot_id=spot_id, generation=generation,
                    message="throughput probe unavailable: %s" % exc)
            except speed_mod.SpeedtestFailedError as exc:
                result = SpeedProbeResult(
                    ok=False, spot_id=spot_id, generation=generation,
                    message="throughput probe failed: %s" % exc)
            except Exception as exc:  # noqa: BLE001 - TUI worker survives
                result = SpeedProbeResult(
                    ok=False, spot_id=spot_id, generation=generation,
                    message="throughput probe failed: %s" % exc)
            with self._lock:
                self._speed_probe_running = False
                self.pending = max(0, self.pending - 1)
                if (result.generation == self._comparison_generation
                        and result.spot_id == self.active_spot_id):
                    self._speed_probe = result if result.ok else None
                    self.toast = result.message

        thread = threading.Thread(target=_work, daemon=True)
        thread.start()
        with self._lock:
            self._workers.append(thread)
        return thread

    def try_snapshot(
        self,
        run_speedtest_fn: Optional[Callable[[], speed_mod.Speed]] = None,
    ) -> Optional["threading.Thread"]:
        """Spawn snapshot thread for active spot; None + hint if blocked.

        The worker samples the signal itself (5s average) on its own
        thread; the UI thread keeps live meters untouched.
        """
        if self.no_wifi:
            self.set_toast("NO-WIFI: `s` blocked (WiFi off/not associated)")
            return None
        if self.active_spot_id is None:
            self.set_toast("no active spot: press `l` to pick one")
            return None
        with self._lock:
            if self.pending or self._speed_probe_running:
                self.toast = "another measurement is already running"
                return None
            self.pending += 1
            generation = self._comparison_generation
        loc_id = self.active_spot_id
        result_box: List[SnapshotResult] = []

        def _cb(res: SnapshotResult) -> None:
            result_box.append(res)
            with self._lock:
                self.pending = max(0, self.pending - 1)
                if (generation != self._comparison_generation
                        or loc_id != self.active_spot_id):
                    return
                self.toast = (res.message if res.message else (
                    "saved #%s" % res.reading_id))
                self.last_result = self.toast
            if res.ok:
                with self._lock:
                    if (generation == self._comparison_generation
                            and loc_id == self.active_spot_id
                            and any(value is not None for value in (
                                res.ping_ms, res.down_mbps, res.up_mbps))):
                        self._speed_probe = SpeedProbeResult(
                            ok=True, spot_id=loc_id,
                            generation=generation, ping_ms=res.ping_ms,
                            down_mbps=res.down_mbps, up_mbps=res.up_mbps,
                            message=res.message)
                try:
                    cur = {"rssi": res.rssi,
                           "snr": res.snr,
                           "down_mbps": res.down_mbps,
                           "up_mbps": res.up_mbps}
                    with self._lock:
                        bench = self.benchmark
                    delta = store_mod.format_benchmark_delta(cur, bench)
                    if delta:
                        with self._lock:
                            if (generation == self._comparison_generation
                                    and loc_id == self.active_spot_id):
                                self.toast = "%s vs bench (%s)" % (
                                    self.toast, delta)
                                self.last_result = "%s vs bench (%s)" % (
                                    self.last_result, delta)
                except Exception:  # noqa: BLE001 - delta is best-effort
                    pass

        self.set_toast("snapshot running (speedtest)...")
        t = start_snapshot_thread(
            self.db_path, loc_id,
            no_speedtest=self.no_speedtest,
            ssid_override=self.ssid_override,
            ssid_id=self.ssid_id,
            on_done=_cb,
            run_speedtest_fn=run_speedtest_fn,
            walk_id=self.current_walk_id,
        )
        with self._lock:
            self._workers.append(t)
        return t

    def set_floor(self, conn: sqlite3.Connection, floor: int) -> str:
        """Update active spot's room floor; returns toast message."""
        if self.active_spot_id is None:
            return "no active spot"
        try:
            spot = store_mod.get_spot(conn, self.active_spot_id)
        except (sqlite3.Error, OSError) as exc:
            return "DB error: %s" % (exc,)
        if spot is None:
            return "unknown spot id: %r" % (self.active_spot_id,)
        try:
            store_mod.update_room_floor(conn, spot.room_id, floor)
        except ValueError:
            return "unknown room id: %r" % (spot.room_id,)
        except (sqlite3.Error, OSError) as exc:
            return "DB error: %s" % (exc,)
        return "floor set to %d" % floor


_COMPARE_ROWS = (
    ("RSSI", "rssi", "dBm", 0),
    ("noise", "noise", "dBm", 0),
    ("SNR", "snr", "dB", 0),
    ("TX rate", "tx_rate", "Mbps", 0),
    ("ping", "ping_ms", "ms", 1),
    ("down", "down_mbps", "Mbps", 1),
    ("up", "up_mbps", "Mbps", 1),
)


def _compare_number(value: Optional[float], decimals: int,
                    signed: bool = False) -> str:
    if value is None:
        return "-"
    if decimals == 0:
        return (("%+d" if signed else "%d") % int(round(value)))
    return (("%+.*f" if signed else "%.*f") % (decimals, value))


def comparison_lines(state: "WalkState") -> List[str]:
    """Compact current/baseline/delta table for curses and fallback."""
    if state.baseline_walk_id is None:
        return []
    label = state.baseline_walk_name or str(state.baseline_walk_id)
    lines = [
        "compare: current walk #%s vs #%s %s" % (
            state.current_walk_id if state.current_walk_id is not None else "-",
            state.baseline_walk_id, label),
        "metric       current       before       change",
    ]
    comparison = state.active_comparison()
    for metric_label, key, unit, decimals in _COMPARE_ROWS:
        item = comparison[key]
        current = _compare_number(item.current, decimals)
        before = _compare_number(item.baseline, decimals)
        delta = _compare_number(item.delta, decimals, signed=True)
        lines.append("%-7s %9s %-5s %9s %-5s %9s" % (
            metric_label, current, unit, before, unit, delta))
    if state.active_spot_id is not None and state.baseline_for_active_spot() is None:
        lines.append("baseline: this spot was not measured")
    return lines


def _nonempty_walk_ids(conn: sqlite3.Connection) -> set:
    rows = conn.execute(
        "SELECT DISTINCT walk_id FROM readings WHERE walk_id IS NOT NULL"
    ).fetchall()
    return {int(row[0]) for row in rows}


def _baseline_walk_rows(
    conn: sqlite3.Connection,
    state: "WalkState",
) -> List[Tuple[int, str]]:
    """Prior-walk picker rows followed by an explicit Off choice."""
    location_id = state.active_location_id
    if location_id is None and state.current_walk_id is not None:
        current = store_mod.get_walk(conn, state.current_walk_id)
        location_id = current.location_id if current is not None else None
    walks = (store_mod.list_walks(conn, location_id=location_id)
             if location_id is not None else [])
    nonempty = _nonempty_walk_ids(conn)
    rows = [
        (walk.id, "%s | %s | #%d" % (
            walk.name, walk.started_at, walk.id))
        for walk in walks
        if walk.id != state.current_walk_id and walk.id in nonempty
    ]
    rows.append((0, "Off"))
    return rows


def _start_walk_session(
    conn: sqlite3.Connection,
    state: "WalkState",
    walk_name: Optional[str],
    compare_to: Optional[Union[int, str]],
) -> None:
    """Create this invocation's named walk and optionally cache a baseline."""
    if state.active_location_id is None:
        return
    name = ((walk_name or "").strip()
            or time.strftime("walk %Y-%m-%d %H:%M:%S"))
    state.current_walk_id = store_mod.create_walk(
        conn, state.active_location_id, name, ssid_id=state.ssid_id)
    if compare_to is None:
        return
    target: Union[int, str] = compare_to
    if str(compare_to).strip().lower() == "latest":
        prior = [walk_id for walk_id, _label in _baseline_walk_rows(
            conn, state) if walk_id != 0]
        if not prior:
            raise ValueError("no prior walk available for comparison")
        target = prior[0]
    state.select_baseline_walk(conn, target)


def _finish_walk_session(conn: sqlite3.Connection,
                         state: "WalkState",
                         worker_timeout: float = WORKER_DRAIN_TIMEOUT) -> bool:
    if state.current_walk_id is None:
        return True
    if not state.wait_for_workers(timeout=worker_timeout):
        print("Warning: measurement workers still running; "
              "walk left open", file=sys.stderr)
        return False
    try:
        store_mod.finish_walk(conn, state.current_walk_id)
    except (sqlite3.Error, OSError, ValueError) as exc:
        print("Warning: cannot finish walk: %s" % exc, file=sys.stderr)
        return False
    return True


def _resolve_preset(conn: sqlite3.Connection, preset: Optional[str]) -> Optional[int]:
    if preset is None:
        return None
    try:
        return store_mod.resolve_location(conn, preset)
    except (sqlite3.Error, OSError, ValueError):
        return None


def _location_label(conn: sqlite3.Connection, spot_id: Optional[int]) -> str:
    if spot_id is None:
        return "(none)"
    try:
        spot = store_mod.get_spot(conn, spot_id)
        if spot is None:
            return "#%s (deleted?)" % spot_id
        room = store_mod.get_room(conn, spot.room_id)
        if room is None:
            return "#%s (deleted?)" % spot_id
        loc = store_mod.get_location(conn, room.location_id)
        if loc is None:
            return "#%s (deleted?)" % spot_id
    except (sqlite3.Error, OSError, ValueError):
        return "#%s (db error)" % spot_id
    return "#%s %s/%s/%s (floor %s)" % (
        spot.id, loc.name, room.name, spot.name, room.floor)


# ---------------------------------------------------------------------------
# Curses loop
# ---------------------------------------------------------------------------

def _list_picker_curses(stdscr: object, title: str,
                        rows: List[Tuple[int, str]],
                        active: Optional[int],
                        create_label: str,
                        poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS,
                        ) -> Optional[PickerSelection]:
    """Generic numbered-list picker prefilled with active.

    Positions ``0..count-1`` are rows, ``count`` is the create row.
    Returns id/``"new"``/None (cancel). Cursor starts on the active row
    so a single Enter confirms instantly for fast walkthroughs.
    """
    import curses

    ids = [rid for rid, _ in rows]
    cursor = picker_start_cursor(ids, active)
    stdscr.timeout(-1)  # type: ignore[attr-defined]  # blocking for picker
    try:
        while True:
            h, w = stdscr.getmaxyx()  # type: ignore[attr-defined]
            lines = ["%s (Enter=active, 1-%d, + new, q cancel):"
                     % (title, len(rows))]
            for i, (rid, label) in enumerate(rows):
                cur = ">" if i == cursor else " "
                mark = "*" if rid == active else " "
                lines.append("%s%s%d. %s" % (cur, mark, i + 1, label))
            lines.append("%s  +. <%s>" % (
                ">" if cursor == len(rows) else " ", create_label))
            stdscr.clear()  # type: ignore[attr-defined]
            for r, ln in enumerate(lines[: h - 1]):
                try:
                    stdscr.addstr(r, 0, ln[: w - 1])  # type: ignore[attr-defined]
                except Exception:
                    pass
            stdscr.refresh()  # type: ignore[attr-defined]
            ch = stdscr.getch()  # type: ignore[attr-defined]
            if ch == curses.KEY_UP:
                key = "up"
            elif ch == curses.KEY_DOWN:
                key = "down"
            elif ch in KEY_ENTER_CODES + (curses.KEY_ENTER,):
                key = "\n"
            elif ch == KEY_ESC:
                key = "\x1b"
            else:
                try:
                    key = chr(ch)
                except (ValueError, OverflowError):
                    continue
            cursor, action, idx = picker_press(key, cursor, len(rows))
            if action == "cancel":
                return None
            if action == "confirm" and idx is not None:
                if idx == len(rows):
                    return "new"
                return rows[idx][0]
            # "move"/"ignore" → re-render with updated cursor
    finally:
        stdscr.timeout(poll_timeout_ms)  # type: ignore[attr-defined]


def _baseline_picker_curses(
    stdscr: object,
    conn: sqlite3.Connection,
    state: "WalkState",
    poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS,
) -> Optional[int]:
    """Pick a prior walk or the explicit Off row; None means cancel."""
    import curses

    rows = _baseline_walk_rows(conn, state)
    ids = [walk_id for walk_id, _label in rows]
    active = (state.baseline_walk_id
              if state.baseline_walk_id is not None else 0)
    cursor = picker_start_cursor(ids, active)
    stdscr.timeout(-1)  # type: ignore[attr-defined]
    try:
        while True:
            h, w = stdscr.getmaxyx()  # type: ignore[attr-defined]
            visible_count = max(1, h - 2)
            start = max(0, min(
                cursor - visible_count // 2,
                max(0, len(rows) - visible_count)))
            end = min(len(rows), start + visible_count)
            lines = [
                "Compare with prior walk (Enter select, q cancel) "
                "[%d-%d/%d]:" % (start + 1, end, len(rows))
            ]
            for index in range(start, end):
                walk_id, label = rows[index]
                marker = ">" if index == cursor else " "
                selected = "*" if walk_id == active else " "
                lines.append("%s%s%d. %s" % (
                    marker, selected, index + 1, label))
            stdscr.clear()  # type: ignore[attr-defined]
            for row, line in enumerate(lines[:h - 1]):
                try:
                    stdscr.addstr(
                        row, 0, line[:w - 1])  # type: ignore[attr-defined]
                except Exception:
                    pass
            stdscr.refresh()  # type: ignore[attr-defined]
            ch = stdscr.getch()  # type: ignore[attr-defined]
            if ch == curses.KEY_UP:
                cursor = (cursor - 1) % len(rows)
            elif ch == curses.KEY_DOWN:
                cursor = (cursor + 1) % len(rows)
            elif ch in KEY_ENTER_CODES + (curses.KEY_ENTER,):
                return rows[cursor][0]
            elif ch in (KEY_ESC, ord("q"), ord("Q")):
                return None
            else:
                try:
                    digit = int(chr(ch))
                except (ValueError, OverflowError):
                    continue
                if 1 <= digit <= len(rows):
                    return rows[digit - 1][0]
    finally:
        stdscr.timeout(poll_timeout_ms)  # type: ignore[attr-defined]


def _room_picker_curses(stdscr: object, conn: sqlite3.Connection,
                        location_id: int,
                        active_room: Optional[int],
                        poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS,
                        ) -> Optional[PickerSelection]:
    """Room picker scoped to a location; ``"new"`` means create a room."""
    rooms = store_mod.list_rooms(conn, location_id=location_id)
    rows = [(rm.id, "%s (floor %d%s)" % (
        rm.name, rm.floor, ", outdoors" if rm.outdoors else ""))
        for rm in rooms]
    return _list_picker_curses(stdscr, "Pick room", rows, active_room,
                               "new room", poll_timeout_ms)


def _spot_picker_curses(stdscr: object, conn: sqlite3.Connection,
                        room_id: int,
                        active_spot: Optional[int],
                        poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS,
                        ) -> Optional[PickerSelection]:
    """Spot picker scoped to a room; ``"new"`` means create a spot."""
    spots = store_mod.list_spots(conn, room_id=room_id)
    rows = [(sp.id, sp.name) for sp in spots]
    return _list_picker_curses(stdscr, "Pick spot", rows, active_spot,
                               "new spot", poll_timeout_ms)


def _pick_room_spot_curses(
    stdscr: object, conn: sqlite3.Connection, state: "WalkState",
    poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS,
) -> bool:
    """Drilldown: pick room in active location, then spot in that room.

    Returns True when a spot is selected (state.active_spot_id set),
    False on cancel at either level.
    """
    if state.active_location_id is None:
        state.set_toast("no preset location: rerun with --location")
        return False
    active_room = _active_room_id(conn, state)
    try:
        picked_room = _room_picker_curses(
            stdscr, conn, state.active_location_id, active_room,
            poll_timeout_ms)
    except (sqlite3.Error, OSError) as exc:
        state.set_toast("DB error: %s" % (exc,))
        return False
    if picked_room == "new":
        picked_room, note = _create_room_curses(
            stdscr, conn, state.active_location_id, poll_timeout_ms)
        state.set_toast(note)
    if not isinstance(picked_room, int):
        return False
    try:
        picked_spot = _spot_picker_curses(
            stdscr, conn, picked_room, state.active_spot_id,
            poll_timeout_ms)
    except (sqlite3.Error, OSError) as exc:
        state.set_toast("DB error: %s" % (exc,))
        return False
    if picked_spot == "new":
        picked_spot = _create_spot_curses(
            stdscr, conn, picked_room, poll_timeout_ms)
    if not isinstance(picked_spot, int):
        state.set_toast("spot pick cancelled")
        return False
    state.set_active_spot(picked_spot)
    return True


def _prompt_curses(stdscr: object, prompt: str,
                   poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS) -> str:
    """One-line prompt via curses echo; falls back to '' on error.

    Blocks for input (``timeout(-1)``): the walk loop's poll timeout
    would otherwise abort ``getstr`` after ~1s, making typing impossible.
    Restores the poll timeout on exit.
    """
    import curses

    h, _w = stdscr.getmaxyx()  # type: ignore[attr-defined]
    stdscr.timeout(-1)  # type: ignore[attr-defined]  # blocking for typing
    try:
        curses.echo()  # type: ignore[attr-defined]
        stdscr.addstr(h - 1, 0, prompt[: _w - 1])  # type: ignore[attr-defined]
        stdscr.clrtoeol()  # type: ignore[attr-defined]
        stdscr.refresh()  # type: ignore[attr-defined]
        raw = stdscr.getstr(h - 1, min(len(prompt), _w - 1))  # type: ignore[attr-defined]
        return raw.decode("utf-8", "replace")
    except Exception:
        return ""
    finally:
        try:
            curses.noecho()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            stdscr.timeout(poll_timeout_ms)  # type: ignore[attr-defined]
        except Exception:
            pass


def _create_room_curses(
    stdscr: object, conn: sqlite3.Connection,
    location_id: int,
    poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS,
) -> Tuple[Optional[int], str]:
    """Prompt name/floor/outdoors; return (room_id, note).

    room_id None means cancel/failure; note explains the outcome
    ("room created" on success, "room exists (floor N); adding spot
    there" when a same-name room was reused on UNIQUE collision).
    """
    name = _prompt_curses(stdscr, "room name: ", poll_timeout_ms).strip()
    if not name:
        return None, "new room cancelled"
    floor_s = _prompt_curses(stdscr, "floor (int): ", poll_timeout_ms).strip()
    try:
        floor = parse_floor_input(floor_s or "0")
    except ValueError as exc:
        return None, "cancelled/invalid: %s" % (exc,)
    od_s = _prompt_curses(stdscr, "outdoors? [y/N]: ", poll_timeout_ms).strip().lower()
    outdoors = od_s in ("y", "yes", "1")
    try:
        room_id = store_mod.create_room(
            conn, location_id, name, floor=floor, outdoors=outdoors)
    except sqlite3.IntegrityError:
        existing = _find_room_by_name(conn, location_id, name)
        if existing is None:
            return None, "room create failed: duplicate room"
        rid, efloor = existing
        return rid, "room exists (floor %d); adding spot there" % efloor
    except (sqlite3.Error, OSError, ValueError) as exc:
        return None, "room create failed: %s" % (exc,)
    return room_id, "room created"


def _create_spot_curses(
    stdscr: object, conn: sqlite3.Connection,
    room_id: int,
    poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS,
) -> Optional[int]:
    name = _prompt_curses(stdscr, "spot name: ", poll_timeout_ms).strip()
    if not name:
        return None
    try:
        return store_mod.create_spot(conn, room_id, name)
    except (sqlite3.Error, OSError, ValueError):
        return None


def _walk_curses(stdscr: object, db_path: str, interval: float,
                 location_preset: Optional[str],
                 no_speedtest: bool,
                 ssid: Optional[str] = None,
                 walk_name: Optional[str] = None,
                 compare_to: Optional[Union[int, str]] = None,
                 ssid_id: Optional[int] = None,
                 network_bssid: Optional[str] = None) -> int:
    import curses

    try:
        conn = store_mod.get_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        try:
            stdscr.addstr(0, 0, "DB error: %s" % (exc,))
            stdscr.refresh()
            time.sleep(DB_OPEN_FAIL_SLEEP)
        except Exception:
            pass
        return 3
    cap = history_cap(interval)
    state = WalkState(db_path, no_speedtest=no_speedtest,
                      ssid_id=ssid_id, ssid_name=ssid, history_max=cap)
    state.net_bssid = network_bssid
    try:
        state.active_location_id = _resolve_preset(conn, location_preset)
        if state.active_location_id is not None:
            try:
                _start_walk_session(
                    conn, state, walk_name=walk_name, compare_to=compare_to)
            except (sqlite3.Error, OSError, ValueError) as exc:
                state.set_toast("walk comparison unavailable: %s" % exc)
        state.refresh_benchmark(conn)
        state.ensure_identity()
        state.ensure_addrs()
        if location_preset is not None and state.active_location_id is None:
            state.set_toast(
                "unknown preset %r; press `l` to pick" % (location_preset,))
        if location_preset is None:
            state.set_toast("no preset: rerun with --location or press `l`")
        stdscr.nodelay(False)
        poll_timeout_ms = _poll_timeout_ms(interval)
        stdscr.timeout(poll_timeout_ms)
        try:
            curses.curs_set(0)
        except Exception:
            pass
        has_col = False
        try:
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, -1)
                curses.init_pair(2, curses.COLOR_YELLOW, -1)
                curses.init_pair(3, curses.COLOR_RED, -1)
                has_col = True
        except Exception:
            has_col = False
        while True:
            state.poll()
            # render
            try:
                stdscr.clear()
                h, w = stdscr.getmaxyx()
                row = 0

                def _emit(s: str, attr: int = 0) -> None:
                    nonlocal row
                    if row < h - 1:
                        try:
                            stdscr.addstr(row, 0, s[: w - 1], attr)
                        except Exception:
                            pass
                        row += 1

                def _emit_segs(segs: list) -> None:
                    nonlocal row
                    if row >= h - 1:
                        return
                    col = 0
                    try:
                        for text, attr in segs:
                            if col >= w - 1 or not text:
                                continue
                            chunk = text[: max(0, w - 1 - col)]
                            stdscr.addstr(row, col, chunk, attr)
                            col += len(chunk)
                    except Exception:
                        pass
                    row += 1

                r_pair, _ = rating_style(rate_rssi(state.sig.rssi))
                s_pair, _ = rating_style(rate_snr(state.sig.snr))
                r_attr = curses.color_pair(r_pair) if (has_col and r_pair) else 0
                s_attr = curses.color_pair(s_pair) if (has_col and s_pair) else 0
                if state.no_wifi:
                    _emit("NO-WIFI: %s" % (state.no_wifi_msg,))
                    _emit("`s` blocked; fix WiFi or quit with `q`.")
                else:
                    rssi_s = ("UNKNOWN" if state.sig.rssi is None
                              else "%4d dBm" % state.sig.rssi)
                    snr_s = ("UNKNOWN" if state.sig.snr is None
                             else "%3d dB" % state.sig.snr)
                    noise_s = ("UNKNOWN" if state.sig.noise is None
                               else "%4d dBm" % state.sig.noise)
                    r_rate = rate_rssi(state.sig.rssi)
                    s_rate = rate_snr(state.sig.snr)
                    ch_s = state.sig.channel or "-"
                    phy_s = state.sig.phy or "-"
                    tx_s = state.sig.tx_rate or "-"
                    down_s = fmt_rate_val(
                        state.last_rates[0] if state.last_rates else None)
                    up_s = fmt_rate_val(
                        state.last_rates[1] if state.last_rates else None)
                    r_val_pad = rssi_s.ljust(METER_VAL_W)
                    s_val_pad = snr_s.ljust(METER_VAL_W)
                    n_val_pad = noise_s.ljust(METER_VAL_W)
                    d_val_pad = down_s.ljust(METER_VAL_W)
                    u_val_pad = up_s.ljust(METER_VAL_W)
                    lefts = [
                        format_meter_left("RSSI", rssi_s, r_rate),
                        format_meter_left("SNR", snr_s, s_rate),
                        format_meter_left("noise", noise_s, None),
                        "traffic down " + d_val_pad,
                        "traffic up   " + u_val_pad,
                    ]
                    max_left, gw = meter_layout(w, lefts)
                    pads = [" " * (max_left - len(s)) for s in lefts]
                    if h >= 10:
                        rssi_g = state.hist_rssi.sparkline(-90, -30, gw)
                        snr_g = state.hist_snr.sparkline(0, 40, gw)
                        noise_g = state.hist_noise.sparkline(-100, -60, gw)
                        down_g = state.hist_down.sparkline_auto(gw)
                        up_g = state.hist_up.sparkline_auto(gw)
                        _emit_segs([("RSSI  ", 0),
                                    (r_val_pad + "[%s]" % r_rate, r_attr),
                                    (pads[0] + " | ", 0),
                                    (rssi_g, r_attr), (" [60s]", 0)])
                        _emit_segs([("SNR   ", 0),
                                    (s_val_pad + "[%s]" % s_rate, s_attr),
                                    (pads[1] + " | ", 0),
                                    (snr_g, s_attr), (" [60s]", 0)])
                        _emit_segs([("noise ", 0), (n_val_pad, 0),
                                    (pads[2] + " | ", 0),
                                    (noise_g, 0), (" [60s]", 0)])
                        _emit("")
                        _emit(format_extra_line(ch_s, phy_s, tx_s))
                        _emit(format_radio_line(
                            state.sig.mcs, state.sig.band,
                            state.sig.security))
                        _emit("")
                        _emit_segs([("traffic down ", 0), (d_val_pad, 0),
                                    (pads[3] + " | ", 0),
                                    (down_g, 0), (" [60s]", 0)])
                        _emit_segs([("traffic up   ", 0), (u_val_pad, 0),
                                    (pads[4] + " | ", 0),
                                    (up_g, 0), (" [60s]", 0)])
                    else:
                        _emit_segs([("RSSI  ", 0),
                                    (r_val_pad + "[%s]" % r_rate, r_attr),
                                    (pads[0], 0)])
                        _emit_segs([("SNR   ", 0),
                                    (s_val_pad + "[%s]" % s_rate, s_attr),
                                    (pads[1], 0)])
                        _emit_segs([("noise ", 0), (n_val_pad, 0),
                                    (pads[2], 0)])
                        _emit("")
                        _emit(format_extra_line(ch_s, phy_s, tx_s))
                        _emit(format_radio_line(
                            state.sig.mcs, state.sig.band,
                            state.sig.security))
                        _emit("")
                        _emit_segs([("traffic down ", 0), (d_val_pad, 0),
                                    (pads[3], 0)])
                        _emit_segs([("traffic up   ", 0), (u_val_pad, 0),
                                    (pads[4], 0)])
                _emit("")
                _emit("Net: %s" % (state.net_ssid or "unknown"))
                _addr = format_addr_line(state.ip, state.router, state.mac)
                if _addr:
                    _emit(_addr)
                toast, pending = state.ui_snapshot()
                _emit("")
                _emit("loc: %s  pending: %d" % (
                    _location_label(conn, state.active_spot_id), pending))
                for compare_line in comparison_lines(state):
                    _emit(compare_line)
                with state._lock:
                    _bench = state.benchmark
                    _last = state.last_result
                if _bench is not None:
                    _emit("bench: rssi %s snr %s down %s up %s" % (
                        "-" if _bench.get("rssi") is None
                        else _bench.get("rssi"),
                        "-" if _bench.get("snr") is None
                        else _bench.get("snr"),
                        fmt_mbps(_bench.get("down_mbps")),
                        fmt_mbps(_bench.get("up_mbps"))))
                if _last:
                    _emit("last: %s" % _last)
                _emit("")
                _emit("keys: s snapshot | t throughput | c compare | "
                      "b benchmark | l switch | n new | f floor | q quit")
                if toast:
                    _emit("» %s" % toast)
                stdscr.refresh()
            except Exception:
                pass
            ch = stdscr.getch()
            if ch == KEY_NO_INPUT:
                continue
            if ch == KEY_CTRL_C:  # Ctrl-C via getch if delivered
                return 0
            try:
                key = chr(ch)
            except (ValueError, OverflowError):
                continue
            if key in (KEY_QUIT, KEY_QUIT_UPPER):
                return 0
            elif key == KEY_SNAPSHOT:
                if state.no_wifi:
                    state.try_snapshot()  # toasts NO-WIFI blocked hint
                    continue
                # Always via drilldown prefilled with active (spec §1):
                # single Enter confirms instantly for fast walkthroughs.
                if not _pick_room_spot_curses(
                        stdscr, conn, state, poll_timeout_ms):
                    state.set_toast("snapshot cancelled (no spot)")
                    continue
                state.try_snapshot()
            elif key == KEY_SPEED_PROBE:
                state.try_speed_probe()
            elif key == KEY_COMPARE:
                try:
                    target = _baseline_picker_curses(
                        stdscr, conn, state, poll_timeout_ms)
                except (sqlite3.Error, OSError, ValueError) as exc:
                    state.set_toast("comparison unavailable: %s" % exc)
                    continue
                if target is None:
                    continue
                if target == 0:
                    state.clear_baseline_walk()
                    state.set_toast("walk comparison off")
                else:
                    try:
                        state.select_baseline_walk(conn, target)
                    except (sqlite3.Error, OSError, ValueError) as exc:
                        state.set_toast("comparison unavailable: %s" % exc)
                    else:
                        state.set_toast("comparing with walk #%s" % (
                            state.baseline_walk_id,))
            elif key == KEY_BENCHMARK:
                if state.no_wifi:
                    state.set_toast(
                        "NO-WIFI: `b` blocked (WiFi off/not associated)")
                    continue
                if state.active_location_id is None:
                    state.set_toast(
                        "no preset location: rerun with --location")
                    continue
                try:
                    existing = store_mod.get_benchmark(
                        conn, state.active_location_id)
                except (sqlite3.Error, OSError, ValueError):
                    existing = None
                if existing is not None:
                    ans = _prompt_curses(
                        stdscr, "Overwrite benchmark? [y/N] ",
                        poll_timeout_ms).strip().lower()
                    if ans not in ("y", "yes"):
                        state.set_toast("benchmark kept")
                        continue
                note = _prompt_curses(
                    stdscr, "benchmark note: ",
                    poll_timeout_ms).strip()
                state.set_toast("benchmark running (speedtest)...")
                try:
                    _h, _w = stdscr.getmaxyx()
                    stdscr.addstr(
                        _h - 1, 0,
                        "» benchmark running (speedtest)..."[:_w - 1])
                    stdscr.refresh()
                except Exception:
                    pass
                res = _finish_benchmark(
                    state.db_path, state.active_location_id, note=note,
                    ssid_override=state.ssid_override,
                    ssid_id=state.ssid_id,
                    no_speedtest=state.no_speedtest)
                state.set_toast(res.message)
                if res.ok:
                    state.refresh_benchmark(conn)
            elif key == KEY_SWITCH:
                if _pick_room_spot_curses(
                        stdscr, conn, state, poll_timeout_ms):
                    state.set_toast("active: %s" % _location_label(
                        conn, state.active_spot_id))
            elif key == KEY_NEW:
                if state.active_location_id is None:
                    state.set_toast(
                        "no preset location: rerun with --location")
                    continue
                room_id, note = _create_room_curses(
                    stdscr, conn, state.active_location_id,
                    poll_timeout_ms)
                state.set_toast(note)
                if room_id is None:
                    continue
                spot_id = _create_spot_curses(
                    stdscr, conn, room_id, poll_timeout_ms)
                if spot_id is not None:
                    state.set_active_spot(spot_id)
                    state.set_toast("active: %s" % _location_label(
                        conn, state.active_spot_id))
                else:
                    state.set_toast("new spot cancelled/invalid")
            elif key == KEY_FLOOR:
                if state.active_spot_id is None:
                    state.set_toast("no active spot")
                else:
                    raw = _prompt_curses(
                        stdscr, "floor (int): ", poll_timeout_ms)
                    try:
                        floor = parse_floor_input(raw)
                    except ValueError as exc:
                        state.set_toast(str(exc))
                    else:
                        state.set_toast(state.set_floor(conn, floor))
            # loop; timeout already paces polling
    except KeyboardInterrupt:
        return 0
    finally:
        _finish_walk_session(conn, state)
        conn.close()


# ---------------------------------------------------------------------------
# Non-curses fallback (plain ANSI; blocking-input limit documented)
# ---------------------------------------------------------------------------

def _baseline_picker_fallback(
    conn: sqlite3.Connection,
    state: WalkState,
) -> Optional[int]:
    """Line-based prior-walk picker; None means cancel."""
    rows = _baseline_walk_rows(conn, state)
    active = (state.baseline_walk_id
              if state.baseline_walk_id is not None else 0)
    for index, (walk_id, label) in enumerate(rows):
        marker = ">" if walk_id == active else " "
        print("%s%d. %s" % (marker, index + 1, label))
    try:
        raw = input("compare walk [number, Enter=active, q cancel]: ").strip()
    except (EOFError, OSError):
        return None
    if not raw:
        return active
    if raw.lower() in ("q", "quit"):
        return None
    try:
        index = int(raw) - 1
    except ValueError:
        return None
    if 0 <= index < len(rows):
        return rows[index][0]
    return None

def _walk_fallback(db_path: str, interval: float,
                   location_preset: Optional[str],
                   no_speedtest: bool,
                   ssid: Optional[str] = None,
                   walk_name: Optional[str] = None,
                   compare_to: Optional[Union[int, str]] = None,
                   ssid_id: Optional[int] = None,
                   network_bssid: Optional[str] = None) -> int:
    """ANSI fallback when curses/tty unavailable.

    Limit: keys are line-buffered (type a key + Enter); no live refresh
    while waiting for input. Same ``s``/``b``/``l``/``n``/``f``/``q`` keys.
    """
    import select

    try:
        conn = store_mod.get_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return 3
    cap = history_cap(interval)
    state = WalkState(db_path, no_speedtest=no_speedtest,
                      ssid_id=ssid_id, ssid_name=ssid, history_max=cap)
    state.net_bssid = network_bssid
    try:
        state.active_location_id = _resolve_preset(conn, location_preset)
        if state.active_location_id is not None:
            try:
                _start_walk_session(
                    conn, state, walk_name=walk_name, compare_to=compare_to)
            except (sqlite3.Error, OSError, ValueError) as exc:
                state.set_toast("walk comparison unavailable: %s" % exc)
        state.refresh_benchmark(conn)
        state.ensure_identity()
        state.ensure_addrs()
        print("walk fallback (no curses): type a key + Enter", flush=True)
        print("keys: s snapshot | t throughput | c compare | b benchmark | "
              "l switch | n new | f floor | q quit", flush=True)
        while True:
            state.poll()
            if state.no_wifi:
                print("NO-WIFI: %s (`s` blocked)" % state.no_wifi_msg)
                rssi_s = "UNKNOWN" if state.sig.rssi is None else "%4d dBm" % state.sig.rssi
                snr_s = "UNKNOWN" if state.sig.snr is None else "%3d dB" % state.sig.snr
                noise_s = "UNKNOWN" if state.sig.noise is None else "%4d dBm" % state.sig.noise
                r_rating = rate_rssi(state.sig.rssi)
                s_rating = rate_snr(state.sig.snr)
                _, r_code = rating_style(r_rating or "UNKNOWN")
                _, s_code = rating_style(s_rating or "UNKNOWN")
                lefts = [
                    format_meter_left("RSSI", rssi_s, r_rating),
                    format_meter_left("SNR", snr_s, s_rating),
                    format_meter_left("noise", noise_s, None),
                ]
                max_left = max((len(s) for s in lefts), default=0)
                print(("RSSI  " + ansi_wrap(
                    rssi_s.ljust(METER_VAL_W) + "[%s]" % r_rating,
                    r_code) + " " * (max_left - len(lefts[0]))), flush=True)
                print(("SNR   " + ansi_wrap(
                    snr_s.ljust(METER_VAL_W) + "[%s]" % s_rating,
                    s_code) + " " * (max_left - len(lefts[1]))), flush=True)
                print(("noise " + noise_s.ljust(METER_VAL_W)
                       + " " * (max_left - len(lefts[2]))), flush=True)
                print(format_extra_line(
                    state.sig.channel or "-", state.sig.phy or "-",
                    state.sig.tx_rate or "-"), flush=True)
                print(format_radio_line(
                    state.sig.mcs, state.sig.band,
                    state.sig.security), flush=True)
            else:
                rssi_s = "UNKNOWN" if state.sig.rssi is None else "%4d dBm" % state.sig.rssi
                snr_s = "UNKNOWN" if state.sig.snr is None else "%3d dB" % state.sig.snr
                noise_s = "UNKNOWN" if state.sig.noise is None else "%4d dBm" % state.sig.noise
                r_rating = rate_rssi(state.sig.rssi)
                s_rating = rate_snr(state.sig.snr)
                _, r_code = rating_style(r_rating or "UNKNOWN")
                _, s_code = rating_style(s_rating or "UNKNOWN")
                ch_s = state.sig.channel or "-"
                phy_s = state.sig.phy or "-"
                tx_s = state.sig.tx_rate or "-"
                lefts = [
                    format_meter_left("RSSI", rssi_s, r_rating),
                    format_meter_left("SNR", snr_s, s_rating),
                    format_meter_left("noise", noise_s, None),
                ]
                tw = 80
                try:
                    tw = shutil.get_terminal_size((80, 24)).columns
                except Exception:
                    pass
                max_left, gw = meter_layout(tw, lefts)
                rssi_g = state.hist_rssi.sparkline(-90, -30, gw, align="right")
                snr_g = state.hist_snr.sparkline(0, 40, gw, align="right")
                noise_g = state.hist_noise.sparkline(-100, -60, gw, align="right")
                down_g = state.hist_down.sparkline_auto(gw)
                up_g = state.hist_up.sparkline_auto(gw)
                down_s = fmt_rate_val(
                    state.last_rates[0] if state.last_rates else None)
                up_s = fmt_rate_val(
                    state.last_rates[1] if state.last_rates else None)
                d_val_pad = down_s.ljust(METER_VAL_W)
                u_val_pad = up_s.ljust(METER_VAL_W)
                pad1 = " " * (max_left - len(lefts[0]))
                pad2 = " " * (max_left - len(lefts[1]))
                pad3 = " " * (max_left - len(lefts[2]))
                print("RSSI  " + ansi_wrap(
                    rssi_s.ljust(METER_VAL_W) + "[%s]" % r_rating, r_code)
                    + pad1 + " | " + ansi_wrap(rssi_g, r_code)
                    + " [60s]", flush=True)
                print("SNR   " + ansi_wrap(
                    snr_s.ljust(METER_VAL_W) + "[%s]" % s_rating, s_code)
                    + pad2 + " | " + ansi_wrap(snr_g, s_code)
                    + " [60s]", flush=True)
                print("noise " + noise_s.ljust(METER_VAL_W)
                      + pad3 + " | " + noise_g
                      + " [60s]", flush=True)
                print(format_extra_line(ch_s, phy_s, tx_s), flush=True)
                print(format_radio_line(
                    state.sig.mcs, state.sig.band,
                    state.sig.security), flush=True)
                print("", flush=True)
                print("traffic down " + d_val_pad + " | " + down_g
                      + " [60s]", flush=True)
                print("traffic up   " + u_val_pad + " | " + up_g
                      + " [60s]", flush=True)
            print("Net: %s" % (state.net_ssid or "unknown"), flush=True)
            _addr = format_addr_line(state.ip, state.router, state.mac)
            if _addr:
                print(_addr, flush=True)
            toast, pending = state.ui_snapshot()
            print("loc: %s pending: %d %s" % (
                _location_label(conn, state.active_spot_id), pending,
                ("» %s" % toast) if toast else ""))
            for compare_line in comparison_lines(state):
                print(compare_line, flush=True)
            with state._lock:
                _bench = state.benchmark
                _last = state.last_result
            if _bench is not None:
                print("bench: rssi %s snr %s down %s up %s" % (
                    "-" if _bench.get("rssi") is None
                    else _bench.get("rssi"),
                    "-" if _bench.get("snr") is None
                    else _bench.get("snr"),
                    fmt_mbps(_bench.get("down_mbps")),
                    fmt_mbps(_bench.get("up_mbps"))), flush=True)
            if _last:
                print("last: %s" % _last, flush=True)
            state.set_toast("")
            r, _, _ = select.select([sys.stdin], [], [], interval)
            key = ""
            if r:
                try:
                    key = sys.stdin.readline().strip().lower()
                except (OSError, ValueError):
                    key = ""
            else:
                continue
            if key in QUIT_WORDS:
                return 0
            elif key == KEY_SNAPSHOT:
                if state.no_wifi:
                    state.try_snapshot()  # toasts NO-WIFI blocked hint
                    time.sleep(FALLBACK_SETTLE_SLEEP)
                    continue
                if not _fallback_pick(conn, state):
                    continue  # picker toasted already ("cancelled", ...)
                state.try_snapshot()
                time.sleep(FALLBACK_SETTLE_SLEEP)  # fast mocks → toast order
            elif key == KEY_SPEED_PROBE:
                state.try_speed_probe()
                time.sleep(FALLBACK_SETTLE_SLEEP)
            elif key == KEY_COMPARE:
                try:
                    target = _baseline_picker_fallback(conn, state)
                except (sqlite3.Error, OSError, ValueError) as exc:
                    state.set_toast("comparison unavailable: %s" % exc)
                    continue
                if target is None:
                    continue
                if target == 0:
                    state.clear_baseline_walk()
                    state.set_toast("walk comparison off")
                else:
                    try:
                        state.select_baseline_walk(conn, target)
                    except (sqlite3.Error, OSError, ValueError) as exc:
                        state.set_toast("comparison unavailable: %s" % exc)
                    else:
                        state.set_toast("comparing with walk #%s" % (
                            state.baseline_walk_id,))
            elif key == KEY_BENCHMARK:
                if state.no_wifi:
                    state.set_toast(
                        "NO-WIFI: `b` blocked (WiFi off/not associated)")
                    time.sleep(FALLBACK_SETTLE_SLEEP)
                    continue
                if state.active_location_id is None:
                    state.set_toast(
                        "no preset location: rerun with --location")
                    continue
                try:
                    existing = store_mod.get_benchmark(
                        conn, state.active_location_id)
                except (sqlite3.Error, OSError, ValueError):
                    existing = None
                if existing is not None:
                    try:
                        ans = input(
                            "Overwrite benchmark? [y/N] ").strip().lower()
                    except (EOFError, OSError):
                        ans = ""
                    if ans not in ("y", "yes"):
                        state.set_toast("benchmark kept")
                        continue
                try:
                    note = input("benchmark note: ").strip()
                except (EOFError, OSError):
                    note = ""
                state.set_toast("benchmark running (speedtest)...")
                print("» benchmark running (speedtest)...", flush=True)
                res = _finish_benchmark(
                    state.db_path, state.active_location_id, note=note,
                    ssid_override=state.ssid_override,
                    ssid_id=state.ssid_id,
                    no_speedtest=state.no_speedtest)
                state.set_toast(res.message)
                if res.ok:
                    state.refresh_benchmark(conn)
            elif key == KEY_SWITCH:
                _fallback_pick(conn, state)
            elif key == KEY_NEW:
                _fallback_create(conn, state)
            elif key == KEY_FLOOR:
                _fallback_floor(conn, state)
    except KeyboardInterrupt:
        return 0
    finally:
        _finish_walk_session(conn, state)
        conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _select_walk_location(
    conn: sqlite3.Connection,
    requested: Optional[str],
    input_fn: Optional[Callable[[str], str]] = None,
) -> int:
    """Resolve a preset or select/create a location before the walk UI."""
    if requested is not None:
        return store_mod.resolve_location(conn, requested)
    if not sys.stdin.isatty():
        raise ValueError(
            "location selection requires a terminal; pass --location NAME")
    read = input_fn or input
    while True:
        locations = store_mod.list_locations(conn)
        print("Select location:")
        for index, location in enumerate(locations, start=1):
            print("%d. %s" % (index, location.name))
        print("%d. Create location" % (len(locations) + 1))
        try:
            raw = read("Location [number, q cancel]: ").strip()
        except (EOFError, OSError):
            raise ssid_mod.SSIDSelectionCancelled(
                "location selection cancelled")
        if raw.lower() in QUIT_WORDS:
            raise ssid_mod.SSIDSelectionCancelled(
                "location selection cancelled")
        try:
            index = int(raw) - 1
        except ValueError:
            continue
        if 0 <= index < len(locations):
            return locations[index].id
        if index != len(locations):
            continue
        try:
            name = read("Location name: ").strip()
        except (EOFError, OSError):
            raise ssid_mod.SSIDSelectionCancelled(
                "location selection cancelled")
        if name:
            return store_mod.resolve_location(conn, name)


def _prepare_walk_selection(
    db_path: str,
    location_preset: Optional[str],
    requested_ssid: Optional[str],
) -> Tuple[str, store_mod.SSID, Optional[str]]:
    """Resolve the location and its SSID before curses takes the terminal."""
    conn = store_mod.get_db(db_path)
    try:
        location_id = _select_walk_location(conn, location_preset)
        identity = None
        if requested_ssid is None:
            try:
                identity = signal_mod.read_network_identity()
            except Exception:  # noqa: BLE001 - detection is best-effort
                identity = None
        selected = ssid_mod.select_ssid_line(
            conn,
            location_id,
            detected_name=identity[0] if identity is not None else None,
            requested=requested_ssid,
        )
        return (
            str(location_id),
            selected,
            identity[1] if identity is not None else None,
        )
    finally:
        conn.close()


def run_walk(db_path: str, interval: float = 1.0,
             location_preset: Optional[str] = None,
             no_speedtest: bool = False,
             ssid: Optional[str] = None,
             walk_name: Optional[str] = None,
             compare_to: Optional[Union[int, str]] = None) -> int:
    """Run walk loop; curses when tty available else ANSI fallback."""
    if interval <= 0:
        print("Error: --interval must be > 0", file=sys.stderr)
        return 3
    if ssid is not None and not ssid.strip():
        print("Error: --ssid must not be blank", file=sys.stderr)
        return 3
    try:
        location_preset, selected_ssid, network_bssid = (
            _prepare_walk_selection(db_path, location_preset, ssid))
    except ssid_mod.SSIDSelectionCancelled as exc:
        print(str(exc), file=sys.stderr)
        return 0
    except (sqlite3.Error, OSError, ValueError) as exc:
        print("Error: cannot prepare walk: %s" % exc, file=sys.stderr)
        return 3
    ssid = selected_ssid.name
    try:
        import curses  # noqa: F401
        use_curses = sys.stdin.isatty() and sys.stdout.isatty()
    except ImportError:
        use_curses = False
    if use_curses:
        import curses as _c

        def _main(stdscr: object) -> int:
            return _walk_curses(
                stdscr, db_path, interval, location_preset, no_speedtest,
                ssid, walk_name, compare_to,
                ssid_id=selected_ssid.id, network_bssid=network_bssid)

        try:
            return _c.wrapper(_main)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # curses init failed → fallback
            print("curses unavailable (%s); using fallback" % (exc,),
                  file=sys.stderr)
    return _walk_fallback(
        db_path, interval, location_preset, no_speedtest, ssid,
        walk_name, compare_to,
        ssid_id=selected_ssid.id, network_bssid=network_bssid)


__all__ = [
    "WalkState",
    "SnapshotResult",
    "SpotBaseline",
    "MetricComparison",
    "SpeedProbeResult",
    "PickerSelection",
    "QUIT_WORDS",
    "KEY_SNAPSHOT",
    "KEY_BENCHMARK",
    "KEY_SWITCH",
    "KEY_NEW",
    "KEY_FLOOR",
    "KEY_COMPARE",
    "KEY_SPEED_PROBE",
    "KEY_QUIT",
    "KEY_CREATE",
    "attempt_read",
    "ansi_wrap",
    "comparison_lines",
    "finish_snapshot",
    "fmt_mbps",
    "fmt_rate_val",
    "format_extra_line",
    "format_radio_line",
    "format_addr_line",
    "format_meter_left",
    "format_meter_row",
    "format_net_line",
    "format_signal_line",
    "history_cap",
    "graph_width",
    "wide_graph_width",
    "grouped_graph_width",
    "layout_mode",
    "meter_layout",
    "parse_floor_input",
    "picker_move",
    "picker_press",
    "picker_start_cursor",
    "rate_rssi",
    "rate_snr",
    "rating_style",
    "snapshot_payload",
    "SparkHistory",
    "start_snapshot_thread",
    "METER_LABEL_W",
    "METER_VAL_W",
    "WIDE_MIN_WIDTH",
    "run_walk",
]
