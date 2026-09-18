"""Walk TUI: live signal poll + snapshot/location keys (stdlib only).

Thread safety (M1 concern): ``store.list_readings`` toggles
``conn.row_factory`` on the passed connection, so connections are NOT
thread-safe to share. The snapshot background thread MUST open its own
``store.get_db(db_path)`` connection and close it; it never touches the
UI thread's connection. ``WalkState`` toast/pending mutations from worker
callbacks and UI-thread reads are serialized by ``WalkState._lock``.
"""
from __future__ import annotations

import copy
import math
import sqlite3
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Dict, List, Literal, Optional, Tuple, Union

from wifimap import signal as signal_mod
from wifimap import speed as speed_mod
from wifimap import store as store_mod


# ---------------------------------------------------------------------------
# Module constants: walk keys, key codes, timeouts
# ---------------------------------------------------------------------------

KEY_SNAPSHOT = "s"
KEY_SWITCH = "l"
KEY_NEW = "n"
KEY_FLOOR = "f"
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


def _poll_timeout_ms(interval: float) -> int:
    """Poll getch timeout for walk loop; floor keeps fast intervals usable."""
    return max(POLL_TIMEOUT_MIN_MS, int(interval * 1000))

#: Picker outcome: a location id, the ``"new"`` create row, or None (cancel).
PickerSelection = Union[int, Literal["new"]]


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def format_signal_line(sig: signal_mod.Signal) -> str:
    """One-line live readout; None fields render as UNKNOWN/-."""

    def _v(v: object) -> str:
        return "-" if v is None else str(v)

    rssi = "UNKNOWN" if sig.rssi is None else "%d dBm" % sig.rssi
    noise = "UNKNOWN" if sig.noise is None else "%d dBm" % sig.noise
    snr = "UNKNOWN" if sig.snr is None else "%d dB" % sig.snr
    return "rssi=%s noise=%s snr=%s ssid=%s bssid=%s ch=%s phy=%s tx=%s" % (
        rssi, noise, snr, _v(sig.ssid), _v(sig.bssid),
        _v(sig.channel), _v(sig.phy), _v(sig.tx_rate),
    )


def format_net_line(ssid: Optional[str]) -> str:
    """Session header: ``Net: <ssid>`` or ``Net: unknown``."""
    return "Net: %s" % (ssid if ssid else "unknown")


def rate_rssi(v: Optional[int]) -> str:
    """Rate RSSI: >=-60 GREAT, >=-70 OK, else WEAK, None UNKNOWN."""
    if v is None:
        return "UNKNOWN"
    if v >= -60:
        return "GREAT"
    if v >= -70:
        return "OK"
    return "WEAK"


def rate_snr(v: Optional[int]) -> str:
    """Rate SNR: >=25 GREAT, >=15 OK, else WEAK, None UNKNOWN."""
    if v is None:
        return "UNKNOWN"
    if v >= 25:
        return "GREAT"
    if v >= 15:
        return "OK"
    return "WEAK"


_SPARK_CHARS = "▁▂▃▄▅▆▇█"


class SparkHistory:
    """Fixed-length sample ring; renders ASCII block sparkline."""

    def __init__(self, maxlen: int = 60) -> None:
        self._buf: deque = deque(maxlen=max(1, maxlen))

    def append(self, v: Optional[float]) -> None:
        self._buf.append(v)

    def sparkline(self, lo: float, hi: float, width: int) -> str:
        vals = list(self._buf)[-width:] if width > 0 else []
        if not vals:
            return ""
        span = hi - lo
        out = []
        for v in vals:
            if v is None:
                out.append(" ")
                continue
            if span <= 0:
                lvl = 7
            else:
                frac = (v - lo) / span
                frac = 0.0 if frac < 0.0 else (1.0 if frac > 1.0 else frac)
                lvl = int(round(frac * 7))
            out.append(_SPARK_CHARS[lvl])
        return "".join(out)


def history_cap(interval: float) -> int:
    """Samples covering ~60s at the poll interval; at least 1."""
    return max(1, int(math.ceil(60.0 / interval)))


def picker_start_cursor(loc_ids: List[int],
                        active: Optional[int]) -> int:
    """Cursor position with the active location preselected (spec §1).

    Positions ``0..count-1`` are locations, ``count`` is the create row.
    Returns the active index, or 0 when active is unset/missing/empty.
    """
    if active is not None:
        try:
            return loc_ids.index(active)
        except ValueError:
            pass
    return 0


def picker_move(cursor: int, direction: int, count: int) -> int:
    """Move cursor ±1 with wraparound over locations + create row."""
    total = count + 1  # create row always exists, even with zero locations
    return (cursor + direction) % total


def picker_press(keypress: str, cursor: int,
                 count: int) -> Tuple[int, str, Optional[int]]:
    """Pure picker key model; cursor roams ``0..count`` (``count`` = create).

    Returns ``(new_cursor, action, index)`` with action one of:
    - ``"confirm"``: Enter (``""``/``"\\n"``/``"\\r"``/``"enter"``) confirms
      the cursor, digits ``1``-``9`` jump to and confirm that row,
      ``"+"`` confirms the create row.
      ``index == count`` means the create row.
    - ``"move"``: arrows (``"up"``/``"down"``) reposition the cursor;
      keep waiting.
    - ``"cancel"``: ``q``/Escape; ``"ignore"``: anything else.
    """
    if keypress in ("", "\n", "\r", "enter"):
        return (cursor, "confirm", cursor)
    if keypress in ("q", "Q", "\x1b", "esc"):
        return (cursor, "cancel", None)
    if keypress in ("up", "down"):
        step = -1 if keypress == "up" else 1
        return (picker_move(cursor, step, count), "move", None)
    if keypress == "+":
        return (count, "confirm", count)
    if len(keypress) == 1 and "1" <= keypress <= "9":
        idx = int(keypress) - 1
        if 0 <= idx < count:
            return (idx, "confirm", idx)
        return (cursor, "ignore", None)
    return (cursor, "ignore", None)


def parse_floor_input(s: str) -> int:
    """Parse floor int (negative = basement); raises ValueError on bad input."""
    t = s.strip()
    if not t:
        raise ValueError("floor must be an integer, got empty input")
    try:
        return int(t, 10)
    except ValueError as exc:
        raise ValueError("floor must be an integer, got %r" % (s,)) from exc


def snapshot_payload(sig: signal_mod.Signal) -> Dict[str, object]:
    """Freeze a signal copy into a store.add_reading kwargs dict."""
    return {
        "ssid": sig.ssid,
        "bssid": sig.bssid,
        "rssi": sig.rssi,
        "noise": sig.noise,
        "snr": sig.snr,
        "channel": sig.channel,
        "phy": sig.phy,
        "tx_rate": sig.tx_rate,
    }


def attempt_read(
    read_fn: Callable[[], signal_mod.Signal],
    max_retries: int = 3,
) -> signal_mod.Signal:
    """Poll once with retry; parse-fail retries ``max_retries``x then UNKNOWN.

    ``NoWiFiError`` propagates immediately (caller shows NO-WIFI).
    Any other exception is retried; after exhaustion returns an empty
    ``Signal()`` (UNKNOWN row), never raises.
    """
    last_exc: Optional[Exception] = None
    for _ in range(max_retries + 1):
        try:
            return read_fn()
        except signal_mod.NoWiFiError:
            raise
        except Exception as exc:  # noqa: BLE001 - walk must never crash
            last_exc = exc
    _ = last_exc
    return signal_mod.Signal()


# ---------------------------------------------------------------------------
# Snapshot background worker (thread-safe: own DB connection)
# ---------------------------------------------------------------------------

@dataclass
class SnapshotResult:
    ok: bool
    reading_id: Optional[int] = None
    message: str = ""


def finish_snapshot(
    db_path: str,
    location_id: int,
    sig_dict: Dict[str, object],
    run_speedtest_fn: Optional[Callable[[], speed_mod.Speed]] = None,
    no_speedtest: bool = False,
) -> SnapshotResult:
    """Run speedtest (unless skipped) + insert reading on a fresh connection.

    Always opens/closes its OWN ``store.get_db`` connection — never share
    the UI thread's connection (row_factory toggling is not thread-safe).
    Speedtest fail → NULLs + server="ERROR", signal kept (spec 4).
    Missing binary → signal-only row + warning message (same as scan).
    DB error → ok=False + error message (caller toasts, walk continues).
    """
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
                conn, location_id,
                ssid=sig_dict.get("ssid"),  # type: ignore[arg-type]
                bssid=sig_dict.get("bssid"),  # type: ignore[arg-type]
                rssi=sig_dict.get("rssi"),  # type: ignore[arg-type]
                noise=sig_dict.get("noise"),  # type: ignore[arg-type]
                snr=sig_dict.get("snr"),  # type: ignore[arg-type]
                channel=sig_dict.get("channel"),  # type: ignore[arg-type]
                phy=sig_dict.get("phy"),  # type: ignore[arg-type]
                tx_rate=sig_dict.get("tx_rate"),  # type: ignore[arg-type]
                ping_ms=ping_ms, down_mbps=down,  # type: ignore[arg-type]
                up_mbps=up,  # type: ignore[arg-type]
                server=server,
            )
        except (sqlite3.Error, OSError, ValueError) as exc:
            return SnapshotResult(ok=False, message="DB error: %s" % (exc,))
    finally:
        conn.close()
    msg = "saved #%d" % rid
    if notice:
        msg += " (%s)" % notice
    return SnapshotResult(ok=True, reading_id=rid, message=msg)


def start_snapshot_thread(
    db_path: str,
    location_id: int,
    sig: signal_mod.Signal,
    no_speedtest: bool = False,
    on_done: Optional[Callable[[SnapshotResult], None]] = None,
    run_speedtest_fn: Optional[Callable[[], speed_mod.Speed]] = None,
) -> "threading.Thread":
    """Freeze signal copy, spawn daemon thread; on done insert + callback.

    The thread opens its own DB connection via ``finish_snapshot``.
    """
    frozen = snapshot_payload(copy.deepcopy(sig))

    def _work() -> None:
        res = finish_snapshot(
            db_path, location_id, frozen,
            run_speedtest_fn=run_speedtest_fn,
            no_speedtest=no_speedtest,
        )
        if on_done is not None:
            on_done(res)

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Walk state (shared by curses + fallback loops)
# ---------------------------------------------------------------------------

class WalkState:
    """Mutable walk session: active location, last signal, toast, counters.

    ``toast``/``pending`` are shared between the UI thread and snapshot
    worker threads; all mutation and UI reads go through the lock.
    """

    def __init__(self, db_path: str, no_speedtest: bool = False,
                 ssid_override: Optional[str] = None,
                 history_max: int = 60) -> None:
        self.db_path = db_path
        self.no_speedtest = no_speedtest
        self.ssid_override = (ssid_override.strip() or None) if ssid_override is not None else None
        self.history_max = max(1, history_max)
        self.hist_rssi: SparkHistory = SparkHistory(maxlen=self.history_max)
        self.hist_noise: SparkHistory = SparkHistory(maxlen=self.history_max)
        self.hist_snr: SparkHistory = SparkHistory(maxlen=self.history_max)
        self.active_id: Optional[int] = None
        self.sig: signal_mod.Signal = signal_mod.Signal()
        self.no_wifi: bool = False
        self.no_wifi_msg: str = ""
        self.toast: str = ""
        self.pending: int = 0
        self.net_ssid: Optional[str] = None
        self.net_bssid: Optional[str] = None
        self._lock = threading.Lock()

    def ensure_identity(
        self,
        identity_fn: Optional[Callable[[], Optional[Tuple[str, str]]]] = None,
    ) -> Optional[Tuple[str, str]]:
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
            return
        if self.net_ssid is not None and self.sig.ssid is None:
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

    def try_snapshot(
        self,
        run_speedtest_fn: Optional[Callable[[], speed_mod.Speed]] = None,
    ) -> Optional["threading.Thread"]:
        """Spawn snapshot thread for active location; None + hint if blocked."""
        if self.no_wifi:
            self.set_toast("NO-WIFI: `s` blocked (WiFi off/not associated)")
            return None
        if self.active_id is None:
            self.set_toast("no active location: press `l` to pick one")
            return None
        with self._lock:
            self.pending += 1
        loc_id = self.active_id
        sig_copy = copy.deepcopy(self.sig)
        if self.ssid_override is not None:
            sig_copy.ssid = self.ssid_override
        result_box: List[SnapshotResult] = []

        def _cb(res: SnapshotResult) -> None:
            result_box.append(res)
            self.on_snapshot_done(res)

        t = start_snapshot_thread(
            self.db_path, loc_id, sig_copy,
            no_speedtest=self.no_speedtest,
            on_done=_cb,
            run_speedtest_fn=run_speedtest_fn,
        )
        return t

    def set_floor(self, conn: sqlite3.Connection, floor: int) -> str:
        """Update active location floor; returns toast message."""
        if self.active_id is None:
            return "no active location"
        try:
            store_mod.update_location_floor(conn, self.active_id, floor)
        except ValueError:
            return "unknown location id: %r" % (self.active_id,)
        except (sqlite3.Error, OSError) as exc:
            return "DB error: %s" % (exc,)
        return "floor set to %d" % floor


def _resolve_preset(conn: sqlite3.Connection, preset: Optional[str]) -> Optional[int]:
    if preset is None:
        return None
    try:
        return store_mod.resolve_location(conn, preset)
    except (sqlite3.Error, OSError, ValueError):
        return None


def _location_label(conn: sqlite3.Connection, loc_id: Optional[int]) -> str:
    if loc_id is None:
        return "(none)"
    try:
        loc = store_mod.get_location(conn, loc_id)
    except (sqlite3.Error, OSError, ValueError):
        return "#%s (db error)" % loc_id
    if loc is None:
        return "#%s (deleted?)" % loc_id
    return "#%s %s (floor %s)" % (loc.id, loc.name, loc.floor)


# ---------------------------------------------------------------------------
# Curses loop
# ---------------------------------------------------------------------------

def _picker_curses(stdscr: object, conn: sqlite3.Connection,
                   active: Optional[int],
                   poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS,
                   ) -> Optional[PickerSelection]:
    """Numbered-list picker prefilled with active; returns id/``"new"``/None.

    The cursor starts on the active row (spec §1 prefill), so a single
    Enter confirms instantly for fast walkthroughs. Arrows roam,
    digits jump+confirm, ``+`` confirms the create row, ``q`` cancels.
    The ``"new"`` create row means the caller prompts for
    name/floor/outdoors. Cancel → None.
    """
    import curses

    locs = store_mod.list_locations(conn)
    ids = [loc.id for loc in locs]
    cursor = picker_start_cursor(ids, active)
    stdscr.timeout(-1)  # type: ignore[attr-defined]  # blocking for picker
    try:
        while True:
            h, w = stdscr.getmaxyx()  # type: ignore[attr-defined]
            lines = ["Pick location (Enter=active, 1-%d, + new, q cancel):"
                     % len(locs)]
            for i, loc in enumerate(locs):
                cur = ">" if i == cursor else " "
                mark = "*" if loc.id == active else " "
                lines.append("%s%s%d. %s (floor %d%s)" % (
                    cur, mark, i + 1, loc.name, loc.floor,
                    ", outdoors" if loc.outdoors else ""))
            lines.append("%s  +. <new location>" % (
                ">" if cursor == len(locs) else " ",))
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
            cursor, action, idx = picker_press(key, cursor, len(locs))
            if action == "cancel":
                return None
            if action == "confirm" and idx is not None:
                if idx == len(locs):
                    return "new"
                return locs[idx].id
            # "move"/"ignore" → re-render with updated cursor
    finally:
        stdscr.timeout(poll_timeout_ms)  # type: ignore[attr-defined]


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


def _create_location_curses(
    stdscr: object, conn: sqlite3.Connection,
    poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS,
) -> Optional[int]:
    name = _prompt_curses(stdscr, "name: ", poll_timeout_ms).strip()
    if not name:
        return None
    floor_s = _prompt_curses(stdscr, "floor (int): ", poll_timeout_ms).strip()
    try:
        floor = parse_floor_input(floor_s or "0")
    except ValueError:
        return None
    od_s = _prompt_curses(stdscr, "outdoors? [y/N]: ", poll_timeout_ms).strip().lower()
    outdoors = od_s in ("y", "yes", "1")
    try:
        return store_mod.create_location(
            conn, name, floor=floor, outdoors=outdoors)
    except (sqlite3.Error, OSError, ValueError):
        return None


def _walk_curses(stdscr: object, db_path: str, interval: float,
                 location_preset: Optional[str],
                 no_speedtest: bool,
                 ssid: Optional[str] = None) -> int:
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
                      ssid_override=ssid, history_max=cap)
    try:
        state.active_id = _resolve_preset(conn, location_preset)
        state.ensure_identity()
        if location_preset is not None and state.active_id is None:
            state.set_toast(
                "unknown preset %r; press `l` to pick" % (location_preset,))
        stdscr.nodelay(False)
        poll_timeout_ms = _poll_timeout_ms(interval)
        stdscr.timeout(poll_timeout_ms)
        try:
            curses.curs_set(0)
        except Exception:
            pass
        while True:
            state.poll()
            try:
                locs = store_mod.list_locations(conn)
            except (sqlite3.Error, OSError) as exc:
                state.set_toast("DB error: %s" % (exc,))
                locs = []
            # render
            try:
                stdscr.clear()
                h, w = stdscr.getmaxyx()
                row = 0

                def _emit(s: str) -> None:
                    nonlocal row
                    if row < h - 1:
                        try:
                            stdscr.addstr(row, 0, s[: w - 1])
                        except Exception:
                            pass
                        row += 1

                if state.no_wifi:
                    _emit("NO-WIFI: %s" % (state.no_wifi_msg,))
                    _emit("`s` blocked; fix WiFi or quit with `q`.")
                else:
                    rssi_s = "UNKNOWN" if state.sig.rssi is None else "%d dBm" % state.sig.rssi
                    snr_s = "UNKNOWN" if state.sig.snr is None else "%d dB" % state.sig.snr
                    noise_s = "UNKNOWN" if state.sig.noise is None else "%d dBm" % state.sig.noise
                    _emit("RSSI %s [%s]" % (rssi_s, rate_rssi(state.sig.rssi)))
                    _emit("SNR %s [%s]  noise %s  ch %s  phy %s  tx %s" % (
                        snr_s, rate_snr(state.sig.snr), noise_s,
                        state.sig.channel or "-", state.sig.phy or "-",
                        state.sig.tx_rate or "-"))
                manual = " (manual)" if state.ssid_override else ""
                _emit("Net: %s%s" % (state.net_ssid or "unknown", manual))
                toast, pending = state.ui_snapshot()
                _emit("loc: %s  pending: %d" % (
                    _location_label(conn, state.active_id), pending))
                if h >= 10 and not state.no_wifi:
                    gw = max(10, w - 12)
                    _emit("RSSI  %s [60s]" % state.hist_rssi.sparkline(-90, -30, gw))
                    _emit("SNR   %s [60s]" % state.hist_snr.sparkline(0, 40, gw))
                    _emit("noise %s [60s]" % state.hist_noise.sparkline(-100, -60, gw))
                _emit("keys: s snapshot | l switch | n new | "
                      "f floor | q quit")
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
                # Always via picker prefilled with active (spec §1):
                # single Enter confirms instantly for fast walkthroughs.
                picked = _picker_curses(
                    stdscr, conn, state.active_id, poll_timeout_ms)
                if picked == "new":
                    picked = _create_location_curses(
                        stdscr, conn, poll_timeout_ms)
                if isinstance(picked, int):
                    state.active_id = picked
                else:
                    state.set_toast("snapshot cancelled (no location)")
                    continue
                state.try_snapshot()
            elif key == KEY_SWITCH:
                picked = _picker_curses(
                    stdscr, conn, state.active_id, poll_timeout_ms)
                if picked == "new":
                    picked = _create_location_curses(
                        stdscr, conn, poll_timeout_ms)
                if isinstance(picked, int):
                    state.active_id = picked
                    state.set_toast("active: %s" % _location_label(
                        conn, state.active_id))
                elif picked is None:
                    state.set_toast("location switch cancelled")
            elif key == KEY_NEW:
                new_id = _create_location_curses(
                    stdscr, conn, poll_timeout_ms)
                if new_id is not None:
                    state.active_id = new_id
                    state.set_toast("active: %s" % _location_label(
                        conn, state.active_id))
                else:
                    state.set_toast("new location cancelled/invalid")
            elif key == KEY_FLOOR:
                if state.active_id is None:
                    state.set_toast("no active location")
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
        conn.close()


# ---------------------------------------------------------------------------
# Non-curses fallback (plain ANSI; blocking-input limit documented)
# ---------------------------------------------------------------------------

def _walk_fallback(db_path: str, interval: float,
                   location_preset: Optional[str],
                   no_speedtest: bool,
                   ssid: Optional[str] = None) -> int:
    """ANSI fallback when curses/tty unavailable.

    Limit: keys are line-buffered (type a key + Enter); no live refresh
    while waiting for input. Same ``s``/``l``/``n``/``f``/``q`` keys.
    """
    import select

    try:
        conn = store_mod.get_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return 3
    cap = history_cap(interval)
    state = WalkState(db_path, no_speedtest=no_speedtest,
                      ssid_override=ssid, history_max=cap)
    try:
        state.active_id = _resolve_preset(conn, location_preset)
        state.ensure_identity()
        print("walk fallback (no curses): keys s/l/n/f/q + Enter", flush=True)
        while True:
            state.poll()
            if state.no_wifi:
                print("NO-WIFI: %s (`s` blocked)" % state.no_wifi_msg)
            else:
                rssi_s = "UNKNOWN" if state.sig.rssi is None else "%d dBm" % state.sig.rssi
                print("RSSI %s [%s]" % (rssi_s, rate_rssi(state.sig.rssi)), flush=True)
                print("SNR %s [%s]  noise %s" % (
                    "UNKNOWN" if state.sig.snr is None else "%d dB" % state.sig.snr,
                    rate_snr(state.sig.snr),
                    "UNKNOWN" if state.sig.noise is None else "%d dBm" % state.sig.noise),
                    flush=True)
            manual = " (manual)" if state.ssid_override else ""
            print("Net: %s%s" % (state.net_ssid or "unknown", manual), flush=True)
            gw = 40
            print("RSSI  %s [60s]" % state.hist_rssi.sparkline(-90, -30, gw), flush=True)
            toast, pending = state.ui_snapshot()
            print("loc: %s pending: %d %s" % (
                _location_label(conn, state.active_id), pending,
                ("» %s" % toast) if toast else ""))
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
            elif key == KEY_SWITCH:
                _fallback_pick(conn, state)
            elif key == KEY_NEW:
                _fallback_create(conn, state)
            elif key == KEY_FLOOR:
                _fallback_floor(conn, state)
    except KeyboardInterrupt:
        return 0
    finally:
        conn.close()


def _fallback_pick(conn: sqlite3.Connection, state: WalkState) -> bool:
    """Line-based picker prefilled with active; empty input confirms it.

    Returns True when a location is selected, False on cancel.
    """
    try:
        locs = store_mod.list_locations(conn)
    except (sqlite3.Error, OSError) as exc:
        state.set_toast("DB error: %s" % (exc,))
        return False
    ids = [loc.id for loc in locs]
    cursor = picker_start_cursor(ids, state.active_id)
    for i, loc in enumerate(locs):
        cur = ">" if i == cursor else " "
        mark = "*" if loc.id == state.active_id else " "
        print("%s%s%d. %s (floor %d)" % (cur, mark, i + 1, loc.name, loc.floor))
    print("%s  +. <new location>" % (">" if cursor == len(locs) else " ",))
    try:
        raw = input("pick [Enter=active, 1-%d,+]: " % len(locs)).strip()
    except (EOFError, OSError):
        state.set_toast("cancelled")
        return False
    if raw == "":
        if state.active_id is not None and state.active_id in ids:
            return True  # Enter confirms prefilled active instantly
        state.set_toast("cancelled")
        return False
    if raw == KEY_CREATE:
        before = state.active_id
        _fallback_create(conn, state)
        # create sets a fresh id on success, leaves active + error toast on fail
        return state.active_id is not None and state.active_id != before
    # Single digit path: picker_press owns all digit logic.
    _, action, idx = picker_press(raw, cursor, len(locs))
    if action == "cancel":
        state.set_toast("cancelled")
        return False
    if action == "confirm" and idx is not None and idx < len(locs):
        state.active_id = locs[idx].id
        return True
    state.set_toast("cancelled")
    return False


def _fallback_create(conn: sqlite3.Connection, state: WalkState) -> None:
    try:
        name = input("name: ").strip()
        floor = parse_floor_input(input("floor (int): ") or "0")
        outdoors = input("outdoors? [y/N]: ").strip().lower() in (
            "y", "yes", "1")
    except (EOFError, OSError, ValueError) as exc:
        state.set_toast("cancelled/invalid: %s" % (exc,))
        return
    try:
        state.active_id = store_mod.create_location(
            conn, name, floor=floor, outdoors=outdoors)
    except (sqlite3.Error, OSError, ValueError) as exc:
        state.set_toast("DB error: %s" % (exc,))


def _fallback_floor(conn: sqlite3.Connection, state: WalkState) -> None:
    try:
        raw = input("floor (int): ")
        floor = parse_floor_input(raw)
    except (EOFError, OSError, ValueError) as exc:
        state.set_toast(str(exc))
        return
    state.set_toast(state.set_floor(conn, floor))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_walk(db_path: str, interval: float = 1.0,
             location_preset: Optional[str] = None,
             no_speedtest: bool = False,
             ssid: Optional[str] = None) -> int:
    """Run walk loop; curses when tty available else ANSI fallback."""
    if interval <= 0:
        print("Error: --interval must be > 0", file=sys.stderr)
        return 3
    if ssid is not None and not ssid.strip():
        print("Error: --ssid must not be blank", file=sys.stderr)
        return 3
    try:
        import curses  # noqa: F401
        use_curses = sys.stdin.isatty() and sys.stdout.isatty()
    except ImportError:
        use_curses = False
    if use_curses:
        import curses as _c

        def _main(stdscr: object) -> int:
            return _walk_curses(
                stdscr, db_path, interval, location_preset, no_speedtest, ssid)

        try:
            return _c.wrapper(_main)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # curses init failed → fallback
            print("curses unavailable (%s); using fallback" % (exc,),
                  file=sys.stderr)
    return _walk_fallback(db_path, interval, location_preset, no_speedtest, ssid)


__all__ = [
    "WalkState",
    "SnapshotResult",
    "PickerSelection",
    "QUIT_WORDS",
    "KEY_SNAPSHOT",
    "KEY_SWITCH",
    "KEY_NEW",
    "KEY_FLOOR",
    "KEY_QUIT",
    "KEY_CREATE",
    "attempt_read",
    "finish_snapshot",
    "format_net_line",
    "format_signal_line",
    "history_cap",
    "parse_floor_input",
    "picker_move",
    "picker_press",
    "picker_start_cursor",
    "rate_rssi",
    "rate_snr",
    "snapshot_payload",
    "SparkHistory",
    "start_snapshot_thread",
    "run_walk",
]
