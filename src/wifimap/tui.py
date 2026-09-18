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
import os
import shutil
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


_RATING_STYLE = {
    "GREAT": (1, "32"),
    "OK": (2, "33"),
    "WEAK": (3, "31"),
    "UNKNOWN": (0, "37"),
}

WIDE_MIN_WIDTH = 100


def rating_style(rating: str) -> Tuple[int, str]:
    """Map GREAT/OK/WEAK/UNKNOWN to (curses_pair, ansi_code)."""
    return _RATING_STYLE.get(rating, (0, "37"))


def layout_mode(width: int) -> str:
    """Wide side-by-side at >=100 cols, else stacked narrow."""
    return "wide" if width >= WIDE_MIN_WIDTH else "narrow"


def graph_width(total_w: int, label_len: int = 6, suffix_len: int = 6) -> int:
    """Width for narrow stacked graph line so label+bar+suffix fits w-1."""
    return max(10, total_w - label_len - suffix_len - 1)


def wide_graph_width(total_w: int, left_len: int, label_len: int = 6,
                     sep_len: int = 3, suffix_len: int = 0) -> int:
    """Width for wide side-by-side graph so full line fits w-1."""
    return max(10, total_w - 1 - left_len - sep_len - label_len - suffix_len)


def grouped_graph_width(total_w: int, prefixes) -> int:
    """Shared bar width so grouped metric+graph rows right-align.

    ``prefixes`` are the three row prefixes (text before each bar);
    width leaves room for the longest prefix plus ``" [60s]"`` in w-1.
    """
    lens = []
    for p in prefixes:
        try:
            lens.append(len(p) if isinstance(p, str) else int(p))
        except Exception:
            continue
    max_pre = max(lens) if lens else 0
    return max(10, total_w - max_pre - len(" [60s]") - 1)


#: Fixed width of the meter value column (e.g. ``" -63 dBm  "``).
METER_VAL_W = 10

#: Fixed width of the meter label column (``"RSSI  "``/``"SNR   "``/``"noise "``).
METER_LABEL_W = 6


def format_meter_left(label: str, value: str, rating: Optional[str]) -> str:
    """Left side of a meter row: label + value + hugging ``[rating]``.

    No padding inside brackets; alignment padding is applied AFTER ``]``
    by the caller via ``ljust(maxLeft)`` so all ``|`` line up.
    """
    lab = (label if label is not None else "").ljust(METER_LABEL_W)[:METER_LABEL_W]
    val = (value if value is not None else "UNKNOWN").ljust(METER_VAL_W)
    if rating:
        return "%s%s[%s]" % (lab, val, rating)
    return "%s%s" % (lab, val)


def format_meter_row(label: str, value: str, rating: Optional[str],
                     bar: str, max_left: int) -> str:
    """Full meter row with ``|`` separator and ``[60s]`` suffix."""
    left = format_meter_left(label, value, rating).ljust(max_left)
    return "%s | %s [60s]" % (left, bar)


def meter_layout(total_w: int, lefts: List[str]) -> Tuple[int, int]:
    """Return ``(maxLeft, gw)`` so grouped rows share bar width."""
    max_left = max((len(s) for s in lefts), default=0)
    gw = max(10, total_w - max_left - len(" | ") - len(" [60s]") - 1)
    return (max_left, gw)


def format_extra_line(ch: str, phy: str, tx: str) -> str:
    """Fourth info row: ``ch <ch> phy <phy> tx <tx>``."""
    return "ch %s phy %s tx %s" % (ch, phy, tx)


def ansi_wrap(s: str, code: str) -> str:
    """Wrap s in ANSI colour; plain when NO_COLOR is set."""
    if os.environ.get("NO_COLOR"):
        return s
    return "\x1b[%sm%s\x1b[0m" % (code, s)


_SPARK_CHARS = "▁▂▃▄▅▆▇█"


class SparkHistory:
    """Fixed-length sample ring; renders ASCII block sparkline."""

    def __init__(self, maxlen: int = 60) -> None:
        self._buf: deque = deque(maxlen=max(1, maxlen))

    def append(self, v: Optional[float]) -> None:
        self._buf.append(v)

    def sparkline(self, lo: float, hi: float, width: int, align: str = "right") -> str:
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
        s = "".join(out)
        if align == "right" and len(s) < width:
            s = " " * (width - len(s)) + s
        return s


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
                    r_val_pad = rssi_s.ljust(METER_VAL_W)
                    s_val_pad = snr_s.ljust(METER_VAL_W)
                    n_val_pad = noise_s.ljust(METER_VAL_W)
                    lefts = [
                        format_meter_left("RSSI", rssi_s, r_rate),
                        format_meter_left("SNR", snr_s, s_rate),
                        format_meter_left("noise", noise_s, None),
                    ]
                    max_left, gw = meter_layout(w, lefts)
                    pad1 = " " * (max_left - len(lefts[0]))
                    pad2 = " " * (max_left - len(lefts[1]))
                    pad3 = " " * (max_left - len(lefts[2]))
                    if h >= 10:
                        rssi_g = state.hist_rssi.sparkline(-90, -30, gw)
                        snr_g = state.hist_snr.sparkline(0, 40, gw)
                        noise_g = state.hist_noise.sparkline(-100, -60, gw)
                        _emit_segs([("RSSI  ", 0),
                                    (r_val_pad + "[%s]" % r_rate, r_attr),
                                    (pad1 + " | ", 0),
                                    (rssi_g, r_attr), (" [60s]", 0)])
                        _emit_segs([("SNR   ", 0),
                                    (s_val_pad + "[%s]" % s_rate, s_attr),
                                    (pad2 + " | ", 0),
                                    (snr_g, s_attr), (" [60s]", 0)])
                        _emit_segs([("noise ", 0), (n_val_pad, r_attr),
                                    (pad3 + " | ", 0),
                                    (noise_g, r_attr), (" [60s]", 0)])
                        _emit(format_extra_line(ch_s, phy_s, tx_s))
                    else:
                        _emit_segs([("RSSI  ", 0),
                                    (r_val_pad + "[%s]" % r_rate, r_attr),
                                    (pad1, 0)])
                        _emit_segs([("SNR   ", 0),
                                    (s_val_pad + "[%s]" % s_rate, s_attr),
                                    (pad2, 0)])
                        _emit_segs([("noise ", 0), (n_val_pad, r_attr),
                                    (pad3, 0)])
                        _emit(format_extra_line(ch_s, phy_s, tx_s))
                manual = " (manual)" if state.ssid_override else ""
                _emit("Net: %s%s" % (state.net_ssid or "unknown", manual))
                toast, pending = state.ui_snapshot()
                _emit("loc: %s  pending: %d" % (
                    _location_label(conn, state.active_id), pending))
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
                print(("noise " + ansi_wrap(noise_s.ljust(METER_VAL_W), r_code)
                       + " " * (max_left - len(lefts[2]))), flush=True)
                print(format_extra_line(
                    state.sig.channel or "-", state.sig.phy or "-",
                    state.sig.tx_rate or "-"), flush=True)
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
                print("noise " + ansi_wrap(noise_s.ljust(METER_VAL_W), r_code)
                      + pad3 + " | " + ansi_wrap(noise_g, r_code)
                      + " [60s]", flush=True)
                print(format_extra_line(ch_s, phy_s, tx_s), flush=True)
            manual = " (manual)" if state.ssid_override else ""
            print("Net: %s%s" % (state.net_ssid or "unknown", manual), flush=True)
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
    "ansi_wrap",
    "finish_snapshot",
    "format_extra_line",
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
