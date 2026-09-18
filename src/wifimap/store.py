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
  name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS rooms(
  id INTEGER PRIMARY KEY,
  location_id INTEGER NOT NULL REFERENCES locations(id),
  name TEXT NOT NULL,
  floor INTEGER NOT NULL DEFAULT 0,
  outdoors INTEGER NOT NULL DEFAULT 0 CHECK(outdoors IN (0, 1)),
  UNIQUE(location_id, name, floor)
);
CREATE TABLE IF NOT EXISTS spots(
  id INTEGER PRIMARY KEY,
  room_id INTEGER NOT NULL REFERENCES rooms(id),
  name TEXT NOT NULL,
  UNIQUE(room_id, name)
);
CREATE TABLE IF NOT EXISTS readings(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  spot_id INTEGER NOT NULL REFERENCES spots(id),
  ssid TEXT, bssid TEXT,
  rssi INTEGER, noise INTEGER, snr INTEGER,
  channel TEXT, phy TEXT, tx_rate TEXT,
  ping_ms REAL, down_mbps REAL, up_mbps REAL,
  server TEXT, note TEXT
);
CREATE INDEX IF NOT EXISTS idx_rooms_location ON rooms(location_id);
CREATE INDEX IF NOT EXISTS idx_spots_room ON spots(room_id);
CREATE INDEX IF NOT EXISTS idx_readings_spot ON readings(spot_id);
CREATE INDEX IF NOT EXISTS idx_readings_ssid ON readings(ssid);
"""
@dataclass
class Location:
    id: int
    name: str
@dataclass
class Room:
    id: int
    location_id: int
    name: str
    floor: int = 0
    outdoors: bool = False
@dataclass
class Spot:
    id: int
    room_id: int
    name: str


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
) -> int:
    """Insert a location row; duplicate names raise IntegrityError."""
    if not name:
        raise ValueError("location name must not be empty")
    cur = conn.execute(
        "INSERT INTO locations(name) VALUES (?)",
        (name,),
    )
    conn.commit()
    row = cur.lastrowid
    if row is None:
        raise sqlite3.Error("location insert returned no row id")
    return row


def list_locations(conn: sqlite3.Connection) -> List[Location]:
    """Return all locations ordered by id."""
    rows = conn.execute(
        "SELECT id, name FROM locations ORDER BY id"
    ).fetchall()
    return [
        Location(id=r[0], name=r[1])
        for r in rows
    ]


def get_location(
    conn: sqlite3.Connection,
    location_id: int,
) -> Optional[Location]:
    """Return a Location by id, or None if no such row."""
    if isinstance(location_id, bool):
        raise ValueError("invalid location id: %r" % (location_id,))
    row = conn.execute(
        "SELECT id, name FROM locations WHERE id = ?",
        (location_id,),
    ).fetchone()
    if row is None:
        return None
    return Location(id=row[0], name=row[1])


def resolve_location(
    conn: sqlite3.Connection,
    id_or_name: Union[int, str],
) -> int:
    """Resolve id|name to a location id, auto-creating unknown names.

    Numeric strings route to the id lookup first; a non-numeric (or
    unknown-id) string falls through to the name lookup/create.
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
        "SELECT id FROM locations WHERE name = ?",
        (name,),
    ).fetchone()
    if row is not None:
        return row[0]
    return create_location(conn, name)


def create_room(
    conn: sqlite3.Connection,
    location_id: int,
    name: str,
    floor: int = 0,
    outdoors: bool = False,
) -> int:
    """Insert a room row; unknown locations raise ValueError."""
    if not name:
        raise ValueError("room name must not be empty")
    if isinstance(location_id, bool):
        raise ValueError("unknown location id: %r" % (location_id,))
    row = conn.execute(
        "SELECT id FROM locations WHERE id = ?", (location_id,)
    ).fetchone()
    if row is None:
        raise ValueError("unknown location id: %r" % (location_id,))
    cur = conn.execute(
        "INSERT INTO rooms(location_id, name, floor, outdoors)"
        " VALUES (?, ?, ?, ?)",
        (location_id, name, floor, 1 if outdoors else 0),
    )
    conn.commit()
    rowid = cur.lastrowid
    if rowid is None:
        raise sqlite3.Error("room insert returned no row id")
    return rowid


def list_rooms(
    conn: sqlite3.Connection,
    location_id: Optional[int] = None,
) -> List[Room]:
    """Return rooms ordered by id, optionally filtered by location."""
    if location_id is None:
        rows = conn.execute(
            "SELECT id, location_id, name, floor, outdoors"
            " FROM rooms ORDER BY id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, location_id, name, floor, outdoors"
            " FROM rooms WHERE location_id = ? ORDER BY id",
            (location_id,),
        ).fetchall()
    return [
        Room(id=r[0], location_id=r[1], name=r[2], floor=r[3],
             outdoors=bool(r[4]))
        for r in rows
    ]


def get_room(
    conn: sqlite3.Connection,
    room_id: int,
) -> Optional[Room]:
    """Return a Room by id, or None if no such row."""
    row = conn.execute(
        "SELECT id, location_id, name, floor, outdoors"
        " FROM rooms WHERE id = ?",
        (room_id,),
    ).fetchone()
    if row is None:
        return None
    return Room(id=row[0], location_id=row[1], name=row[2],
                floor=row[3], outdoors=bool(row[4]))


def resolve_room(
    conn: sqlite3.Connection,
    location_id: int,
    name: Union[int, str],
    floor: int = 0,
    outdoors: bool = False,
) -> int:
    """Resolve room id|name (scoped to location), auto-creating unknown.

    Numeric strings route to the id lookup (scoped to location) first;
    anything else falls through to the (location_id, name, floor)
    lookup/create.
    """
    if isinstance(name, bool):
        raise ValueError("invalid room: %r" % (name,))
    if isinstance(name, int):
        row = conn.execute(
            "SELECT id FROM rooms WHERE id = ? AND location_id = ?",
            (name, location_id),
        ).fetchone()
        if row is None:
            raise ValueError("unknown room id: %r" % (name,))
        return row[0]
    label = str(name)
    try:
        as_id = int(label)
    except ValueError:
        as_id = None
    if as_id is not None:
        row = conn.execute(
            "SELECT id FROM rooms WHERE id = ? AND location_id = ?",
            (as_id, location_id),
        ).fetchone()
        if row is not None:
            return row[0]
    row = conn.execute(
        "SELECT id FROM rooms WHERE location_id = ? AND name = ?"
        " AND floor = ?",
        (location_id, label, floor),
    ).fetchone()
    if row is not None:
        return row[0]
    return create_room(conn, location_id, label, floor=floor,
                       outdoors=outdoors)


def update_room_floor(
    conn: sqlite3.Connection,
    room_id: int,
    floor: int,
) -> None:
    """Set a room's floor; raises ValueError if the id is unknown."""
    if isinstance(room_id, bool):
        raise ValueError("invalid room id: %r" % (room_id,))
    cur = conn.execute(
        "UPDATE rooms SET floor = ? WHERE id = ?",
        (floor, room_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise ValueError("unknown room id: %r" % (room_id,))


def create_spot(
    conn: sqlite3.Connection,
    room_id: int,
    name: str,
) -> int:
    """Insert a spot row; unknown rooms raise ValueError."""
    if not name:
        raise ValueError("spot name must not be empty")
    if isinstance(room_id, bool):
        raise ValueError("unknown room id: %r" % (room_id,))
    row = conn.execute(
        "SELECT id FROM rooms WHERE id = ?", (room_id,)
    ).fetchone()
    if row is None:
        raise ValueError("unknown room id: %r" % (room_id,))
    cur = conn.execute(
        "INSERT INTO spots(room_id, name) VALUES (?, ?)",
        (room_id, name),
    )
    conn.commit()
    rowid = cur.lastrowid
    if rowid is None:
        raise sqlite3.Error("spot insert returned no row id")
    return rowid


def list_spots(
    conn: sqlite3.Connection,
    room_id: Optional[int] = None,
) -> List[Spot]:
    """Return spots ordered by id, optionally filtered by room."""
    if room_id is None:
        rows = conn.execute(
            "SELECT id, room_id, name FROM spots ORDER BY id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, room_id, name FROM spots WHERE room_id = ?"
            " ORDER BY id",
            (room_id,),
        ).fetchall()
    return [
        Spot(id=r[0], room_id=r[1], name=r[2])
        for r in rows
    ]


def get_spot(
    conn: sqlite3.Connection,
    spot_id: int,
) -> Optional[Spot]:
    """Return a Spot by id, or None if no such row."""
    row = conn.execute(
        "SELECT id, room_id, name FROM spots WHERE id = ?",
        (spot_id,),
    ).fetchone()
    if row is None:
        return None
    return Spot(id=row[0], room_id=row[1], name=row[2])


def resolve_spot(
    conn: sqlite3.Connection,
    room_id: int,
    name: Union[int, str],
) -> int:
    """Resolve spot id|name (scoped to room), auto-creating unknown.

    Numeric strings route to the id lookup (scoped to room) first;
    anything else falls through to the (room_id, name) lookup/create.
    """
    if isinstance(name, bool):
        raise ValueError("invalid spot: %r" % (name,))
    if isinstance(name, int):
        row = conn.execute(
            "SELECT id FROM spots WHERE id = ? AND room_id = ?",
            (name, room_id),
        ).fetchone()
        if row is None:
            raise ValueError("unknown spot id: %r" % (name,))
        return row[0]
    label = str(name)
    try:
        as_id = int(label)
    except ValueError:
        as_id = None
    if as_id is not None:
        row = conn.execute(
            "SELECT id FROM spots WHERE id = ? AND room_id = ?",
            (as_id, room_id),
        ).fetchone()
        if row is not None:
            return row[0]
    row = conn.execute(
        "SELECT id FROM spots WHERE room_id = ? AND name = ?",
        (room_id, label),
    ).fetchone()
    if row is not None:
        return row[0]
    return create_spot(conn, room_id, label)


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
    ssid: Optional[str] = None,
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
    if ssid is not None:
        if isinstance(ssid, bool):
            raise ValueError("invalid ssid filter: %r" % (ssid,))
        clauses.append("r.ssid = ?")
        params.append(ssid)
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
