"""Walk comparison display and named-session lifecycle helpers."""
from __future__ import annotations

import sqlite3
import sys
import textwrap
import time
from typing import List, Optional, Tuple, Union

from wifimap import store as store_mod
from wifimap.walk_baseline import nonempty_walk_ids
from wifimap.walk_ui import fmt_mbps


WORKER_DRAIN_TIMEOUT = 130.0


_COMPARE_ROWS = (
    ("RSSI", "rssi", "dBm", 0, True, "stronger", "weaker"),
    ("noise", "noise", "dBm", 0, False, "noisier", "quieter"),
    ("SNR", "snr", "dB", 0, True, "more margin", "less margin"),
    ("TX rate", "tx_rate", "Mbps", 0, None, "higher link rate", "lower link rate"),
    ("ping", "ping_ms", "ms", 1, False, "more latency", "less latency"),
    ("down", "down_mbps", "Mbps", 1, True, "faster", "slower"),
    ("up", "up_mbps", "Mbps", 1, True, "faster", "slower"),
)


def _compare_number(value: Optional[float], decimals: int,
                    signed: bool = False) -> str:
    if value is None:
        return "-"
    if decimals == 0:
        return (("%+d" if signed else "%d") % int(round(value)))
    return (("%+.*f" if signed else "%.*f") % (decimals, value))


def comparison_rows(state: "WalkState", width: int = 98) -> List[List[Tuple[str, int]]]:
    """Semantic text/colour segments, shared by both walk renderers."""
    if state.baseline_walk_id is None:
        return []
    label = state.baseline_walk_name or str(state.baseline_walk_id)
    rows = [[("COMPARE · %s (#%s) → current walk #%s" % (
            label, state.baseline_walk_id,
            state.current_walk_id if state.current_walk_id is not None else "-"), 4)]]
    baseline = state.baseline_for_active_spot()
    if baseline is not None:
        rows.append([("before: %s · %d readings · %d speed tests (medians)" % (
            (baseline.latest_at or "date unknown")[:10],
            baseline.reading_count, baseline.speed_count), 0)])
    rows.append([("metric       now              before           change", 0)])
    comparison = state.active_comparison()
    for metric_label, key, unit, decimals, higher_is_better, rising, falling in _COMPARE_ROWS:
        item = comparison[key]
        current = "%s %s" % (_compare_number(item.current, decimals), unit)
        before = "%s %s" % (_compare_number(item.baseline, decimals), unit)
        delta = round(item.delta, decimals) if item.delta is not None else None
        pair = 0
        if delta is None:
            change = "—"
        else:
            if delta == 0:
                delta = 0.0  # Avoid displaying a rounded negative zero.
                outcome = "unchanged"
            else:
                outcome = rising if delta > 0 else falling
            delta_unit = "dB" if unit == "dBm" else unit
            change = "%s %s  %s" % (
                _compare_number(delta, decimals, signed=delta != 0), delta_unit, outcome)
            if delta:
                pair = (4 if higher_is_better is None else
                        1 if (delta > 0) == higher_is_better else 3)
        rows.append([("%-11s %-16s %-16s " % (
            metric_label, current, before), 0), (change, pair)])
    notes = []
    if baseline is None:
        notes.append("! No baseline captured at this spot" if state.active_spot_id
                     is not None else "Select a spot to compare its readings")
    probe = state.speed_probe_for_active_spot()
    if probe is not None or (baseline is not None and baseline.speed_count):
        current_server = (probe.server or "unknown") if probe else "—"
        servers = baseline.servers if baseline is not None else ()
        notes.append("Test server: now %s · before %s" % (
            current_server, ", ".join(servers) if servers else "—"))
    for note in notes:
        rows.extend([[(line, 0)] for line in textwrap.wrap(note, max(1, width))])
    return rows


def comparison_lines(state: "WalkState", width: int = 98) -> List[str]:
    return ["".join(text for text, _pair in row)
            for row in comparison_rows(state, width)]


def benchmark_lines(benchmark: Optional[dict], width: int = 98) -> List[str]:
    """Saved benchmark values, date, and any capture context."""
    if benchmark is None:
        return []
    date = (benchmark.get("ts") or "date unknown")[:10]
    lines = ["BENCHMARK · %s" % date,
             "RSSI %s dBm · SNR %s dB · down %s · up %s Mbps" % (
                 benchmark.get("rssi") if benchmark.get("rssi") is not None else "-",
                 benchmark.get("snr") if benchmark.get("snr") is not None else "-",
                 fmt_mbps(benchmark.get("down_mbps")),
                 fmt_mbps(benchmark.get("up_mbps")))]
    context = [str(benchmark[key]) for key in ("note", "server") if benchmark.get(key)]
    if context:
        lines.extend(textwrap.wrap(" · ".join(context), max(1, width)))
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
    nonempty = nonempty_walk_ids(conn)
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
