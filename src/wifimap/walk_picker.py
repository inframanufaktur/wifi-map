"""Line-based room and spot picker workflows for walk mode."""
from __future__ import annotations

import sqlite3
from typing import List, Literal, Optional, Tuple, Union

from wifimap import store as store_mod
from wifimap.walk_ui import parse_floor_input, picker_press, picker_start_cursor


KEY_CREATE = "+"
PickerSelection = Union[int, Literal["new"]]


def _active_room_id(conn: sqlite3.Connection,
                    state: "WalkState") -> Optional[int]:
    """Room id of the active spot, or None when unset/unknown."""
    if state.active_spot_id is None:
        return None
    try:
        spot = store_mod.get_spot(conn, state.active_spot_id)
    except (sqlite3.Error, OSError):
        return None
    return spot.room_id if spot is not None else None


def _find_room_by_name(
    conn: sqlite3.Connection, location_id: int, name: str,
) -> Optional[Tuple[int, int]]:
    """Return an existing room id and floor for a location and name."""
    try:
        row = conn.execute(
            "SELECT id, floor FROM rooms WHERE location_id = ? AND name = ?",
            (location_id, name),
        ).fetchone()
    except (sqlite3.Error, OSError):
        return None
    if row is None:
        return None
    return row[0], row[1]


def _fallback_pick_level(prompt: str, rows: List[Tuple[int, str]],
                         active: Optional[int],
                         create_label: str) -> Optional[PickerSelection]:
    """Show one line-based picker level and return its selection."""
    ids = [rid for rid, _ in rows]
    cursor = picker_start_cursor(ids, active)
    for i, (rid, label) in enumerate(rows):
        cur = ">" if i == cursor else " "
        mark = "*" if rid == active else " "
        print("%s%s%d. %s" % (cur, mark, i + 1, label))
    print("%s  +. <%s>" % (">" if cursor == len(rows) else " ",
                           create_label))
    try:
        raw = input("%s [Enter=active, 1-%d,+]: "
                    % (prompt, len(rows))).strip()
    except (EOFError, OSError):
        return None
    if raw == "":
        if active is not None and active in ids:
            return active
        return None
    if raw == KEY_CREATE:
        return "new"
    _, action, index = picker_press(raw, cursor, len(rows))
    if action == "cancel":
        return None
    if action == "confirm" and index is not None and index < len(rows):
        return rows[index][0]
    return None


def _fallback_pick(conn: sqlite3.Connection, state: "WalkState") -> bool:
    """Pick a room and spot within the active location."""
    if state.active_location_id is None:
        state.set_toast("no preset location: rerun with --location")
        return False
    try:
        rooms = store_mod.list_rooms(conn,
                                     location_id=state.active_location_id)
    except (sqlite3.Error, OSError) as exc:
        state.set_toast("DB error: %s" % (exc,))
        return False
    room_rows = [(room.id, "%s (floor %d%s)" % (
        room.name, room.floor, ", outdoors" if room.outdoors else ""))
        for room in rooms]
    picked_room = _fallback_pick_level(
        "room", room_rows, _active_room_id(conn, state), "new room")
    if picked_room == "new":
        picked_room, note = _fallback_create_room(conn, state)
        if picked_room is None:
            state.set_toast(note)
            return False
        state.set_toast(note)
    if not isinstance(picked_room, int):
        state.set_toast("cancelled")
        return False
    try:
        spots = store_mod.list_spots(conn, room_id=picked_room)
    except (sqlite3.Error, OSError) as exc:
        state.set_toast("DB error: %s" % (exc,))
        return False
    spot_rows = [(spot.id, spot.name) for spot in spots]
    picked_spot = _fallback_pick_level(
        "spot", spot_rows, state.active_spot_id, "new spot")
    if picked_spot == "new":
        picked_spot = _fallback_create_spot(conn, picked_room, state)
    if not isinstance(picked_spot, int):
        state.set_toast("cancelled")
        return False
    state.set_active_spot(picked_spot)
    return True


def _fallback_create_room(
    conn: sqlite3.Connection,
    state: "WalkState",
) -> Tuple[Optional[int], str]:
    """Prompt for and create or reuse a room."""
    if state.active_location_id is None:
        return None, "no preset location: rerun with --location"
    try:
        name = input("room name: ").strip()
        floor = parse_floor_input(input("floor (int): ") or "0")
        outdoors = input("outdoors? [y/N]: ").strip().lower() in (
            "y", "yes", "1")
    except (EOFError, OSError, ValueError) as exc:
        return None, "cancelled/invalid: %s" % (exc,)
    if not name:
        return None, "new room cancelled"
    try:
        room_id = store_mod.create_room(
            conn, state.active_location_id, name, floor=floor,
            outdoors=outdoors)
    except sqlite3.IntegrityError:
        existing = _find_room_by_name(
            conn, state.active_location_id, name)
        if existing is None:
            return None, "room create failed: duplicate room"
        room_id, existing_floor = existing
        return room_id, (
            "room exists (floor %d); adding spot there" % existing_floor)
    except (sqlite3.Error, OSError, ValueError) as exc:
        return None, "DB error: %s" % (exc,)
    return room_id, "room created"


def _fallback_create_spot(conn: sqlite3.Connection, room_id: int,
                          state: "WalkState") -> Optional[int]:
    """Prompt for and create a spot in the given room."""
    try:
        name = input("spot name: ").strip()
    except (EOFError, OSError) as exc:
        state.set_toast("cancelled/invalid: %s" % (exc,))
        return None
    try:
        return store_mod.create_spot(conn, room_id, name)
    except (sqlite3.Error, OSError, ValueError) as exc:
        state.set_toast("DB error: %s" % (exc,))
        return None


def _fallback_create(conn: sqlite3.Connection, state: "WalkState") -> None:
    """Create a room in the active location, then create its first spot."""
    room_id, note = _fallback_create_room(conn, state)
    if room_id is None:
        state.set_toast(note)
        return
    state.set_toast(note)
    spot_id = _fallback_create_spot(conn, room_id, state)
    if spot_id is None:
        if not state.ui_snapshot()[0]:
            state.set_toast("cancelled/invalid")
        return
    state.set_active_spot(spot_id)


def _fallback_floor(conn: sqlite3.Connection, state: "WalkState") -> None:
    """Prompt for and update the active room's floor."""
    try:
        raw = input("floor (int): ")
        floor = parse_floor_input(raw)
    except (EOFError, OSError, ValueError) as exc:
        state.set_toast(str(exc))
        return
    state.set_toast(state.set_floor(conn, floor))
