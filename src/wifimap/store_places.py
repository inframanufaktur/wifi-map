"""Persistence operations for locations, rooms, and spots."""
from __future__ import annotations

import sqlite3
from typing import List, Optional, Union

from wifimap.store_networks import create_location
from wifimap.store_schema import Room, Spot

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
