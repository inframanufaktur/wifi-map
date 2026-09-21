"""Plain ANSI renderer and input loop for walk mode."""
from __future__ import annotations

import shutil
import sqlite3
import sys
import time
from typing import Optional, Union

from wifimap import store as store_mod
from wifimap.walk_keys import (
    FALLBACK_SETTLE_SLEEP,
    KEY_BENCHMARK,
    KEY_COMPARE,
    KEY_NEW,
    KEY_SNAPSHOT,
    KEY_SPEED_PROBE,
    KEY_SWITCH,
    QUIT_WORDS,
)
from wifimap.walk_picker import (
    _fallback_create,
    _fallback_pick,
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
    ansi_wrap,
    fmt_mbps,
    fmt_rate_val,
    format_addr_line,
    format_extra_line,
    format_meter_left,
    format_radio_line,
    history_cap,
    meter_layout,
    rate_rssi,
    rate_snr,
    rating_style,
)


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
                   network_bssid: Optional[str] = None,
                   state_factory=WalkState) -> int:
    """ANSI fallback when curses/tty unavailable.

    Limit: keys are line-buffered (type a key + Enter); no live refresh
    while waiting for input. Same ``s``/``b``/``l``/``n``/``q`` keys.
    """
    import select

    try:
        conn = store_mod.get_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
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
        state.start_path_monitor()
        print("walk fallback (no curses): type a key + Enter", flush=True)
        print("keys: s snapshot | t throughput | c compare | b benchmark | "
              "l switch | n new | q quit", flush=True)
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
    except KeyboardInterrupt:
        return 0
    finally:
        state.stop_path_monitor()
        _finish_walk_session(conn, state)
        conn.close()
