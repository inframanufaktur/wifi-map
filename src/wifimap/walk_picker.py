"""Line-based room and spot picker workflows for walk mode."""
from __future__ import annotations

import sqlite3
from typing import List, Literal, Optional, Tuple, Union

from wifimap import store as store_mod
from wifimap.walk_ui import parse_floor_input, picker_press, picker_start_cursor


KEY_CREATE = "+"
KEY_ENTER_CODES = (10, 13)
KEY_ESC = 27
PICKER_TIMEOUT_RESTORE_MS = 1000
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


def _list_picker_curses(
    stdscr: object,
    title: str,
    rows: List[Tuple[int, str]],
    active: Optional[int],
    create_label: str,
    poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS,
) -> Optional[PickerSelection]:
    """Show a blocking numbered curses picker prefilled with active."""
    import curses

    ids = [rid for rid, _ in rows]
    cursor = picker_start_cursor(ids, active)
    stdscr.timeout(-1)  # type: ignore[attr-defined]
    try:
        while True:
            height, width = stdscr.getmaxyx()  # type: ignore[attr-defined]
            lines = ["%s (Enter=active, 1-%d, + new, q cancel):"
                     % (title, len(rows))]
            for index, (row_id, label) in enumerate(rows):
                current = ">" if index == cursor else " "
                selected = "*" if row_id == active else " "
                lines.append("%s%s%d. %s" % (
                    current, selected, index + 1, label))
            lines.append("%s  +. <%s>" % (
                ">" if cursor == len(rows) else " ", create_label))
            stdscr.clear()  # type: ignore[attr-defined]
            for row_number, line in enumerate(lines[:height - 1]):
                try:
                    stdscr.addstr(  # type: ignore[attr-defined]
                        row_number, 0, line[:width - 1])
                except Exception:
                    pass
            stdscr.refresh()  # type: ignore[attr-defined]
            ch = stdscr.getch()  # type: ignore[attr-defined]
            if ch == curses.KEY_UP:
                key = "up"
            elif ch == curses.KEY_DOWN:
                key = "down"
            elif ch in KEY_ENTER_CODES + (curses.KEY_ENTER,):
                key = "\n"
            elif ch == KEY_ESC:
                key = "\x1b"
            else:
                try:
                    key = chr(ch)
                except (ValueError, OverflowError):
                    continue
            cursor, action, index = picker_press(key, cursor, len(rows))
            if action == "cancel":
                return None
            if action == "confirm" and index is not None:
                if index == len(rows):
                    return "new"
                return rows[index][0]
    finally:
        stdscr.timeout(poll_timeout_ms)  # type: ignore[attr-defined]


def _room_picker_curses(
    stdscr: object,
    conn: sqlite3.Connection,
    location_id: int,
    active_room: Optional[int],
    poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS,
) -> Optional[PickerSelection]:
    """Pick or create a room within a location."""
    rooms = store_mod.list_rooms(conn, location_id=location_id)
    rows = [(room.id, "%s (floor %d%s)" % (
        room.name, room.floor, ", outdoors" if room.outdoors else ""))
        for room in rooms]
    return _list_picker_curses(
        stdscr, "Pick room", rows, active_room, "new room", poll_timeout_ms)


def _spot_picker_curses(
    stdscr: object,
    conn: sqlite3.Connection,
    room_id: int,
    active_spot: Optional[int],
    poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS,
) -> Optional[PickerSelection]:
    """Pick or create a spot within a room."""
    spots = store_mod.list_spots(conn, room_id=room_id)
    rows = [(spot.id, spot.name) for spot in spots]
    return _list_picker_curses(
        stdscr, "Pick spot", rows, active_spot, "new spot", poll_timeout_ms)


def _pick_room_spot_curses(
    stdscr: object,
    conn: sqlite3.Connection,
    state: "WalkState",
    poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS,
) -> bool:
    """Pick a room and spot within the active location."""
    if state.active_location_id is None:
        state.set_toast("no preset location: rerun with --location")
        return False
    active_room = _active_room_id(conn, state)
    try:
        picked_room = _room_picker_curses(
            stdscr, conn, state.active_location_id, active_room,
            poll_timeout_ms)
    except (sqlite3.Error, OSError) as exc:
        state.set_toast("DB error: %s" % (exc,))
        return False
    if picked_room == "new":
        picked_room, note = _create_room_curses(
            stdscr, conn, state.active_location_id, poll_timeout_ms)
        state.set_toast(note)
    if not isinstance(picked_room, int):
        return False
    try:
        picked_spot = _spot_picker_curses(
            stdscr, conn, picked_room, state.active_spot_id,
            poll_timeout_ms)
    except (sqlite3.Error, OSError) as exc:
        state.set_toast("DB error: %s" % (exc,))
        return False
    if picked_spot == "new":
        picked_spot = _create_spot_curses(
            stdscr, conn, picked_room, poll_timeout_ms)
    if not isinstance(picked_spot, int):
        state.set_toast("spot pick cancelled")
        return False
    state.set_active_spot(picked_spot)
    return True


def _prompt_curses(
    stdscr: object,
    prompt: str,
    poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS,
) -> str:
    """Read one line in blocking curses mode and restore poll timing."""
    import curses

    height, width = stdscr.getmaxyx()  # type: ignore[attr-defined]
    stdscr.timeout(-1)  # type: ignore[attr-defined]
    try:
        curses.echo()  # type: ignore[attr-defined]
        stdscr.addstr(  # type: ignore[attr-defined]
            height - 1, 0, prompt[:width - 1])
        stdscr.clrtoeol()  # type: ignore[attr-defined]
        stdscr.refresh()  # type: ignore[attr-defined]
        raw = stdscr.getstr(  # type: ignore[attr-defined]
            height - 1, min(len(prompt), width - 1))
        return raw.decode("utf-8", "replace")
    except Exception:
        return ""
    finally:
        try:
            curses.noecho()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            stdscr.timeout(poll_timeout_ms)  # type: ignore[attr-defined]
        except Exception:
            pass


def _create_room_curses(
    stdscr: object,
    conn: sqlite3.Connection,
    location_id: int,
    poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS,
) -> Tuple[Optional[int], str]:
    """Prompt for and create or reuse a room."""
    name = _prompt_curses(stdscr, "room name: ", poll_timeout_ms).strip()
    if not name:
        return None, "new room cancelled"
    floor_input = _prompt_curses(
        stdscr, "floor (int): ", poll_timeout_ms).strip()
    try:
        floor = parse_floor_input(floor_input or "0")
    except ValueError as exc:
        return None, "cancelled/invalid: %s" % (exc,)
    outdoors_input = _prompt_curses(
        stdscr, "outdoors? [y/N]: ", poll_timeout_ms).strip().lower()
    outdoors = outdoors_input in ("y", "yes", "1")
    try:
        room_id = store_mod.create_room(
            conn, location_id, name, floor=floor, outdoors=outdoors)
    except sqlite3.IntegrityError:
        existing = _find_room_by_name(conn, location_id, name)
        if existing is None:
            return None, "room create failed: duplicate room"
        room_id, existing_floor = existing
        return room_id, (
            "room exists (floor %d); adding spot there" % existing_floor)
    except (sqlite3.Error, OSError, ValueError) as exc:
        return None, "room create failed: %s" % (exc,)
    return room_id, "room created"


def _create_spot_curses(
    stdscr: object,
    conn: sqlite3.Connection,
    room_id: int,
    poll_timeout_ms: int = PICKER_TIMEOUT_RESTORE_MS,
) -> Optional[int]:
    """Prompt for and create a spot within a room."""
    name = _prompt_curses(stdscr, "spot name: ", poll_timeout_ms).strip()
    if not name:
        return None
    try:
        return store_mod.create_spot(conn, room_id, name)
    except (sqlite3.Error, OSError, ValueError):
        return None


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
