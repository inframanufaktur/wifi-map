# Location > Room > Spot Implementation Plan (rev2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split room-level `locations` table into `locations` (site) + `rooms` (floor/outdoors) + `spots` (window/bed/corner), fresh break, wire CLI/TUI/tests.

**Architecture:** Three normalized tables: `locations(id,name UNIQUE)`, `rooms(location_id FK,name,floor,outdoors, UNIQUE(location_id,name,floor))`, `spots(room_id FK,name, UNIQUE(room_id,name))`, `readings.spot_id FK`. Location resolved first, room inside it, spot inside room.

**Tech Stack:** Python 3.9 stdlib, sqlite3, argparse, pytest.

---

## File map

- Modify: `src/wifimap/store.py` — DDL, `Location`/`Room`/`Spot`, all CRUD, `list_readings` 3-join.
- Modify: `src/wifimap/cli.py` — `rooms` + `spots` subcommands, `scan --location --room --spot`, `list/export --location --room --spot`.
- Modify: `src/wifimap/tui.py` — `WalkState.active_location_id/active_spot_id`, room->spot drilldown picker scoped to location.
- Modify: `tests/test_store.py`, `tests/test_cli.py`, `tests/test_tui.py`.
- Modify: `README.md`.

### Task 1: store DDL + dataclasses

**Files:**
- Modify: `src/wifimap/store.py:10-58`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing test**

```python
def test_new_schema_tables(tmp_path):
    from wifimap.store import get_db
    conn = get_db(str(tmp_path / "s.db"))
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"locations", "rooms", "spots", "readings"} <= tables
    finally:
        conn.close()
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `python3 -m pytest tests/test_store.py::test_new_schema_tables -v`
Expected: FAIL (no `rooms`/`spots` tables).

- [ ] **Step 3: Replace DDL + dataclasses**

```python
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
```

Fresh break — no migration.

- [ ] **Step 4: Run test, verify PASS**

Run: `python3 -m pytest tests/test_store.py::test_new_schema_tables -v`
Expected: PASS (other tests fail until Task 2-3).

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/store.py tests/test_store.py
git commit -m "feat(store): location room spot schema"
```

### Task 2: location + room + spot helpers

**Files:**
- Modify: `src/wifimap/store.py:70-177`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python3 -m pytest tests/test_store.py::test_location_create_unique tests/test_store.py::test_room_spot_scoped_unique -v`
Expected: FAIL (functions missing).

- [ ] **Step 3: Implement helpers**

```python
def create_location(conn, name: str) -> int:
    if not name:
        raise ValueError("location name must not be empty")
    cur = conn.execute("INSERT INTO locations(name) VALUES (?)", (name,))
    conn.commit()
    row = cur.lastrowid
    if row is None:
        raise sqlite3.Error("location insert returned no row id")
    return row

def list_locations(conn):
    rows = conn.execute("SELECT id, name FROM locations ORDER BY id").fetchall()
    return [Location(id=r[0], name=r[1]) for r in rows]

def get_location(conn, location_id: int):
    if isinstance(location_id, bool):
        raise ValueError("invalid location id: %r" % (location_id,))
    row = conn.execute(
        "SELECT id, name FROM locations WHERE id = ?", (location_id,)).fetchone()
    return None if row is None else Location(id=row[0], name=row[1])

def resolve_location(conn, id_or_name) -> int:
    if isinstance(id_or_name, bool):
        raise ValueError("invalid location: %r" % (id_or_name,))
    if isinstance(id_or_name, int):
        row = conn.execute(
            "SELECT id FROM locations WHERE id = ?", (id_or_name,)).fetchone()
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
            "SELECT id FROM locations WHERE id = ?", (as_id,)).fetchone()
        if row is not None:
            return row[0]
    row = conn.execute(
        "SELECT id FROM locations WHERE name = ?", (name,)).fetchone()
    if row is not None:
        return row[0]
    return create_location(conn, name)

def create_room(conn, location_id: int, name: str, floor: int = 0, outdoors: bool = False) -> int:
    if not name:
        raise ValueError("room name must not be empty")
    if get_location(conn, location_id) is None:
        raise ValueError("unknown location id: %r" % (location_id,))
    cur = conn.execute(
        "INSERT INTO rooms(location_id, name, floor, outdoors) VALUES (?, ?, ?, ?)",
        (location_id, name, floor, 1 if outdoors else 0))
    conn.commit()
    row = cur.lastrowid
    if row is None:
        raise sqlite3.Error("room insert returned no row id")
    return row

def list_rooms(conn, location_id=None):
    if location_id is None:
        rows = conn.execute(
            "SELECT id, location_id, name, floor, outdoors FROM rooms ORDER BY id").fetchall()
    else:
        rows = conn.execute(
            "SELECT id, location_id, name, floor, outdoors FROM rooms WHERE location_id = ? ORDER BY id",
            (location_id,)).fetchall()
    return [Room(id=r[0], location_id=r[1], name=r[2], floor=r[3], outdoors=bool(r[4])) for r in rows]

def get_room(conn, room_id: int):
    if isinstance(room_id, bool):
        raise ValueError("invalid room id: %r" % (room_id,))
    row = conn.execute(
        "SELECT id, location_id, name, floor, outdoors FROM rooms WHERE id = ?",
        (room_id,)).fetchone()
    if row is None:
        return None
    return Room(id=row[0], location_id=row[1], name=row[2], floor=row[3], outdoors=bool(row[4]))

def resolve_room(conn, location_id: int, name: str, floor: int = 0, outdoors: bool = False) -> int:
    if isinstance(name, bool) or not str(name):
        raise ValueError("invalid room: %r" % (name,))
    s = str(name)
    try:
        as_id = int(s)
    except ValueError:
        as_id = None
    if as_id is not None:
        row = conn.execute(
            "SELECT id FROM rooms WHERE id = ? AND location_id = ?",
            (as_id, location_id)).fetchone()
        if row is not None:
            return row[0]
    row = conn.execute(
        "SELECT id FROM rooms WHERE location_id = ? AND name = ? AND floor = ?",
        (location_id, s, floor)).fetchone()
    if row is not None:
        return row[0]
    return create_room(conn, location_id, s, floor=floor, outdoors=outdoors)

def update_room_floor(conn, room_id: int, floor: int) -> None:
    if isinstance(room_id, bool):
        raise ValueError("invalid room id: %r" % (room_id,))
    cur = conn.execute("UPDATE rooms SET floor = ? WHERE id = ?", (floor, room_id))
    conn.commit()
    if cur.rowcount == 0:
        raise ValueError("unknown room id: %r" % (room_id,))

def create_spot(conn, room_id: int, name: str) -> int:
    if not name:
        raise ValueError("spot name must not be empty")
    if get_room(conn, room_id) is None:
        raise ValueError("unknown room id: %r" % (room_id,))
    cur = conn.execute(
        "INSERT INTO spots(room_id, name) VALUES (?, ?)", (room_id, name))
    conn.commit()
    row = cur.lastrowid
    if row is None:
        raise sqlite3.Error("spot insert returned no row id")
    return row

def list_spots(conn, room_id=None):
    if room_id is None:
        rows = conn.execute("SELECT id, room_id, name FROM spots ORDER BY id").fetchall()
    else:
        rows = conn.execute(
            "SELECT id, room_id, name FROM spots WHERE room_id = ? ORDER BY id",
            (room_id,)).fetchall()
    return [Spot(id=r[0], room_id=r[1], name=r[2]) for r in rows]

def get_spot(conn, spot_id: int):
    if isinstance(spot_id, bool):
        raise ValueError("invalid spot id: %r" % (spot_id,))
    row = conn.execute(
        "SELECT id, room_id, name FROM spots WHERE id = ?", (spot_id,)).fetchone()
    if row is None:
        return None
    return Spot(id=row[0], room_id=row[1], name=row[2])

def resolve_spot(conn, room_id: int, name: str) -> int:
    if isinstance(name, bool) or not str(name):
        raise ValueError("invalid spot: %r" % (name,))
    s = str(name)
    try:
        as_id = int(s)
    except ValueError:
        as_id = None
    if as_id is not None:
        row = conn.execute(
            "SELECT id FROM spots WHERE id = ? AND room_id = ?",
            (as_id, room_id)).fetchone()
        if row is not None:
            return row[0]
    row = conn.execute(
        "SELECT id FROM spots WHERE room_id = ? AND name = ?",
        (room_id, s)).fetchone()
    if row is not None:
        return row[0]
    return create_spot(conn, room_id, s)
```

Delete `update_location_floor`.

- [ ] **Step 4: Run, verify PASS**

Run: `python3 -m pytest tests/test_store.py::test_location_create_unique tests/test_store.py::test_room_spot_scoped_unique tests/test_store.py::test_new_schema_tables -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/store.py tests/test_store.py
git commit -m "feat(store): location room spot helpers"
```

### Task 3: readings on spot_id + 3-join list

**Files:**
- Modify: `src/wifimap/store.py:179-277`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python3 -m pytest tests/test_store.py::test_readings_join_all_levels -v`
Expected: FAIL (`add_reading` still takes `location_id`).

- [ ] **Step 3: Implement**

`add_reading(conn, spot_id, ...)` inserts `spot_id`.
`list_readings(conn, location?, room?, spot?, floor?, ssid?, limit?)`:
```sql
SELECT r.id, r.ts, r.spot_id, r.ssid, ...,
 s.name AS spot_name, s.room_id AS room_id,
 m.name AS room_name, m.location_id AS location_id,
 m.floor AS floor, m.outdoors AS outdoors,
 l.name AS location_name
 FROM readings r JOIN spots s ON r.spot_id = s.id
 JOIN rooms m ON s.room_id = m.id
 JOIN locations l ON m.location_id = l.id
```
Filters: location→`m.location_id/l.name`, room→`s.room_id/m.name`,
spot→`r.spot_id/s.name`, floor→`m.floor`, ssid→`r.ssid`.
Numeric-string IDs route to id OR name (same pattern as existing code).

- [ ] **Step 4: Rewrite old store tests to 3 levels**

Replace `create_location(db,"x",floor=)` with
`lid=create_location(db,"home"); rid=create_room(db,lid,"x",floor=); create_spot(...)`,
`add_reading(db, location_id)` with spot id.

- [ ] **Step 5: Full store suite green**

Run: `python3 -m pytest tests/test_store.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wifimap/store.py tests/test_store.py
git commit -m "feat(store): readings on spot with 3-level join"
```

### Task 4: CLI rooms + spots + scan

**Files:**
- Modify: `src/wifimap/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

```python
def test_scan_location_room_spot(monkeypatch, tmp_path):
    from wifimap import signal as signal_mod, store as store_mod
    from wifimap.cli import main
    db = str(tmp_path / "cli.db")
    monkeypatch.setattr(signal_mod, "read_signal",
        lambda timeout=2.0: signal_mod.Signal(ssid="h", rssi=-55))
    rc = main(["--db", db, "scan", "--location", "home",
               "--room", "kitchen", "--spot", "window", "--no-speedtest"])
    assert rc == 0
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert rows[0]["location_name"] == "home"
        assert rows[0]["room_name"] == "kitchen"
        assert rows[0]["spot_name"] == "window"
    finally:
        conn.close()
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python3 -m pytest tests/test_cli.py::test_scan_location_room_spot -v`
Expected: FAIL (no `--room/--spot` flags).

- [ ] **Step 3: Implement parser + scan + rooms/spots subcommands**

- scan: `--location REQ --room REQ --spot REQ --room-floor default=0 --room-outdoors choices(0,1) default=0`.
- `rooms list --location REQ | add --location REQ --name REQ --floor --outdoors`.
- `spots list --location REQ --room REQ | add --location REQ --room REQ --name REQ`.
- `locations add`: only `--name`.
- Scan body: resolve location → resolve_room → resolve_spot → add_reading(spot_id).

- [ ] **Step 4: Update old CLI tests** (`--location X` → `--location home --room X --spot window`).

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/cli.py tests/test_cli.py
git commit -m "feat(cli): rooms spots commands and scan 3-level"
```

### Task 5: CLI list/export filters + TUI drilldown

**Files:**
- Modify: `src/wifimap/cli.py`, `src/wifimap/tui.py`
- Test: `tests/test_cli.py`, `tests/test_tui.py`

- [ ] **Step 1: Write failing filter test**

```python
def test_list_filter_room_spot(tmp_path):
    from wifimap.store import get_db, create_location, create_room, create_spot, add_reading, list_readings
    conn = get_db(str(tmp_path / "f.db"))
    try:
        home = create_location(conn, "home")
        k = create_room(conn, home, "kitchen")
        w = create_spot(conn, k, "window")
        b = create_spot(conn, k, "bed")
        add_reading(conn, w, rssi=-50)
        add_reading(conn, b, rssi=-70)
        assert len(list_readings(conn, spot="window")) == 1
        assert len(list_readings(conn, room="kitchen")) == 2
    finally:
        conn.close()
```

- [ ] **Step 2: Implement list/export `--room --spot` + export fields**

```python
_EXPORT_FIELDS = [
    "id", "ts", "spot_id", "room_id", "location_id",
    "location_name", "room_name", "spot_name",
    "floor", "outdoors",
    "ssid", "bssid", "rssi", "noise", "snr", "channel", "phy",
    "tx_rate", "ping_ms", "down_mbps", "up_mbps", "server", "note",
]
```

- [ ] **Step 3: TUI drilldown** — `WalkState.active_location_id/active_spot_id`;
  `run_walk(location_preset)` resolves location; room picker over
  `list_rooms(location_id)`, spot picker over `list_spots(room_id)`;
  create room (name/floor/outdoors) or spot (name); `set_floor` →
  `update_room_floor`; label `#spot location/room/spot (floor N)`.

- [ ] **Step 4: Run suites**

Run: `python3 -m pytest tests/test_cli.py tests/test_tui.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/cli.py src/wifimap/tui.py tests/test_cli.py tests/test_tui.py
git commit -m "feat: spot filter and walk room-spot drilldown"
```

### Task 6: README + full green

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README commands**

```
- `wifimap scan --location HOME --room KITCHEN --spot WINDOW ...`
- `wifimap locations list|add` — sites
- `wifimap rooms list|add --location HOME ...`
- `wifimap spots list|add --location HOME --room KITCHEN ...`
- `wifimap walk [--location HOME]` — preset site, drill room -> spot
- `wifimap list/export [--location HOME] [--room K] [--spot WINDOW] ...`
```

- [ ] **Step 2: Run full suite**

Run: `python3 -m pytest -v`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: location room spot commands"
```

## Self-review
- Spec coverage: bare locations ✓, rooms floor/outdoors ✓, spots bare ✓,
  readings on spot ✓, CLI rooms/spots/scan ✓, list/export ✓,
  walk preset+drilldown ✓, fresh break ✓, tests ✓.
- No placeholders. Type/field names (`room_id`, `room_name`, `spot_name`)
  consistent across tasks.
