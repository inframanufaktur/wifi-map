"""Persistence operations for locations, SSIDs, and named walks."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import List, Optional, Union

from wifimap.store_schema import Location, SSID, Walk


def create_location(conn: sqlite3.Connection, name: str) -> int:
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
    return [Location(id=row[0], name=row[1]) for row in rows]


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


def create_ssid(
    conn: sqlite3.Connection,
    location_id: int,
    name: str,
) -> int:
    """Create one case-sensitive SSID name within a location."""
    if isinstance(location_id, bool) or get_location(conn, location_id) is None:
        raise ValueError("unknown location id: %r" % (location_id,))
    if not isinstance(name, str) or not name.strip():
        raise ValueError("SSID name must not be blank")
    cur = conn.execute(
        "INSERT INTO ssids(location_id, name) VALUES (?, ?)",
        (location_id, name.strip()),
    )
    conn.commit()
    rowid = cur.lastrowid
    if rowid is None:
        raise sqlite3.Error("SSID insert returned no row id")
    return rowid


def get_ssid(
    conn: sqlite3.Connection,
    ssid_id: int,
) -> Optional[SSID]:
    """Return an SSID by id, or None when it does not exist."""
    if isinstance(ssid_id, bool):
        raise ValueError("invalid SSID id: %r" % (ssid_id,))
    row = conn.execute(
        "SELECT id, location_id, name FROM ssids WHERE id = ?",
        (ssid_id,),
    ).fetchone()
    if row is None:
        return None
    return SSID(id=row[0], location_id=row[1], name=row[2])


def list_ssids(
    conn: sqlite3.Connection,
    location_id: int,
) -> List[SSID]:
    """List the SSIDs owned by one location in creation order."""
    if isinstance(location_id, bool) or get_location(conn, location_id) is None:
        raise ValueError("unknown location id: %r" % (location_id,))
    rows = conn.execute(
        "SELECT id, location_id, name FROM ssids "
        "WHERE location_id = ? ORDER BY id",
        (location_id,),
    ).fetchall()
    return [SSID(id=row[0], location_id=row[1], name=row[2]) for row in rows]


def resolve_ssid(
    conn: sqlite3.Connection,
    location_id: int,
    id_or_name: Union[int, str],
) -> int:
    """Resolve a location-owned SSID id/name, creating an unknown name."""
    if isinstance(id_or_name, bool):
        raise ValueError("invalid SSID: %r" % (id_or_name,))
    if isinstance(location_id, bool) or get_location(conn, location_id) is None:
        raise ValueError("unknown location id: %r" % (location_id,))
    if isinstance(id_or_name, int):
        ssid = get_ssid(conn, id_or_name)
        if ssid is None or ssid.location_id != location_id:
            raise ValueError("unknown SSID id for location: %r" % id_or_name)
        return ssid.id
    label = str(id_or_name).strip()
    if not label:
        raise ValueError("SSID name must not be blank")
    try:
        as_id = int(label)
    except ValueError:
        as_id = None
    if as_id is not None:
        ssid = get_ssid(conn, as_id)
        if ssid is not None and ssid.location_id == location_id:
            return ssid.id
    row = conn.execute(
        "SELECT id FROM ssids WHERE location_id = ? AND name = ?",
        (location_id, label),
    ).fetchone()
    if row is not None:
        return row[0]
    return create_ssid(conn, location_id, label)


def create_walk(
    conn: sqlite3.Connection,
    location_id: int,
    name: str,
    started_at: Optional[str] = None,
    ssid_id: Optional[int] = None,
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
    if ssid_id is not None:
        ssid = get_ssid(conn, ssid_id)
        if ssid is None:
            raise ValueError("unknown SSID id: %r" % (ssid_id,))
        if ssid.location_id != location_id:
            raise ValueError("walk and SSID belong to a different location")
    if started_at is None:
        started_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO walks(location_id, ssid_id, name, started_at) "
        "VALUES (?, ?, ?, ?)",
        (location_id, ssid_id, name.strip(), started_at),
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
        "SELECT id, location_id, name, started_at, ended_at, ssid_id "
        "FROM walks WHERE id = ?",
        (walk_id,),
    ).fetchone()
    if row is None:
        return None
    return Walk(id=row[0], location_id=row[1], name=row[2],
                started_at=row[3], ended_at=row[4], ssid_id=row[5])


def list_walks(
    conn: sqlite3.Connection,
    location_id: Optional[int] = None,
) -> List[Walk]:
    """Return walks newest-first, optionally for one location."""
    if isinstance(location_id, bool):
        raise ValueError("invalid location id: %r" % (location_id,))
    query = (
        "SELECT id, location_id, name, started_at, ended_at, ssid_id "
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
             started_at=row[3], ended_at=row[4], ssid_id=row[5])
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
