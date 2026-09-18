import sqlite3

import pytest

from wifimap.store import (
    add_reading,
    create_location,
    create_room,
    create_spot,
    get_db,
    get_location,
    get_room,
    get_spot,
    list_locations,
    list_readings,
    list_rooms,
    list_spots,
    resolve_location,
    resolve_room,
    resolve_spot,
    update_room_floor,
)


@pytest.fixture
def db(tmp_path):
    conn = get_db(str(tmp_path / "test.db"))
    yield conn
    conn.close()


def _three_levels(db, loc="home", room="kitchen", spot="window",
                  floor=0, outdoors=False):
    lid = create_location(db, loc)
    rid = create_room(db, lid, room, floor=floor, outdoors=outdoors)
    sid = create_spot(db, rid, spot)
    return lid, rid, sid


def test_locations_create_and_list(db):
    lid = create_location(db, "home")
    locs = list_locations(db)
    assert len(locs) == 1
    assert locs[0].id == lid
    assert locs[0].name == "home"


def test_locations_unique_name(db):
    create_location(db, "home")
    with pytest.raises(sqlite3.IntegrityError):
        create_location(db, "home")


def test_rooms_floor_outdoors_flag(db):
    lid, rid, _ = _three_levels(db, loc="home", room="garden",
                                spot="s1", floor=0, outdoors=True)
    rooms = {r.name: r for r in list_rooms(db)}
    assert rooms["garden"].id == rid
    assert rooms["garden"].location_id == lid
    assert rooms["garden"].outdoors is True
    assert get_room(db, rid).floor == 0


def test_locations_unique_name_floor(db):
    lid, _, _ = _three_levels(db, loc="office", room="main", spot="s1")
    assert isinstance(lid, int)
    with pytest.raises(sqlite3.IntegrityError):
        create_location(db, "office")
    create_location(db, "office2")
    assert len(list_locations(db)) == 2


def test_reading_fk_violation(db):
    with pytest.raises(sqlite3.IntegrityError):
        add_reading(db, 9999, rssi=-60)


def test_resolve_existing_name(db):
    lid = create_location(db, "home")
    assert resolve_location(db, "home") == lid


def test_resolve_new_name_autocreates(db):
    lid = resolve_location(db, "attic")
    assert isinstance(lid, int)
    locs = {loc.name: loc for loc in list_locations(db)}
    assert locs["attic"].id == lid


def test_resolve_int_passthrough(db):
    lid = create_location(db, "home")
    assert resolve_location(db, lid) == lid
    with pytest.raises(ValueError):
        resolve_location(db, 9999)


def test_resolve_room_autocreates_scoped(db):
    home = create_location(db, "home")
    office = create_location(db, "office")
    r1 = resolve_room(db, home, "kitchen", floor=0)
    assert r1 == resolve_room(db, home, "kitchen", floor=0)
    r2 = resolve_room(db, home, "kitchen", floor=1)
    assert r2 != r1
    r3 = resolve_room(db, office, "kitchen", floor=0)
    assert r3 not in (r1, r2)
    with pytest.raises(ValueError):
        resolve_room(db, home, 9999)


def test_resolve_spot_autocreates_scoped(db):
    lid, rid, sid = _three_levels(db)
    assert resolve_spot(db, rid, "window") == sid
    other_room = create_room(db, lid, "hall", floor=0)
    other = resolve_spot(db, other_room, "window")
    assert other != sid
    with pytest.raises(ValueError):
        resolve_spot(db, rid, 9999)


def test_readings_insert_join_filters_limit(db):
    la, ra, sa = _three_levels(db, loc="home-a", room="room-a", spot="spot-a")
    lb, rb, sb = _three_levels(db, loc="home-b", room="room-b", spot="spot-b",
                               floor=1)
    r1 = add_reading(db, sa, ssid="home", rssi=-50, note="first")
    r2 = add_reading(db, sa, ssid="home", rssi=-55, note="second")
    r3 = add_reading(db, sb, ssid="home", rssi=-70, note="third")

    assert isinstance(r1, int)

    all_rows = list_readings(db)
    # newest first
    assert [r["id"] for r in all_rows] == [r3, r2, r1]
    # join carries all three levels
    assert all_rows[0]["location_name"] == "home-b"
    assert all_rows[0]["room_name"] == "room-b"
    assert all_rows[0]["spot_name"] == "spot-b"
    assert all_rows[0]["floor"] == 1

    by_name = list_readings(db, location="home-a")
    assert {r["id"] for r in by_name} == {r1, r2}

    by_id = list_readings(db, location=lb)
    assert [r["id"] for r in by_id] == [r3]

    by_room = list_readings(db, room="room-a")
    assert {r["id"] for r in by_room} == {r1, r2}

    by_spot = list_readings(db, spot="spot-b")
    assert [r["id"] for r in by_spot] == [r3]

    by_floor = list_readings(db, floor=0)
    assert {r["id"] for r in by_floor} == {r1, r2}

    limited = list_readings(db, limit=2)
    assert [r["id"] for r in limited] == [r3, r2]


def test_reading_ts_defaults(db):
    _, _, sid = _three_levels(db, loc="home", room="den", spot="desk")
    rid = add_reading(db, sid, rssi=-60)
    rows = list_readings(db, spot=sid)
    assert len(rows) == 1
    assert rows[0]["id"] == rid
    assert rows[0]["ts"]  # non-empty ISO timestamp


def test_list_readings_limit_zero(db):
    _, _, sid = _three_levels(db, loc="home", room="room", spot="s")
    add_reading(db, sid, rssi=-60)
    assert list_readings(db, limit=0) == []


def test_list_readings_negative_limit_raises(db):
    with pytest.raises(ValueError):
        list_readings(db, limit=-1)


def test_resolve_digit_string_routes_to_id(db):
    lid = create_location(db, "home")
    assert resolve_location(db, str(lid)) == lid
    _, rid, sid = _three_levels(db, loc="home2", room="study", spot="desk")
    home2 = resolve_location(db, "home2")
    assert resolve_room(db, home2, str(rid)) == rid
    assert resolve_spot(db, rid, str(sid)) == sid


def test_list_readings_location_plus_floor_filter(db):
    lid = create_location(db, "dup")
    r0 = create_room(db, lid, "room", floor=0)
    r1 = create_room(db, lid, "room", floor=1)
    s0 = create_spot(db, r0, "s0")
    s1 = create_spot(db, r1, "s1")
    rid0 = add_reading(db, s0, rssi=-50)
    rid1 = add_reading(db, s1, rssi=-70)
    rows = list_readings(db, location="dup", floor=1)
    assert [r["id"] for r in rows] == [rid1]
    assert rows[0]["location_id"] == lid
    assert rows[0]["room_id"] == r1
    assert rid0 not in [r["id"] for r in rows]


def test_create_location_empty_name_raises(db):
    with pytest.raises(ValueError):
        create_location(db, "")
    with pytest.raises(ValueError):
        resolve_location(db, "")


def test_get_location_existing_and_missing(db):
    lid = create_location(db, "home")
    loc = get_location(db, lid)
    assert loc is not None
    assert (loc.id, loc.name) == (lid, "home")
    assert get_location(db, 9999) is None


def test_get_room_spot_existing_and_missing(db):
    _, rid, sid = _three_levels(db)
    assert get_room(db, rid) is not None
    assert get_room(db, 9999) is None
    assert get_spot(db, sid) is not None
    assert get_spot(db, 9999) is None
    assert list_spots(db, room_id=rid)[0].id == sid


def test_update_room_floor_persists(db):
    _, rid, _ = _three_levels(db, loc="home", room="attic", spot="s")
    update_room_floor(db, rid, -1)
    room = get_room(db, rid)
    assert room is not None and room.floor == -1


def test_update_room_floor_unknown_raises(db):
    with pytest.raises(ValueError):
        update_room_floor(db, 9999, 1)


def test_update_room_floor_unique_conflict(db):
    lid = create_location(db, "home")
    a = create_room(db, lid, "dup", floor=0)
    b = create_room(db, lid, "dup", floor=1)
    with pytest.raises(sqlite3.IntegrityError):
        update_room_floor(db, b, 0)
    # failed update left the row untouched
    assert get_room(db, b).floor == 1
    assert get_room(db, a).floor == 0


def test_list_readings_ssid_filter(db):
    _, _, sid = _three_levels(db, loc="home", room="den", spot="desk")
    add_reading(db, sid, ssid="home-5g", rssi=-55)
    add_reading(db, sid, ssid="iot-2g", rssi=-80)
    rows = list_readings(db, ssid="home-5g")
    assert len(rows) == 1
    assert rows[0]["ssid"] == "home-5g"
    assert len(list_readings(db)) == 2


def test_list_readings_room_spot_id_filters(db):
    _, rid, sid = _three_levels(db)
    r1 = add_reading(db, sid, rssi=-50)
    assert list_readings(db, room=rid)[0]["id"] == r1
    assert list_readings(db, spot=sid)[0]["id"] == r1
    assert list_readings(db, room=str(rid))[0]["id"] == r1
    assert list_readings(db, spot=str(sid))[0]["id"] == r1
    assert list_readings(db, location="home", room="kitchen",
                          spot="window")[0]["id"] == r1


def test_get_db_enables_wal_and_fk(tmp_path):
    path = str(tmp_path / "pragma.db")
    conn = get_db(path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
        # schema init is idempotent
        get_db(path).close()
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"locations", "rooms", "spots", "readings"} <= tables
    finally:
        conn.close()


def test_new_schema_tables(tmp_path):
    from wifimap.store import get_db
    conn = get_db(str(tmp_path / "s.db"))
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"locations", "rooms", "spots", "readings"} <= tables
    finally:
        conn.close()


def test_location_create_unique(db):
    from wifimap.store import create_location
    import sqlite3
    lid = create_location(db, "home")
    assert isinstance(lid, int)
    with pytest.raises(sqlite3.IntegrityError):
        create_location(db, "home")


def test_room_spot_scoped_unique(db):
    from wifimap.store import create_location, create_room, create_spot
    import sqlite3
    home = create_location(db, "home")
    office = create_location(db, "office")
    k = create_room(db, home, "kitchen", floor=0)
    with pytest.raises(sqlite3.IntegrityError):
        create_room(db, home, "kitchen", floor=0)
    create_room(db, home, "kitchen", floor=1)
    create_room(db, office, "kitchen", floor=0)
    w = create_spot(db, k, "window")
    with pytest.raises(sqlite3.IntegrityError):
        create_spot(db, k, "window")


def test_readings_join_all_levels(db):
    from wifimap.store import (create_location, create_room, create_spot,
                               add_reading, list_readings)
    home = create_location(db, "home")
    k = create_room(db, home, "kitchen", floor=0)
    w = create_spot(db, k, "window")
    r1 = add_reading(db, w, rssi=-50, ssid="home")
    rows = list_readings(db)
    assert rows[0]["spot_name"] == "window"
    assert rows[0]["room_name"] == "kitchen"
    assert rows[0]["location_name"] == "home"
    assert rows[0]["spot_id"] == w
    assert list_readings(db, location="home")[0]["id"] == r1
    assert list_readings(db, room="kitchen")[0]["id"] == r1
    assert list_readings(db, spot="window")[0]["id"] == r1
    assert list_readings(db, location="home", floor=0)[0]["id"] == r1
