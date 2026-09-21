"""Mutable state and worker coordination for a walk session."""
from __future__ import annotations

import sqlite3
import threading
import time
from collections import deque
from typing import Callable, Dict, List, Optional, Tuple, Union

from wifimap import signal as signal_mod
from wifimap import speed as speed_mod
from wifimap import store as store_mod
from wifimap import traffic as traffic_mod
from wifimap.path_monitor import (
    PathMonitor,
    PathSample,
    PathSnapshot,
    PathTracker,
)
from wifimap.walk_baseline import load_baseline, median_values
from wifimap.walk_snapshot import (
    MetricComparison,
    SnapshotResult,
    SpeedProbeResult,
    SpotBaseline,
    _finish_benchmark,
    _sample_guarded,
    start_snapshot_thread,
)
from wifimap.walk_ui import SparkHistory, attempt_read, fmt_mbps


WORKER_DRAIN_TIMEOUT = 130.0


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
        self._path_tracker = PathTracker(window_size=60, interval=1.0)
        self._path_monitor: Optional[PathMonitor] = None
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
            self._path_tracker.reset()
            self._speed_probe = None
            if self.pending:
                self.toast = ""
            self.last_result = ""

    def record_path_sample(self, sample: PathSample) -> None:
        with self._lock:
            self._path_tracker.record(sample)

    def path_snapshot(self) -> PathSnapshot:
        with self._lock:
            return self._path_tracker.snapshot()

    def start_path_monitor(self) -> None:
        """Start continuous gateway and Internet probes for this walk."""
        self.stop_path_monitor()
        monitor = PathMonitor(
            gateway=self.router, on_sample=self.record_path_sample)
        self._path_monitor = monitor
        monitor.start()

    def stop_path_monitor(self) -> None:
        monitor = self._path_monitor
        self._path_monitor = None
        if monitor is not None:
            monitor.stop()

    @staticmethod
    def _median(values) -> Optional[float]:
        return median_values(values)

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

        selected = load_baseline(
            conn, location_id, self.current_walk_id, id_or_name)
        with self._lock:
            self.baseline_walk_id = selected.walk_id
            self.baseline_walk_name = selected.walk_name
            self._baseline_by_spot = selected.spots
            self._comparison_generation += 1
            self._speed_probe = None
        return selected.walk_id

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

    def _start_snapshot_thread(self, *args, **kwargs):
        return start_snapshot_thread(*args, **kwargs)

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
        t = self._start_snapshot_thread(
            self.db_path, loc_id,
            no_speedtest=self.no_speedtest,
            ssid_override=self.ssid_override,
            ssid_id=self.ssid_id,
            on_done=_cb,
            run_speedtest_fn=run_speedtest_fn,
            walk_id=self.current_walk_id,
            path_snapshot=self.path_snapshot(),
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
