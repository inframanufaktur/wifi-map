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
CREATE TABLE IF NOT EXISTS walks(
  id INTEGER PRIMARY KEY,
  location_id INTEGER NOT NULL REFERENCES locations(id),
  name TEXT NOT NULL CHECK(trim(name) <> ''),
  started_at TEXT NOT NULL,
  ended_at TEXT
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
  server TEXT, note TEXT,
  walk_id INTEGER REFERENCES walks(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_rooms_location ON rooms(location_id);
CREATE INDEX IF NOT EXISTS idx_spots_room ON spots(room_id);
CREATE INDEX IF NOT EXISTS idx_readings_spot ON readings(spot_id);
CREATE INDEX IF NOT EXISTS idx_readings_ssid ON readings(ssid);
CREATE TABLE IF NOT EXISTS benchmarks(location_id INTEGER PRIMARY KEY REFERENCES locations(id) ON DELETE CASCADE, ts TEXT NOT NULL, ssid TEXT, bssid TEXT, rssi INTEGER, noise INTEGER, snr INTEGER, channel TEXT, phy TEXT, tx_rate TEXT, ping_ms REAL, down_mbps REAL, up_mbps REAL, server TEXT, note TEXT);
"""
_SCHEMA_VERSION = 1


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


@dataclass
class Walk:
    id: int
    location_id: int
    name: str
    started_at: str
    ended_at: Optional[str] = None


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply additive schema migrations to an existing database."""
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(readings)")
    }
    with conn:
        if "walk_id" not in columns:
            conn.execute(
                "ALTER TABLE readings ADD COLUMN walk_id INTEGER "
                "REFERENCES walks(id) ON DELETE SET NULL"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_readings_walk_spot "
            "ON readings(walk_id, spot_id)"
        )
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < _SCHEMA_VERSION:
            conn.execute("PRAGMA user_version = %d" % _SCHEMA_VERSION)


def get_db(path: Union[str, Path]) -> sqlite3.Connection:
    """Open DB at path with WAL mode, FK enforcement, and schema init."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        _migrate_schema(conn)
    except Exception:
        conn.close()
        raise
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


def create_walk(
    conn: sqlite3.Connection,
    location_id: int,
    name: str,
    started_at: Optional[str] = None,
) -> int:
    """Create a named walk for one location and return its id."""
    if isinstance(location_id, bool):
        raise ValueError("unknown location id: %r" % (location_id,))
    if not isinstance(name, str) or not name.strip():
        raise ValueError("walk name must not be blank")
    exists = conn.execute(
        "SELECT id FROM locations WHERE id = ?", (location_id,)
    ).fetchone()
    if exists is None:
        raise ValueError("unknown location id: %r" % (location_id,))
    if started_at is None:
        started_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO walks(location_id, name, started_at) VALUES (?, ?, ?)",
        (location_id, name.strip(), started_at),
    )
    conn.commit()
    rowid = cur.lastrowid
    if rowid is None:
        raise sqlite3.Error("walk insert returned no row id")
    return rowid


def get_walk(
    conn: sqlite3.Connection,
    walk_id: int,
) -> Optional[Walk]:
    """Return a walk by id, or None when it does not exist."""
    if isinstance(walk_id, bool):
        raise ValueError("invalid walk id: %r" % (walk_id,))
    row = conn.execute(
        "SELECT id, location_id, name, started_at, ended_at "
        "FROM walks WHERE id = ?",
        (walk_id,),
    ).fetchone()
    if row is None:
        return None
    return Walk(id=row[0], location_id=row[1], name=row[2],
                started_at=row[3], ended_at=row[4])


def list_walks(
    conn: sqlite3.Connection,
    location_id: Optional[int] = None,
) -> List[Walk]:
    """Return walks newest-first, optionally for one location."""
    if isinstance(location_id, bool):
        raise ValueError("invalid location id: %r" % (location_id,))
    query = (
        "SELECT id, location_id, name, started_at, ended_at "
        "FROM walks"
    )
    params = []
    if location_id is not None:
        query += " WHERE location_id = ?"
        params.append(location_id)
    query += " ORDER BY id DESC"
    rows = conn.execute(query, params).fetchall()
    return [
        Walk(id=row[0], location_id=row[1], name=row[2],
             started_at=row[3], ended_at=row[4])
        for row in rows
    ]


def finish_walk(
    conn: sqlite3.Connection,
    walk_id: int,
    ended_at: Optional[str] = None,
) -> None:
    """Finish a walk once; subsequent calls preserve its end time."""
    if isinstance(walk_id, bool):
        raise ValueError("unknown walk id: %r" % (walk_id,))
    if get_walk(conn, walk_id) is None:
        raise ValueError("unknown walk id: %r" % (walk_id,))
    if ended_at is None:
        ended_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE walks SET ended_at = COALESCE(ended_at, ?) WHERE id = ?",
        (ended_at, walk_id),
    )
    conn.commit()


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


def lookup_location(
    conn: sqlite3.Connection,
    id_or_name: Union[int, str],
) -> int:
    """Resolve id|name to a location id without creating anything.

    Same routing as ``resolve_location`` but unknown names/ids raise
    ValueError instead of auto-creating. Read paths (list) must use
    this so a typo can't silently insert rows.
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
    raise ValueError("unknown location: %r" % (id_or_name,))


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


def lookup_room(
    conn: sqlite3.Connection,
    location_id: int,
    name: Union[int, str],
) -> int:
    """Resolve room id|name (scoped to location) without creating anything.

    Same routing as ``resolve_room`` but unknown names/ids raise
    ValueError instead of auto-creating. Read paths (list) must use
    this so a typo can't silently insert rows.
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
        "SELECT id FROM rooms WHERE location_id = ? AND name = ?",
        (location_id, label),
    ).fetchone()
    if row is not None:
        return row[0]
    raise ValueError("unknown room: %r" % (name,))


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
    server: Optional[str] = None,
    note: Optional[str] = None,
    walk_id: Optional[int] = None,
) -> int:
    """Insert one readings row; ts defaults to current UTC ISO timestamp."""
    if isinstance(walk_id, bool):
        raise ValueError("invalid walk id: %r" % (walk_id,))
    if walk_id is not None:
        walk = get_walk(conn, walk_id)
        if walk is None:
            raise ValueError("unknown walk id: %r" % (walk_id,))
        spot_location = conn.execute(
            "SELECT m.location_id FROM spots s "
            "JOIN rooms m ON m.id = s.room_id WHERE s.id = ?",
            (spot_id,),
        ).fetchone()
        if spot_location is None:
            raise ValueError("unknown spot id: %r" % (spot_id,))
        if spot_location[0] != walk.location_id:
            raise ValueError(
                "walk and reading spot belong to a different location"
            )
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO readings(
             ts, spot_id, ssid, bssid, rssi, noise, snr,
             channel, phy, tx_rate, ping_ms, down_mbps, up_mbps,
             server, note, walk_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            ts, spot_id, ssid, bssid, rssi, noise, snr,
            channel, phy, tx_rate, ping_ms, down_mbps, up_mbps,
            server, note, walk_id,
        ),
    )
    conn.commit()
    row = cur.lastrowid
    if row is None:
        raise sqlite3.Error("reading insert returned no row id")
    return row


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
) -> None:
    """Upsert one benchmarks row per location; ts defaults to UTC ISO."""
    if isinstance(location_id, bool):
        raise ValueError("unknown location id: %r" % (location_id,))
    exists = conn.execute(
        "SELECT id FROM locations WHERE id = ?", (location_id,)
    ).fetchone()
    if exists is None:
        raise ValueError("unknown location id: %r" % (location_id,))
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO benchmarks(
             location_id, ts, ssid, bssid, rssi, noise, snr,
             channel, phy, tx_rate, ping_ms, down_mbps, up_mbps,
             server, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(location_id) DO UPDATE SET
             ts = excluded.ts, ssid = excluded.ssid,
             bssid = excluded.bssid, rssi = excluded.rssi,
             noise = excluded.noise, snr = excluded.snr,
             channel = excluded.channel, phy = excluded.phy,
             tx_rate = excluded.tx_rate, ping_ms = excluded.ping_ms,
             down_mbps = excluded.down_mbps, up_mbps = excluded.up_mbps,
             server = excluded.server, note = excluded.note""",
        (
            location_id, ts, ssid, bssid, rssi, noise, snr,
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
            "SELECT location_id, ts, ssid, bssid, rssi, noise, snr,"
            " channel, phy, tx_rate, ping_ms, down_mbps, up_mbps,"
            " server, note FROM benchmarks WHERE location_id = ?",
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
        "SELECT r.id, r.ts, r.spot_id, r.ssid, r.bssid, r.rssi,"
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
        clauses.append("r.ssid = ?")
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
