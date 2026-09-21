"""Curses renderer and input loop for walk mode."""
from __future__ import annotations

import sqlite3
import time
from typing import Optional, Union

from wifimap import store as store_mod
from wifimap.walk_keys import (
    DB_OPEN_FAIL_SLEEP,
    KEY_BENCHMARK,
    KEY_COMPARE,
    KEY_CTRL_C,
    KEY_FLOOR,
    KEY_NEW,
    KEY_NO_INPUT,
    KEY_QUIT,
    KEY_QUIT_UPPER,
    KEY_SNAPSHOT,
    KEY_SPEED_PROBE,
    KEY_SWITCH,
    poll_timeout_ms,
)
from wifimap.walk_picker import (
    KEY_ENTER_CODES,
    KEY_ESC,
    PICKER_TIMEOUT_RESTORE_MS,
    _create_room_curses,
    _create_spot_curses,
    _pick_room_spot_curses,
    _prompt_curses,
)
from wifimap.walk_session import (
    _baseline_walk_rows,
    _finish_walk_session,
    _location_label,
    _resolve_preset,
    _start_walk_session,
    comparison_lines,
)
from wifimap.walk_snapshot import _finish_benchmark
from wifimap.walk_state import WalkState
from wifimap.walk_ui import (
    METER_VAL_W,
    fmt_mbps,
    fmt_rate_val,
    format_addr_line,
    format_extra_line,
    format_meter_left,
    format_radio_line,
    history_cap,
    meter_layout,
    parse_floor_input,
    picker_start_cursor,
    rate_rssi,
    rate_snr,
    rating_style,
)


_poll_timeout_ms = poll_timeout_ms


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
                 network_bssid: Optional[str] = None,
                 state_factory=WalkState) -> int:
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
    state = state_factory(db_path, no_speedtest=no_speedtest,
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
