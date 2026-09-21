"""Persistence operations for location benchmarks."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from wifimap.store_networks import get_ssid, resolve_ssid

def set_benchmark(
    conn: sqlite3.Connection,
    location_id: int,
    ts: Optional[str] = None,
    ssid: Optional[str] = None,
    bssid: Optional[str] = None,
    rssi: Optional[int] = None,
    noise: Optional[int] = None,
    snr: Optional[int] = None,
    channel: Optional[str] = None,
    phy: Optional[str] = None,
    tx_rate: Optional[str] = None,
    ping_ms: Optional[float] = None,
    down_mbps: Optional[float] = None,
    up_mbps: Optional[float] = None,
    server: Optional[str] = None,
    note: Optional[str] = None,
    ssid_id: Optional[int] = None,
) -> None:
    """Upsert one benchmarks row per location; ts defaults to UTC ISO."""
    if isinstance(location_id, bool):
        raise ValueError("unknown location id: %r" % (location_id,))
    exists = conn.execute(
        "SELECT id FROM locations WHERE id = ?", (location_id,)
    ).fetchone()
    if exists is None:
        raise ValueError("unknown location id: %r" % (location_id,))
    if ssid_id is None and ssid is not None:
        ssid_id = resolve_ssid(conn, location_id, ssid)
    if ssid_id is not None:
        selected_ssid = get_ssid(conn, ssid_id)
        if selected_ssid is None:
            raise ValueError("unknown SSID id: %r" % (ssid_id,))
        if selected_ssid.location_id != location_id:
            raise ValueError("benchmark and SSID belong to a different location")
        if ssid is not None and selected_ssid.name != ssid:
            raise ValueError("SSID id and name do not match")
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO benchmarks(
             location_id, ts, ssid_id, bssid, rssi, noise, snr,
             channel, phy, tx_rate, ping_ms, down_mbps, up_mbps,
             server, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(location_id) DO UPDATE SET
             ts = excluded.ts, ssid_id = excluded.ssid_id,
             bssid = excluded.bssid, rssi = excluded.rssi,
             noise = excluded.noise, snr = excluded.snr,
             channel = excluded.channel, phy = excluded.phy,
             tx_rate = excluded.tx_rate, ping_ms = excluded.ping_ms,
             down_mbps = excluded.down_mbps, up_mbps = excluded.up_mbps,
             server = excluded.server, note = excluded.note""",
        (
            location_id, ts, ssid_id, bssid, rssi, noise, snr,
            channel, phy, tx_rate, ping_ms, down_mbps, up_mbps,
            server, note,
        ),
    )
    conn.commit()


def get_benchmark(
    conn: sqlite3.Connection,
    location_id: int,
) -> Optional[dict]:
    """Return the benchmark row for a location, or None if missing."""
    if isinstance(location_id, bool):
        raise ValueError("invalid location id: %r" % (location_id,))
    old = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT b.location_id, b.ts, b.ssid_id, n.name AS ssid, "
            "b.bssid, b.rssi, b.noise, b.snr,"
            " channel, phy, tx_rate, ping_ms, down_mbps, up_mbps,"
            " server, note FROM benchmarks b "
            "LEFT JOIN ssids n ON n.id = b.ssid_id "
            "WHERE b.location_id = ?",
            (location_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        conn.row_factory = old


def clear_benchmark(
    conn: sqlite3.Connection,
    location_id: int,
) -> None:
    """Delete the benchmark row for a location."""
    if isinstance(location_id, bool):
        raise ValueError("invalid location id: %r" % (location_id,))
    conn.execute(
        "DELETE FROM benchmarks WHERE location_id = ?",
        (location_id,),
    )
    conn.commit()


def format_benchmark_delta(cur: dict, bench: Optional[dict]) -> str:
    """Format cur-vs-benchmark deltas for rssi/snr/down/up."""
    if not bench:
        return ""
    if not cur:
        return ""
    specs = (
        ("rssi", "rssi", "%+d", True),
        ("snr", "snr", "%+d", True),
        ("down_mbps", "down", "%+.1f", False),
        ("up_mbps", "up", "%+.1f", False),
    )
    parts = []
    for key, label, fmt, as_int in specs:
        try:
            c = cur.get(key) if isinstance(cur, dict) else None
            b = bench.get(key) if isinstance(bench, dict) else None
        except AttributeError:
            continue
        if c is None or b is None:
            continue
        if isinstance(c, bool) or isinstance(b, bool):
            continue
        if not isinstance(c, (int, float)) or not isinstance(b, (int, float)):
            continue
        diff = c - b
        if as_int:
            parts.append("%s %s" % (label, fmt % round(diff)))
        else:
            parts.append("%s %s" % (label, fmt % diff))
    return ", ".join(parts)
