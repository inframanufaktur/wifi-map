"""Persistence operations for Wi-Fi readings."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, List, Optional, Union

from wifimap.store_networks import get_ssid, get_walk, resolve_ssid

def add_reading(
    conn: sqlite3.Connection,
    spot_id: int,
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
    path_probe_count: Optional[int] = None,
    gateway_rtt_ms: Optional[float] = None,
    gateway_p95_ms: Optional[float] = None,
    gateway_loss_pct: Optional[float] = None,
    gateway_max_outage_ms: Optional[int] = None,
    internet_rtt_ms: Optional[float] = None,
    internet_p95_ms: Optional[float] = None,
    internet_loss_pct: Optional[float] = None,
    internet_max_outage_ms: Optional[int] = None,
    server: Optional[str] = None,
    note: Optional[str] = None,
    walk_id: Optional[int] = None,
    ssid_id: Optional[int] = None,
) -> int:
    """Insert one readings row; ts defaults to current UTC ISO timestamp."""
    spot_location = conn.execute(
        "SELECT m.location_id FROM spots s "
        "JOIN rooms m ON m.id = s.room_id WHERE s.id = ?",
        (spot_id,),
    ).fetchone()
    if spot_location is None:
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")
    location_id = spot_location[0]
    if ssid_id is None and ssid is not None:
        ssid_id = resolve_ssid(conn, location_id, ssid)
    if ssid_id is not None:
        selected_ssid = get_ssid(conn, ssid_id)
        if selected_ssid is None:
            raise ValueError("unknown SSID id: %r" % (ssid_id,))
        if selected_ssid.location_id != location_id:
            raise ValueError("reading and SSID belong to a different location")
        if ssid is not None and selected_ssid.name != ssid:
            raise ValueError("SSID id and name do not match")
    if isinstance(walk_id, bool):
        raise ValueError("invalid walk id: %r" % (walk_id,))
    if walk_id is not None:
        walk = get_walk(conn, walk_id)
        if walk is None:
            raise ValueError("unknown walk id: %r" % (walk_id,))
        if location_id != walk.location_id:
            raise ValueError(
                "walk and reading spot belong to a different location"
            )
        if (walk.ssid_id is not None and ssid_id is not None
                and walk.ssid_id != ssid_id):
            raise ValueError("walk and reading use a different SSID")
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO readings(
             ts, spot_id, ssid_id, bssid, rssi, noise, snr,
             channel, phy, tx_rate, ping_ms, down_mbps, up_mbps,
             path_probe_count,
             gateway_rtt_ms, gateway_p95_ms, gateway_loss_pct,
             gateway_max_outage_ms,
             internet_rtt_ms, internet_p95_ms, internet_loss_pct,
             internet_max_outage_ms, server, note, walk_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?)""",
        (
            ts, spot_id, ssid_id, bssid, rssi, noise, snr,
            channel, phy, tx_rate, ping_ms, down_mbps, up_mbps,
            path_probe_count,
            gateway_rtt_ms, gateway_p95_ms, gateway_loss_pct,
            gateway_max_outage_ms,
            internet_rtt_ms, internet_p95_ms, internet_loss_pct,
            internet_max_outage_ms,
            server, note, walk_id,
        ),
    )
    conn.commit()
    row = cur.lastrowid
    if row is None:
        raise sqlite3.Error("reading insert returned no row id")
    return row


def list_readings(
    conn: sqlite3.Connection,
    location: Optional[Union[int, str]] = None,
    room: Optional[Union[int, str]] = None,
    spot: Optional[Union[int, str]] = None,
    floor: Optional[int] = None,
    ssid: Optional[str] = None,
    limit: int = 50,
    walk_id: Optional[int] = None,
) -> List[dict]:
    """List readings newest-first as joined dicts across all 3 levels.

    Numeric location/room/spot strings route to the id lookup
    (matching id OR name).
    """
    if isinstance(limit, bool) or limit < 0:
        raise ValueError("limit must be >= 0")
    query = (
        "SELECT r.id, r.ts, r.spot_id, r.ssid_id, n.name AS ssid, "
        "r.bssid, r.rssi,"
        " r.noise, r.snr, r.channel, r.phy, r.tx_rate,"
        " r.ping_ms, r.down_mbps, r.up_mbps, r.server, r.note,"
        " r.walk_id, w.name AS walk_name,"
        " w.started_at AS walk_started_at, w.ended_at AS walk_ended_at,"
        " s.name AS spot_name, s.room_id AS room_id,"
        " m.name AS room_name, m.location_id AS location_id,"
        " m.floor AS floor, m.outdoors AS outdoors,"
        " l.name AS location_name"
        " FROM readings r JOIN spots s ON r.spot_id = s.id"
        " JOIN rooms m ON s.room_id = m.id"
        " JOIN locations l ON m.location_id = l.id"
        " LEFT JOIN ssids n ON r.ssid_id = n.id"
        " LEFT JOIN walks w ON r.walk_id = w.id"
    )
    clauses = []
    params: List[Any] = []
    if location is not None:
        if isinstance(location, bool):
            raise ValueError("invalid location filter: %r" % (location,))
        if isinstance(location, int):
            clauses.append("m.location_id = ?")
            params.append(location)
        else:
            name = str(location)
            try:
                as_id = int(name)
            except ValueError:
                as_id = None
            if as_id is not None:
                clauses.append("(m.location_id = ? OR l.name = ?)")
                params.extend([as_id, name])
            else:
                clauses.append("l.name = ?")
                params.append(name)
    if room is not None:
        if isinstance(room, bool):
            raise ValueError("invalid room filter: %r" % (room,))
        if isinstance(room, int):
            clauses.append("s.room_id = ?")
            params.append(room)
        else:
            name = str(room)
            try:
                as_id = int(name)
            except ValueError:
                as_id = None
            if as_id is not None:
                clauses.append("(s.room_id = ? OR m.name = ?)")
                params.extend([as_id, name])
            else:
                clauses.append("m.name = ?")
                params.append(name)
    if spot is not None:
        if isinstance(spot, bool):
            raise ValueError("invalid spot filter: %r" % (spot,))
        if isinstance(spot, int):
            clauses.append("r.spot_id = ?")
            params.append(spot)
        else:
            name = str(spot)
            try:
                as_id = int(name)
            except ValueError:
                as_id = None
            if as_id is not None:
                clauses.append("(r.spot_id = ? OR s.name = ?)")
                params.extend([as_id, name])
            else:
                clauses.append("s.name = ?")
                params.append(name)
    if floor is not None:
        if isinstance(floor, bool):
            raise ValueError("invalid floor filter: %r" % (floor,))
        clauses.append("m.floor = ?")
        params.append(floor)
    if ssid is not None:
        if isinstance(ssid, bool):
            raise ValueError("invalid ssid filter: %r" % (ssid,))
        clauses.append("n.name = ?")
        params.append(ssid)
    if walk_id is not None:
        if isinstance(walk_id, bool):
            raise ValueError("invalid walk id filter: %r" % (walk_id,))
        clauses.append("r.walk_id = ?")
        params.append(walk_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY r.id DESC LIMIT ?"
    params.append(limit)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.row_factory = None
