import sqlite3

import pytest

from wifimap.store import (
    add_reading,
    create_location,
    get_db,
    get_location,
    list_locations,
    list_readings,
    resolve_location,
)


@pytest.fixture
def db(tmp_path):
    conn = get_db(str(tmp_path / "test.db"))
    yield conn
    conn.close()


def test_locations_create_and_list(db):
    lid = create_location(db, "kitchen", floor=0)
    locs = list_locations(db)
    assert len(locs) == 1
    assert locs[0].id == lid
    assert locs[0].name == "kitchen"
    assert locs[0].floor == 0
    assert locs[0].outdoors is False


def test_locations_outdoors_flag(db):
    lid = create_location(db, "garden", floor=0, outdoors=True)
    locs = {loc.name: loc for loc in list_locations(db)}
    assert locs["garden"].id == lid
    assert locs["garden"].outdoors is True


def test_locations_unique_name_floor(db):
    create_location(db, "office", floor=0)
    with pytest.raises(sqlite3.IntegrityError):
        create_location(db, "office", floor=0)
    # same name, different floor is fine
    create_location(db, "office", floor=1)
    assert len(list_locations(db)) == 2


def test_reading_fk_violation(db):
    with pytest.raises(sqlite3.IntegrityError):
        add_reading(db, location_id=9999, rssi=-60)


def test_resolve_existing_name(db):
    lid = create_location(db, "living", floor=0)
    assert resolve_location(db, "living") == lid


def test_resolve_new_name_autocreates(db):
    lid = resolve_location(db, "attic", floor=1, outdoors=False)
    assert isinstance(lid, int)
    locs = {loc.name: loc for loc in list_locations(db)}
    assert locs["attic"].id == lid
    assert locs["attic"].floor == 1


def test_resolve_int_passthrough(db):
    lid = create_location(db, "hall", floor=0)
    assert resolve_location(db, lid) == lid
    with pytest.raises(ValueError):
        resolve_location(db, 9999)


def test_resolve_same_name_different_floor_creates(db):
    original = create_location(db, "office", floor=0)
    assert resolve_location(db, "office", floor=0) == original
    new_id = resolve_location(db, "office", floor=1)
    assert new_id != original
    locs = {(loc.name, loc.floor): loc for loc in list_locations(db)}
    assert locs[("office", 1)].id == new_id
    assert locs[("office", 1)].floor == 1


def test_readings_insert_join_filters_limit(db):
    a = create_location(db, "room-a", floor=0)
    b = create_location(db, "room-b", floor=1)
    r1 = add_reading(db, a, ssid="home", rssi=-50, note="first")
    r2 = add_reading(db, a, ssid="home", rssi=-55, note="second")
    r3 = add_reading(db, b, ssid="home", rssi=-70, note="third")

    assert isinstance(r1, int)

    all_rows = list_readings(db)
    # newest first
    assert [r["id"] for r in all_rows] == [r3, r2, r1]
    # join carries location columns
    assert all_rows[0]["location_name"] == "room-b"
    assert all_rows[0]["floor"] == 1

    by_name = list_readings(db, location="room-a")
    assert {r["id"] for r in by_name} == {r1, r2}

    by_id = list_readings(db, location=b)
    assert [r["id"] for r in by_id] == [r3]

    by_floor = list_readings(db, floor=0)
    assert {r["id"] for r in by_floor} == {r1, r2}

    limited = list_readings(db, limit=2)
    assert [r["id"] for r in limited] == [r3, r2]


def test_reading_ts_defaults(db):
    lid = create_location(db, "den", floor=0)
    rid = add_reading(db, lid, rssi=-60)
    rows = list_readings(db, location=lid)
    assert len(rows) == 1
    assert rows[0]["id"] == rid
    assert rows[0]["ts"]  # non-empty ISO timestamp


def test_list_readings_limit_zero(db):
    lid = create_location(db, "room", floor=0)
    add_reading(db, lid, rssi=-60)
    assert list_readings(db, limit=0) == []


def test_list_readings_negative_limit_raises(db):
    with pytest.raises(ValueError):
        list_readings(db, limit=-1)


def test_resolve_digit_string_routes_to_id(db):
    lid = create_location(db, "study", floor=0)
    assert resolve_location(db, str(lid)) == lid


def test_list_readings_location_plus_floor_filter(db):
    a0 = create_location(db, "dup", floor=0)
    a1 = create_location(db, "dup", floor=1)
    r0 = add_reading(db, a0, rssi=-50)
    r1 = add_reading(db, a1, rssi=-70)
    rows = list_readings(db, location="dup", floor=1)
    assert [r["id"] for r in rows] == [r1]
    assert rows[0]["location_id"] == a1
    assert r0 not in [r["id"] for r in rows]


def test_create_location_empty_name_raises(db):
    with pytest.raises(ValueError):
        create_location(db, "")
    with pytest.raises(ValueError):
        resolve_location(db, "")


def test_get_location_existing_and_missing(db):
    lid = create_location(db, "nook", floor=2, outdoors=True)
    loc = get_location(db, lid)
    assert loc is not None
    assert (loc.id, loc.name, loc.floor, loc.outdoors) == (lid, "nook", 2, True)
    assert get_location(db, 9999) is None


def test_update_location_floor_persists(db):
    lid = create_location(db, "attic", floor=0)
    update_location_floor(db, lid, -1)
    loc = get_location(db, lid)
    assert loc is not None and loc.floor == -1


def test_update_location_floor_unknown_raises(db):
    with pytest.raises(ValueError):
        update_location_floor(db, 9999, 1)


def test_update_location_floor_unique_conflict(db):
    a = create_location(db, "dup", floor=0)
    b = create_location(db, "dup", floor=1)
    with pytest.raises(sqlite3.IntegrityError):
        update_location_floor(db, b, 0)
    # failed update left the row untouched
    assert get_location(db, b).floor == 1
    assert get_location(db, a).floor == 0


def test_list_readings_ssid_filter(db):
    a = create_location(db, "den", floor=0)
    add_reading(db, a, ssid="home-5g", rssi=-55)
    add_reading(db, a, ssid="iot-2g", rssi=-80)
    rows = list_readings(db, ssid="home-5g")
    assert len(rows) == 1
    assert rows[0]["ssid"] == "home-5g"
    assert len(list_readings(db)) == 2


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
        assert {"locations", "readings"} <= tables
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
