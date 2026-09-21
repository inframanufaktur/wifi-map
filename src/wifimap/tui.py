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
from typing import Callable, Dict, List, Optional, Tuple, Union

from wifimap import signal as signal_mod
from wifimap import ssid as ssid_mod
from wifimap import speed as speed_mod
from wifimap import store as store_mod
from wifimap import traffic as traffic_mod
from wifimap.walk_picker import (
    KEY_CREATE,
    KEY_ENTER_CODES,
    KEY_ESC,
    PICKER_TIMEOUT_RESTORE_MS,
    PickerSelection,
    _active_room_id,
    _create_room_curses,
    _create_spot_curses,
    _fallback_create,
    _fallback_create_room,
    _fallback_create_spot,
    _fallback_floor,
    _fallback_pick,
    _fallback_pick_level,
    _find_room_by_name,
    _list_picker_curses,
    _pick_room_spot_curses,
    _prompt_curses,
    _room_picker_curses,
    _spot_picker_curses,
)
from wifimap.walk_snapshot import (
    MetricComparison,
    SnapshotResult,
    SpeedProbeResult,
    SpotBaseline,
    _finish_benchmark,
    _sample_guarded,
    finish_snapshot,
    start_snapshot_thread,
)
from wifimap.walk_state import (
    WalkState as _BaseWalkState,
    _nonempty_walk_ids,
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
QUIT_WORDS = ("q", "quit", "exit")

KEY_CTRL_C = 3
KEY_NO_INPUT = -1

POLL_TIMEOUT_MIN_MS = 50
FALLBACK_SETTLE_SLEEP = 0.1
DB_OPEN_FAIL_SLEEP = 2.0
# Signal averaging (5s) + Ookla's 120s timeout + shutdown overhead.
WORKER_DRAIN_TIMEOUT = 130.0


def _poll_timeout_ms(interval: float) -> int:
    """Poll getch timeout for walk loop; floor keeps fast intervals usable."""
    return max(POLL_TIMEOUT_MIN_MS, int(interval * 1000))

class WalkState(_BaseWalkState):
    """Walk state with the legacy snapshot-thread patch point."""

    def _start_snapshot_thread(self, *args, **kwargs):
        return start_snapshot_thread(*args, **kwargs)




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
