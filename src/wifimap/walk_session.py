"""Walk comparison display and named-session lifecycle helpers."""
from __future__ import annotations

import sqlite3
import sys
import time
from typing import List, Optional, Tuple, Union

from wifimap import store as store_mod
from wifimap.walk_state import _nonempty_walk_ids


WORKER_DRAIN_TIMEOUT = 130.0


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
