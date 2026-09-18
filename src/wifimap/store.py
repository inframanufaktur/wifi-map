"""SQLite store: locations CRUD + readings insert + join queries."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Union

_SCHEMA = """
CREATE TABLE IF NOT EXISTS locations(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  floor INTEGER NOT NULL DEFAULT 0,
  outdoors INTEGER NOT NULL DEFAULT 0 CHECK(outdoors IN (0, 1)),
  UNIQUE(name, floor)
);
CREATE TABLE IF NOT EXISTS readings(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  location_id INTEGER NOT NULL REFERENCES locations(id),
  ssid TEXT, bssid TEXT,
  rssi INTEGER, noise INTEGER, snr INTEGER,
  channel TEXT, phy TEXT, tx_rate TEXT,
  ping_ms REAL, down_mbps REAL, up_mbps REAL,
  server TEXT, note TEXT
);
CREATE INDEX IF NOT EXISTS idx_readings_location ON readings(location_id);
"""


@dataclass
class Location:
    id: int
    name: str
    floor: int = 0
    outdoors: bool = False


@dataclass
class Reading:
    id: int = 0
    ts: str = ""
    location_id: int = 0
    ssid: Optional[str] = None
    bssid: Optional[str] = None
    rssi: Optional[int] = None
    noise: Optional[int] = None
    snr: Optional[int] = None
    channel: Optional[str] = None
    phy: Optional[str] = None
    tx_rate: Optional[str] = None
    ping_ms: Optional[float] = None
    down_mbps: Optional[float] = None
    up_mbps: Optional[float] = None
    server: Optional[str] = None
    note: Optional[str] = None


def get_db(path: Union[str, Path]) -> sqlite3.Connection:
    """Open DB at path with WAL mode, FK enforcement, and schema init."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


def create_location(
    conn: sqlite3.Connection,
    name: str,
    floor: int = 0,
    outdoors: bool = False,
) -> int:
    """Insert a location row; floor is any int (negative = basement)."""
    if not name:
        raise ValueError("location name must not be empty")
    cur = conn.execute(
        "INSERT INTO locations(name, floor, outdoors) VALUES (?, ?, ?)",
        (name, floor, 1 if outdoors else 0),
    )
    conn.commit()
    row = cur.lastrowid
    if row is None:
        raise sqlite3.Error("location insert returned no row id")
    return row


def list_locations(conn: sqlite3.Connection) -> List[Location]:
    """Return all locations ordered by id."""
    rows = conn.execute(
        "SELECT id, name, floor, outdoors FROM locations ORDER BY id"
    ).fetchall()
    return [
        Location(id=r[0], name=r[1], floor=r[2], outdoors=bool(r[3]))
        for r in rows
    ]


def resolve_location(
    conn: sqlite3.Connection,
    id_or_name: Union[int, str],
    floor: int = 0,
    outdoors: bool = False,
) -> int:
    """Resolve id|name to a location id, auto-creating unknown (name, floor).

    Numeric strings route to the id lookup first; a non-numeric (or
    unknown-id) string falls through to the (name, floor) lookup/create.
    """
    if isinstance(id_or_name, bool):
        raise ValueError("invalid location: %r" % (id_or_name,))
    if isinstance(id_or_name, int):
        row = conn.execute(
            "SELECT id FROM locations WHERE id = ?", (id_or_name,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown location id: %r" % (id_or_name,))
        return row[0]
    name = str(id_or_name)
    try:
        as_id = int(name)
    except ValueError:
        as_id = None
    if as_id is not None:
        row = conn.execute(
            "SELECT id FROM locations WHERE id = ?", (as_id,)
        ).fetchone()
        if row is not None:
            return row[0]
    row = conn.execute(
        "SELECT id FROM locations WHERE name = ? AND floor = ?",
        (name, floor),
    ).fetchone()
    if row is not None:
        return row[0]
    return create_location(conn, name, floor=floor, outdoors=outdoors)


def add_reading(
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
) -> int:
    """Insert one readings row; ts defaults to current UTC ISO timestamp."""
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO readings(
             ts, location_id, ssid, bssid, rssi, noise, snr,
             channel, phy, tx_rate, ping_ms, down_mbps, up_mbps,
             server, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            ts, location_id, ssid, bssid, rssi, noise, snr,
            channel, phy, tx_rate, ping_ms, down_mbps, up_mbps,
            server, note,
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
    floor: Optional[int] = None,
    limit: int = 50,
) -> List[dict]:
    """List readings newest-first as joined dicts (location_name/floor).

    Numeric location strings route to the id lookup (matching id OR name).
    """
    if limit < 0:
        raise ValueError("limit must be >= 0")
    query = (
        "SELECT r.id, r.ts, r.location_id, r.ssid, r.bssid, r.rssi,"
        " r.noise, r.snr, r.channel, r.phy, r.tx_rate,"
        " r.ping_ms, r.down_mbps, r.up_mbps, r.server, r.note,"
        " l.name AS location_name, l.floor AS floor,"
        " l.outdoors AS outdoors"
        " FROM readings r JOIN locations l ON r.location_id = l.id"
    )
    clauses = []
    params: List[Any] = []
    if location is not None:
        if isinstance(location, bool):
            raise ValueError("invalid location filter: %r" % (location,))
        if isinstance(location, int):
            clauses.append("r.location_id = ?")
            params.append(location)
        else:
            name = str(location)
            try:
                as_id = int(name)
            except ValueError:
                as_id = None
            if as_id is not None:
                clauses.append("(r.location_id = ? OR l.name = ?)")
                params.extend([as_id, name])
            else:
                clauses.append("l.name = ?")
                params.append(name)
    if floor is not None:
        clauses.append("l.floor = ?")
        params.append(floor)
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
