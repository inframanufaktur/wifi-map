"""Resolve prior walks and aggregate their readings by spot."""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Union

from wifimap import store as store_mod
from wifimap.walk_snapshot import SpotBaseline


@dataclass(frozen=True)
class BaselineSelection:
    walk_id: int
    walk_name: Optional[str]
    spots: Dict[int, SpotBaseline]


def median_values(values: Iterable[object]) -> Optional[float]:
    numeric = []
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            numeric.append(float(value))
        except (TypeError, ValueError):
            continue
    return float(statistics.median(numeric)) if numeric else None


def nonempty_walk_ids(conn: sqlite3.Connection) -> set:
    rows = conn.execute(
        "SELECT DISTINCT walk_id FROM readings WHERE walk_id IS NOT NULL"
    ).fetchall()
    return {int(row[0]) for row in rows}


def load_baseline(
    conn: sqlite3.Connection,
    location_id: int,
    current_walk_id: Optional[int],
    id_or_name: Union[int, str],
) -> BaselineSelection:
    """Resolve a non-empty prior walk and aggregate per-spot medians."""
    all_walks = store_mod.list_walks(conn, location_id=location_id)
    nonempty = nonempty_walk_ids(conn)
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
        if isinstance(id_or_name, int):
            known_empty = next((walk for walk in all_walks
                                if walk.id == id_or_name), None)
        else:
            requested = str(id_or_name).strip()
            known_empty = next((walk for walk in all_walks if (
                str(walk.id) == requested or walk.name == requested)), None)
        if known_empty is not None and known_empty.id not in nonempty:
            raise ValueError(
                "baseline walk has no readings: #%d" % known_empty.id)
        raise ValueError("unknown baseline walk: %r" % (id_or_name,))
    if selected.id == current_walk_id:
        raise ValueError("current walk cannot be its own baseline")

    rows = store_mod.list_readings(
        conn, walk_id=selected.id, limit=1_000_000)
    grouped: Dict[int, List[dict]] = {}
    for row in rows:
        grouped.setdefault(int(row["spot_id"]), []).append(row)

    spots = {}
    numeric_keys = (
        "rssi", "noise", "snr", "tx_rate", "ping_ms",
        "down_mbps", "up_mbps",
    )
    for spot_id, spot_rows in grouped.items():
        newest = spot_rows[0]
        speed_rows = [row for row in spot_rows if any(
            row.get(key) is not None
            for key in ("ping_ms", "down_mbps", "up_mbps"))]
        numeric = {
            key: median_values(row.get(key) for row in spot_rows)
            for key in numeric_keys
        }
        spots[spot_id] = SpotBaseline(
            spot_id=spot_id,
            ssid=newest.get("ssid"), bssid=newest.get("bssid"),
            channel=newest.get("channel"), server=newest.get("server"),
            reading_count=len(spot_rows), speed_count=len(speed_rows),
            latest_at=max(row["ts"] for row in spot_rows),
            servers=tuple(sorted({row.get("server") or "unknown"
                                  for row in speed_rows})),
            **numeric,
        )
    return BaselineSelection(selected.id, selected.name, spots)
