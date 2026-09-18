# Location > Spot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split room-level `locations` table into bare `locations` (physical site) plus `spots` (room/place), fresh break, wire CLI/TUI/tests.

**Architecture:** Normalized two tables: `locations(id,name UNIQUE)`, `spots(location_id FK,name,floor,outdoors, UNIQUE(location_id,name,floor))`, `readings.spot_id FK`. Location resolved first, spot scoped inside it.

**Tech Stack:** Python 3.9 stdlib, sqlite3, argparse, pytest.

---

## File map

- Modify: `src/wifimap/store.py` — DDL, `Location`/`Spot` dataclasses, all CRUD, `list_readings` join.
- Modify: `src/wifimap/cli.py` — `spots list|add`, `scan --location --spot`, `list/export --location --spot`, export fields.
- Modify: `src/wifimap/tui.py` — `WalkState.active_location_id/active_spot_id`, picker over `list_spots()`, `set_floor` targets spot.
- Modify: `tests/test_store.py` — full rewrite for two levels.
- Modify: `tests/test_cli.py` — scan/spots/list/export scoping.
- Modify: `tests/test_tui.py` — picker/floor scoping (spot-level).
- Modify: `README.md` — commands + model paragraph.

### Task 1: store DDL + dataclasses

**Files:**
- Modify: `src/wifimap/store.py:10-58`
- Test: `tests/test_store.py:197-215`

- [ ] **Step 1: Write failing test for new tables**

```python
def test_new_schema_tables(tmp_path):
    from wifimap.store import get_db
    conn = get_db(str(tmp_path / "s.db"))
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"locations", "spots", "readings"} <= tables
    finally:
        conn.close()
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `pytest tests/test_store.py::test_new_schema_tables -v`
Expected: FAIL (no `spots` table, old `locations` has floor columns).

- [ ] **Step 3: Replace DDL + dataclasses**

```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS locations(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS spots(
  id INTEGER PRIMARY KEY,
  location_id INTEGER NOT NULL REFERENCES locations(id),
  name TEXT NOT NULL,
  floor INTEGER NOT NULL DEFAULT 0,
  outdoors INTEGER NOT NULL DEFAULT 0 CHECK(outdoors IN (0, 1)),
  UNIQUE(location_id, name, floor)
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
CREATE INDEX IF NOT EXISTS idx_spots_location ON spots(location_id);
CREATE INDEX IF NOT EXISTS idx_readings_spot ON readings(spot_id);
CREATE INDEX IF NOT EXISTS idx_readings_ssid ON readings(ssid);
"""

@dataclass
class Location:
    id: int
    name: str

@dataclass
class Spot:
    id: int
    location_id: int
    name: str
    floor: int = 0
    outdoors: bool = False
```

Note: fresh break — no migration, old DBs start clean.

- [ ] **Step 4: Run test, verify PASS**

Run: `pytest tests/test_store.py::test_new_schema_tables -v`
Expected: PASS (other tests fail, expected until Task 2-4).

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/store.py tests/test_store.py
git commit -m "feat(store): location > spot schema"
```

### Task 2: location + spot helpers

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

def test_spot_scoped_unique(db):
    from wifimap.store import create_location, create_spot
    import sqlite3
    home = create_location(db, "home")
    office = create_location(db, "office")
    create_spot(db, home, "kitchen", floor=0)
    with pytest.raises(sqlite3.IntegrityError):
        create_spot(db, home, "kitchen", floor=0)
    # same name different floor ok, same name different location ok
    create_spot(db, home, "kitchen", floor=1)
    create_spot(db, office, "kitchen", floor=0)
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_store.py::test_location_create_unique tests/test_store.py::test_spot_scoped_unique -v`
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

def create_spot(conn, location_id: int, name: str, floor: int = 0, outdoors: bool = False) -> int:
    if not name:
        raise ValueError("spot name must not be empty")
    if get_location(conn, location_id) is None:
        raise ValueError("unknown location id: %r" % (location_id,))
    cur = conn.execute(
        "INSERT INTO spots(location_id, name, floor, outdoors) VALUES (?, ?, ?, ?)",
        (location_id, name, floor, 1 if outdoors else 0))
    conn.commit()
    row = cur.lastrowid
    if row is None:
        raise sqlite3.Error("spot insert returned no row id")
    return row

def list_spots(conn, location_id=None):
    if location_id is None:
        rows = conn.execute(
            "SELECT id, location_id, name, floor, outdoors FROM spots ORDER BY id").fetchall()
    else:
        rows = conn.execute(
            "SELECT id, location_id, name, floor, outdoors FROM spots WHERE location_id = ? ORDER BY id",
            (location_id,)).fetchall()
    return [Spot(id=r[0], location_id=r[1], name=r[2], floor=r[3], outdoors=bool(r[4])) for r in rows]

def get_spot(conn, spot_id: int):
    if isinstance(spot_id, bool):
        raise ValueError("invalid spot id: %r" % (spot_id,))
    row = conn.execute(
        "SELECT id, location_id, name, floor, outdoors FROM spots WHERE id = ?",
        (spot_id,)).fetchone()
    if row is None:
        return None
    return Spot(id=row[0], location_id=row[1], name=row[2], floor=row[3], outdoors=bool(row[4]))

def resolve_spot(conn, location_id: int, name: str, floor: int = 0, outdoors: bool = False) -> int:
    if isinstance(name, bool) or not str(name):
        raise ValueError("invalid spot: %r" % (name,))
    s = str(name)
    try:
        as_id = int(s)
    except ValueError:
        as_id = None
    if as_id is not None:
        row = conn.execute(
            "SELECT id FROM spots WHERE id = ? AND location_id = ?",
            (as_id, location_id)).fetchone()
        if row is not None:
            return row[0]
    row = conn.execute(
        "SELECT id FROM spots WHERE location_id = ? AND name = ? AND floor = ?",
        (location_id, s, floor)).fetchone()
    if row is not None:
        return row[0]
    return create_spot(conn, location_id, s, floor=floor, outdoors=outdoors)

def update_spot_floor(conn, spot_id: int, floor: int) -> None:
    if isinstance(spot_id, bool):
        raise ValueError("invalid spot id: %r" % (spot_id,))
    cur = conn.execute("UPDATE spots SET floor = ? WHERE id = ?", (floor, spot_id))
    conn.commit()
    if cur.rowcount == 0:
        raise ValueError("unknown spot id: %r" % (spot_id,))
```

Keep old names (`create_location` with floor kw) deleted; update callers in later tasks.

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_store.py::test_location_create_unique tests/test_store.py::test_spot_scoped_unique tests/test_store.py::test_new_schema_tables -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/store.py tests/test_store.py
git commit -m "feat(store): location and spot helpers"
```

### Task 3: readings on spot_id + filtered list

**Files:**
- Modify: `src/wifimap/store.py:179-277`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing test**

```python
def test_readings_join_spot_location(db):
    from wifimap.store import create_location, create_spot, add_reading, list_readings
    home = create_location(db, "home")
    k = create_spot(db, home, "kitchen", floor=0)
    r1 = add_reading(db, k, rssi=-50, ssid="home")
    rows = list_readings(db)
    assert rows[0]["spot_name"] == "kitchen"
    assert rows[0]["location_name"] == "home"
    assert rows[0]["spot_id"] == k
    assert list_readings(db, location="home")[0]["id"] == r1
    assert list_readings(db, spot="kitchen")[0]["id"] == r1
    assert list_readings(db, location="home", floor=0)[0]["id"] == r1
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_store.py::test_readings_join_spot_location -v`
Expected: FAIL (`add_reading` still takes `location_id`).

- [ ] **Step 3: Implement**

```python
def add_reading(conn, spot_id: int, ts=None, ssid=None, bssid=None,
                rssi=None, noise=None, snr=None, channel=None, phy=None,
                tx_rate=None, ping_ms=None, down_mbps=None, up_mbps=None,
                server=None, note=None) -> int:
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO readings(
             ts, spot_id, ssid, bssid, rssi, noise, snr,
             channel, phy, tx_rate, ping_ms, down_mbps, up_mbps,
             server, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ts, spot_id, ssid, bssid, rssi, noise, snr,
         channel, phy, tx_rate, ping_ms, down_mbps, up_mbps, server, note))
    conn.commit()
    row = cur.lastrowid
    if row is None:
        raise sqlite3.Error("reading insert returned no row id")
    return row

def list_readings(conn, location=None, spot=None, floor=None, ssid=None, limit=50):
    if limit < 0:
        raise ValueError("limit must be >= 0")
    query = (
        "SELECT r.id, r.ts, r.spot_id, r.ssid, r.bssid, r.rssi,"
        " r.noise, r.snr, r.channel, r.phy, r.tx_rate,"
        " r.ping_ms, r.down_mbps, r.up_mbps, r.server, r.note,"
        " s.name AS spot_name, s.location_id AS location_id,"
        " s.floor AS floor, s.outdoors AS outdoors,"
        " l.name AS location_name"
        " FROM readings r JOIN spots s ON r.spot_id = s.id"
        " JOIN locations l ON s.location_id = l.id"
    )
    clauses, params = [], []
    if location is not None:
        if isinstance(location, bool):
            raise ValueError("invalid location filter: %r" % (location,))
        if isinstance(location, int):
            clauses.append("s.location_id = ?")
            params.append(location)
        else:
            name = str(location)
            try:
                as_id = int(name)
            except ValueError:
                as_id = None
            if as_id is not None:
                clauses.append("(s.location_id = ? OR l.name = ?)")
                params.extend([as_id, name])
            else:
                clauses.append("l.name = ?")
                params.append(name)
    if spot is not None:
        if isinstance(spot, bool):
            raise ValueError("invalid spot filter: %r" % (spot,))
        if isinstance(spot, int):
            clauses.append("r.spot_id = ?")
            params.append(spot)
        else:
            sname = str(spot)
            try:
                as_id = int(sname)
            except ValueError:
                as_id = None
            if as_id is not None:
                clauses.append("(r.spot_id = ? OR s.name = ?)")
                params.extend([as_id, sname])
            else:
                clauses.append("s.name = ?")
                params.append(sname)
    if floor is not None:
        clauses.append("s.floor = ?")
        params.append(floor)
    if ssid is not None:
        if isinstance(ssid, bool):
            raise ValueError("invalid ssid filter: %r" % (ssid,))
        clauses.append("r.ssid = ?")
        params.append(ssid)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY r.id DESC LIMIT ?"
    params.append(limit)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.row_factory = None
```

Delete `update_location_floor`; callers use `update_spot_floor`.

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_store.py -v`
Expected: FAIL on old tests referencing `location_id` — rewrite them now:
replace `create_location(db, "x", floor=)` with
`lid=create_location(db,"home"); create_spot(db,lid,"x",floor=)`,
`add_reading(db, location_id)` with spot id, `list_readings(location=)` stays,
room filter becomes `spot=`.

- [ ] **Step 5: Full store suite green**

Run: `pytest tests/test_store.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wifimap/store.py tests/test_store.py
git commit -m "feat(store): readings on spot_id with location join"
```

### Task 4: CLI spots + scan

**Files:**
- Modify: `src/wifimap/cli.py:40-110,183-224`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

```python
def test_scan_location_spot(monkeypatch, tmp_path, capsys):
    from wifimap import signal as signal_mod, store as store_mod
    from wifimap.cli import main
    db = str(tmp_path / "cli.db")
    monkeypatch.setattr(signal_mod, "read_signal",
        lambda timeout=2.0: signal_mod.Signal(ssid="h", rssi=-55))
    rc = main(["--db", db, "scan", "--location", "home",
               "--spot", "kitchen", "--no-speedtest"])
    assert rc == 0
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert rows[0]["location_name"] == "home"
        assert rows[0]["spot_name"] == "kitchen"
    finally:
        conn.close()
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_cli.py::test_scan_location_spot -v`
Expected: FAIL (no `--spot` flag).

- [ ] **Step 3: Implement parser + scan + spots subcommand**

Parser changes in `_build_parser`:
- scan: replace `--location-floor/--location-outdoors` with
  `--spot` (required), `--spot-floor type=int default=0`,
  `--spot-outdoors type=int choices=(0,1) default=0`.
- add `spots` subparser: `spots list --location REQ`,
  `spots add --location REQ --name REQ --floor --outdoors`.
- `locations add`: only `--name`.

Scan body:
```python
loc_id = store_mod.resolve_location(conn, args.location)
spot_id = store_mod.resolve_spot(
    conn, loc_id, args.spot,
    floor=args.spot_floor, outdoors=bool(args.spot_outdoors))
rid = store_mod.add_reading(conn, spot_id, ...)
print("#%d %s/%s rssi=..." % (rid, args.location, args.spot, ...))
```

Spots handlers mirror `_cmd_locations_*` using
`create_spot/list_spots` scoped to resolved location.

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_cli.py::test_scan_location_spot -v`
Expected: PASS. Then update remaining old CLI tests:
every `scan --location X` becomes `scan --location home --spot X`;
`locations add --name X --floor` drops `--floor`.

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/cli.py tests/test_cli.py
git commit -m "feat(cli): spots commands and scan location+spot"
```

### Task 5: CLI list/export filters + TUI scoping

**Files:**
- Modify: `src/wifimap/cli.py:75-86,227-298`
- Modify: `src/wifimap/tui.py:457-645,1138-1206`
- Test: `tests/test_cli.py`, `tests/test_tui.py`

- [ ] **Step 1: Write failing filter test**

```python
def test_list_filter_spot(tmp_path):
    from wifimap.store import get_db, create_location, create_spot, add_reading, list_readings
    conn = get_db(str(tmp_path / "f.db"))
    try:
        home = create_location(conn, "home")
        k = create_spot(conn, home, "kitchen")
        o = create_spot(conn, home, "office")
        add_reading(conn, k, rssi=-50)
        add_reading(conn, o, rssi=-70)
        assert len(list_readings(conn, spot="kitchen")) == 1
        assert len(list_readings(conn, location="home", spot="office")) == 1
    finally:
        conn.close()
```

- [ ] **Step 2: Run, verify FAIL/PASS**

Run: `pytest tests/test_cli.py -k filter -v`
Expected: new test passes after Task 3; CLI `--spot` flag missing → next step fails until parser added.

- [ ] **Step 3: Implement list/export `--spot` + export fields**

```python
_EXPORT_FIELDS = [
    "id", "ts", "spot_id", "location_id", "location_name", "spot_name",
    "floor", "outdoors",
    "ssid", "bssid", "rssi", "noise", "snr", "channel", "phy",
    "tx_rate", "ping_ms", "down_mbps", "up_mbps", "server", "note",
]
```

`list`/`export` parsers gain `--spot default=None`;
handlers pass `spot=args.spot` into `list_readings`.
List table header `location/spot` instead of `location`:
`print("%-4s %-12s %-12s ..." % ("id","location","spot",...))`.

- [ ] **Step 4: TUI — scope picker to location**

`WalkState.__init__` gains `active_location_id`;
`run_walk(..., location_preset)` resolves location id at startup
(`_resolve_preset` now returns location id, required).
Replace `store_mod.list_locations(conn)` with
`store_mod.list_spots(conn, state.active_location_id)` in
`_picker_curses`, `_walk_curses` render, `_fallback_pick`.
Replace `create_location` calls with
`create_spot(conn, state.active_location_id, ...)`.
`set_floor` calls `update_spot_floor`.
`_location_label` prints `#spot location/spot (floor N)` via `get_spot`.

- [ ] **Step 5: Run suites**

Run: `pytest tests/test_cli.py tests/test_tui.py -v`
Expected: PASS after updating TUI tests to create
`home` location + spots fixture.

- [ ] **Step 6: Commit**

```bash
git add src/wifimap/cli.py src/wifimap/tui.py tests/test_cli.py tests/test_tui.py
git commit -m "feat: list/export spot filter and walk scoped to location"
```

### Task 6: README + full green

**Files:**
- Modify: `README.md:10-31`
- Test: full suite

- [ ] **Step 1: Update README commands**

```
- `wifimap walk [--location HOME] ...` — preset location, `s` snapshot (pick/create spot inside it)
- `wifimap scan --location HOME --spot KITCHEN ...`
- `wifimap locations list|add` — physical sites
- `wifimap spots list|add --location HOME ...` — rooms/places
- `wifimap list/export [--location HOME] [--spot KITCHEN] ...`
```

- [ ] **Step 2: Run full suite**

Run: `pytest -v`
Expected: all PASS (target ~80+ tests).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: location spot commands"
```

## Self-review
- Spec coverage: bare locations ✓ (T1-2), spots with floor/outdoors ✓ (T1-2),
  readings on spot ✓ (T3), CLI spots+scan ✓ (T4), list/export filters ✓ (T5),
  walk preset+picker ✓ (T5), fresh break ✓ (T1 note), tests ✓ (T1-6).
- No placeholders: all steps show code/commands/expected output.
- Type consistency: `spot_id`, `location_id`, `spot_name`, `location_name`
  identical across store/CLI/TUI tasks.
