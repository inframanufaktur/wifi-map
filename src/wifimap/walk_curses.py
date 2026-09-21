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
    benchmark_lines,
    comparison_rows,
)
from wifimap.walk_snapshot import _finish_benchmark
from wifimap.walk_state import WalkState
from wifimap.walk_ui import (
    fmt_rate_val,
    format_addr_line,
    format_walk_footer,
    format_walk_header,
    format_walk_link_status,
    format_walk_signal_summary,
    format_walk_traffic_summary,
    history_cap,
    picker_start_cursor,
    rate_rssi,
    rate_snr,
    signal_panel_rows,
    walk_display_mode,
    walk_layout_mode,
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
        state.refresh_access_point_names(conn)
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
        state.start_path_monitor()
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
                curses.init_pair(4, curses.COLOR_CYAN, -1)
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
                footer_row = max(0, h - 2)

                def _emit(s: str, attr: int = 0) -> None:
                    nonlocal row
                    if row < footer_row:
                        try:
                            stdscr.addstr(row, 0, s[: w - 1], attr)
                        except Exception:
                            pass
                        row += 1

                def _emit_segs(segs: list) -> None:
                    nonlocal row
                    if row >= footer_row:
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

                def _draw(at_row: int, col: int, text: str,
                          attr: int = 0) -> None:
                    if at_row >= footer_row or col >= w - 1:
                        return
                    try:
                        stdscr.addstr(at_row, col, text[:w - 1 - col], attr)
                    except Exception:
                        pass

                def _draw_segs(at_row: int, col: int, segs: list) -> None:
                    if at_row >= footer_row or col >= w - 1:
                        return
                    cursor = col
                    try:
                        for text, attr in segs:
                            if cursor >= w - 1 or not text:
                                continue
                            chunk = text[:w - 1 - cursor]
                            stdscr.addstr(at_row, cursor, chunk, attr)
                            cursor += len(chunk)
                    except Exception:
                        pass

                accent_attr = ((curses.color_pair(4) if has_col else 0)
                               | curses.A_BOLD)

                def _styled(segments: list) -> list:
                    return [(text, accent_attr if pair == 4 else
                             curses.color_pair(pair) if has_col and pair else 0)
                            for text, pair in segments]

                def _bounded(segments: list, width: int) -> list:
                    result = []
                    for text, pair in segments:
                        chunk = text[:max(0, width)]
                        result.append((chunk, pair))
                        width -= len(chunk)
                    return result

                toast, pending = state.ui_snapshot()
                location = _location_label(conn, state.active_spot_id)
                header = format_walk_header(
                    state.net_ssid, location, pending)[:w - 1]
                _emit(header.ljust(max(0, w - 1)),
                      accent_attr | curses.A_REVERSE)
                _emit("")
                wide_compare = (
                    not state.no_wifi and
                    walk_layout_mode(w, state.baseline_walk_id is not None)
                    == "side_by_side")
                compare_width = 78 if wide_compare else w - 1
                compare_col = w - compare_width - 1 if wide_compare else 0
                live_width = compare_col - 3 if wide_compare else w - 1
                details = comparison_rows(state, compare_width)
                with state._lock:
                    bench = state.benchmark
                    last = state.last_result
                bench_rows = benchmark_lines(bench, compare_width)
                if bench_rows:
                    if details:
                        details.append([])
                    details.extend([[(line, 4 if index == 0 else 0)]
                                    for index, line in enumerate(bench_rows)])
                if last:
                    details.append([("last: %s" % last, 0)])

                addr = format_addr_line(state.ip, state.router, state.mac)
                address_rows = [[], [(addr, 0)]] if addr else []
                if state.no_wifi:
                    live = [[("NO-WIFI: %s" % state.no_wifi_msg, 0)],
                            [("`s` blocked; fix WiFi or quit with `q`.", 0)]]
                elif walk_display_mode(h, bool(details)) == "compact":
                    sig = state.sig
                    rssi = "UNKNOWN" if sig.rssi is None else "%d dBm" % sig.rssi
                    snr = "UNKNOWN" if sig.snr is None else "%d dB" % sig.snr
                    noise = "UNKNOWN" if sig.noise is None else "%d dBm" % sig.noise
                    live = [[(format_walk_signal_summary(
                        rssi, rate_rssi(sig.rssi), snr,
                        rate_snr(sig.snr), noise), 0)],
                        [(format_walk_link_status(
                            sig.channel, sig.phy, sig.tx_rate, sig.mcs,
                            sig.band, sig.security), 0)],
                        [(format_walk_traffic_summary(
                            fmt_rate_val(state.last_rates[0] if state.last_rates else None),
                            fmt_rate_val(state.last_rates[1] if state.last_rates else None)), 0)]]
                else:
                    live = signal_panel_rows(
                        state, live_width,
                        compact_path=not wide_compare and h < 40)
                live.extend(address_rows)
                if wide_compare:
                    for index, segments in enumerate(live):
                        _draw_segs(2 + index, 0,
                                   _styled(_bounded(segments, live_width)))
                    for index, segments in enumerate(details):
                        _draw_segs(2 + index, compare_col,
                                   _styled(_bounded(segments, compare_width)))
                else:
                    for segments in live:
                        _emit_segs(_styled(segments))
                    if details:
                        _emit("")
                    for segments in details:
                        _emit_segs(_styled(segments))
                try:
                    footer = format_walk_footer()[:w].ljust(w)
                    stdscr.addstr(footer_row, 0,
                                  footer,
                                  curses.A_REVERSE)
                    if toast and h > 1:
                        stdscr.addstr(h - 1, 0, ("» %s" % toast)[:w - 1])
                except Exception:
                    pass
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
            # loop; timeout already paces polling
    except KeyboardInterrupt:
        return 0
    finally:
        state.stop_path_monitor()
        _finish_walk_session(conn, state)
        conn.close()
