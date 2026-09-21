import sqlite3

import pytest

from wifimap.store import (
    Walk,
    add_reading,
    clear_benchmark,
    create_location,
    create_room,
    create_spot,
    create_walk,
    finish_walk,
    format_benchmark_delta,
    get_benchmark,
    get_db,
    get_location,
    get_room,
    get_spot,
    get_walk,
    list_locations,
    list_readings,
    list_rooms,
    list_spots,
    list_walks,
    resolve_location,
    resolve_room,
    resolve_spot,
    set_benchmark,
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


def test_benchmark_set_get_overwrite(db):
    lid = create_location(db, "home")
    set_benchmark(db, lid, ssid="home-5g", rssi=-45, snr=32,
                  down_mbps=310.5, up_mbps=48.2)
    bench = get_benchmark(db, lid)
    assert bench is not None
    assert bench["location_id"] == lid
    assert bench["ssid"] == "home-5g"
    assert bench["rssi"] == -45
    assert bench["ts"]
    set_benchmark(db, lid, ssid="home-5g", rssi=-50, snr=30,
                  down_mbps=200.0, up_mbps=40.0)
    bench2 = get_benchmark(db, lid)
    assert bench2 is not None
    assert bench2["rssi"] == -50
    assert bench2["snr"] == 30
    rows = db.execute(
        "SELECT COUNT(*) FROM benchmarks WHERE location_id = ?",
        (lid,),
    ).fetchone()
    assert rows[0] == 1


def test_benchmark_missing_returns_none(db):
    lid = create_location(db, "home")
    assert get_benchmark(db, lid) is None
    assert get_benchmark(db, 9999) is None


def test_benchmark_clear(db):
    lid = create_location(db, "home")
    set_benchmark(db, lid, rssi=-45)
    assert get_benchmark(db, lid) is not None
    clear_benchmark(db, lid)
    assert get_benchmark(db, lid) is None


def test_benchmark_bool_id_raises(db):
    lid = create_location(db, "home")
    with pytest.raises(ValueError):
        set_benchmark(db, True, rssi=-45)
    with pytest.raises(ValueError):
        get_benchmark(db, True)
    with pytest.raises(ValueError):
        clear_benchmark(db, False)
    # valid id still works after rejected bool calls
    assert get_benchmark(db, lid) is None


def test_benchmark_unknown_id_raises(db):
    with pytest.raises(ValueError):
        set_benchmark(db, 9999, rssi=-45)


def test_benchmark_ts_default_is_iso(db):
    lid = create_location(db, "home")
    set_benchmark(db, lid, rssi=-45)
    bench = get_benchmark(db, lid)
    assert bench is not None
    assert isinstance(bench["ts"], str)
    assert "T" in bench["ts"]


def test_format_benchmark_delta_full():
    cur = {"rssi": -67, "snr": 24, "down_mbps": 48.1, "up_mbps": 10.0}
    bench = {"rssi": -45, "snr": 32, "down_mbps": 310.5, "up_mbps": 48.2}
    s = format_benchmark_delta(cur, bench)
    assert "rssi -22" in s and "snr -8" in s and "down -262.4" in s
    assert "up -38.2" in s


def test_format_benchmark_delta_null_safe():
    s = format_benchmark_delta({"rssi": -67}, {"rssi": None, "down_mbps": 100.0})
    assert s == ""


def test_walk_create_get_list_and_finish(db):
    home = create_location(db, "home")
    office = create_location(db, "office")
    first = create_walk(
        db, home, "before mesh",
        started_at="2026-09-21T08:00:00+00:00",
    )
    second = create_walk(
        db, home, "after mesh",
        started_at="2026-09-21T09:00:00+00:00",
    )
    create_walk(
        db, office, "office baseline",
        started_at="2026-09-21T10:00:00+00:00",
    )

    walk = get_walk(db, first)
    assert walk == Walk(
        id=first, location_id=home, name="before mesh",
        started_at="2026-09-21T08:00:00+00:00",
        ended_at=None,
    )
    assert [item.id for item in list_walks(db, location_id=home)] == [
        second, first]

    finish_walk(db, first, ended_at="2026-09-21T08:30:00+00:00")
    finish_walk(db, first, ended_at="2026-09-21T08:45:00+00:00")
    assert get_walk(db, first).ended_at == "2026-09-21T08:30:00+00:00"


def test_walk_api_validates_names_locations_and_ids(db):
    home = create_location(db, "home")
    with pytest.raises(ValueError, match="name"):
        create_walk(db, home, "   ")
    with pytest.raises(ValueError, match="location"):
        create_walk(db, 9999, "missing location")
    with pytest.raises(ValueError, match="walk"):
        finish_walk(db, 9999)
    with pytest.raises(ValueError, match="walk"):
        get_walk(db, True)


def test_readings_can_be_assigned_and_filtered_by_walk(db):
    home, _, home_spot = _three_levels(
        db, loc="home", room="kitchen", spot="window")
    _, _, office_spot = _three_levels(
        db, loc="office", room="desk", spot="chair")
    before = create_walk(
        db, home, "before", started_at="2026-09-21T08:00:00+00:00")
    after = create_walk(
        db, home, "after", started_at="2026-09-21T09:00:00+00:00",
    )
    finish_walk(db, after, ended_at="2026-09-21T09:30:00+00:00")

    historical = add_reading(db, home_spot, rssi=-80)
    baseline = add_reading(db, home_spot, rssi=-70, walk_id=before)
    candidate = add_reading(db, home_spot, rssi=-55, walk_id=after)

    rows = list_readings(db, walk_id=after)
    assert [row["id"] for row in rows] == [candidate]
    assert rows[0]["walk_id"] == after
    assert rows[0]["walk_name"] == "after"
    assert rows[0]["walk_started_at"] == "2026-09-21T09:00:00+00:00"
    assert rows[0]["walk_ended_at"] == "2026-09-21T09:30:00+00:00"

    by_id = {row["id"]: row for row in list_readings(db)}
    assert by_id[historical]["walk_id"] is None
    assert by_id[historical]["walk_name"] is None
    assert by_id[baseline]["walk_id"] == before

    with pytest.raises(ValueError, match="different location"):
        add_reading(db, office_spot, rssi=-60, walk_id=before)
    with pytest.raises(ValueError, match="unknown walk"):
        add_reading(db, home_spot, rssi=-60, walk_id=9999)


def test_list_readings_walk_filter_preserves_positional_limit(db):
    home, _, spot = _three_levels(db)
    walk = create_walk(db, home, "walk")
    first = add_reading(db, spot, rssi=-70, walk_id=walk)
    add_reading(db, spot, rssi=-60, walk_id=walk)

    assert len(list_readings(db, None, None, None, None, None, 1)) == 1
    assert [row["id"] for row in list_readings(db, limit=10,
                                                walk_id=walk)] == [
        first + 1, first]


def test_get_db_migrates_legacy_readings_idempotently(tmp_path):
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(str(path))
    legacy.executescript("""
        CREATE TABLE locations(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
        CREATE TABLE rooms(
          id INTEGER PRIMARY KEY,
          location_id INTEGER NOT NULL REFERENCES locations(id),
          name TEXT NOT NULL, floor INTEGER NOT NULL DEFAULT 0,
          outdoors INTEGER NOT NULL DEFAULT 0,
          UNIQUE(location_id, name, floor));
        CREATE TABLE spots(
          id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL REFERENCES rooms(id),
          name TEXT NOT NULL, UNIQUE(room_id, name));
        CREATE TABLE readings(
          id INTEGER PRIMARY KEY, ts TEXT NOT NULL,
          spot_id INTEGER NOT NULL REFERENCES spots(id),
          ssid TEXT, bssid TEXT, rssi INTEGER, noise INTEGER, snr INTEGER,
          channel TEXT, phy TEXT, tx_rate TEXT, ping_ms REAL,
          down_mbps REAL, up_mbps REAL, server TEXT, note TEXT);
        INSERT INTO locations(id, name) VALUES (1, 'home');
        INSERT INTO rooms(id, location_id, name) VALUES (1, 1, 'kitchen');
        INSERT INTO spots(id, room_id, name) VALUES (1, 1, 'window');
        INSERT INTO readings(id, ts, spot_id, rssi)
          VALUES (1, '2026-09-20T10:00:00+00:00', 1, -70);
    """)
    legacy.close()

    conn = get_db(path)
    conn.close()
    conn = get_db(path)
    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(readings)")
        }
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(readings)")
        }
        row = conn.execute(
            "SELECT id, rssi, walk_id FROM readings WHERE id = 1"
        ).fetchone()
        assert "walk_id" in columns
        assert "walks" in tables
        assert "idx_readings_walk_spot" in indexes
        assert row == (1, -70, None)
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 1
    finally:
        conn.close()
