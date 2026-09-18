import sqlite3

import pytest

from wifimap.store import (
    add_reading,
    create_location,
    get_db,
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
