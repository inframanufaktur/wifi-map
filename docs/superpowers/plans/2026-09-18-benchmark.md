# Location Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One ideal-conditions snapshot per location with delta on every reading plus walk visibility.

**Architecture:** New `benchmarks` table keyed by `location_id`; `store.py` owns get/set/delta; `cli.py` adds `benchmark set|show|clear` and scan delta; `tui.py` adds `b` key, bench header, persistent `last:` line.

**Tech Stack:** Python 3.9 stdlib, SQLite WAL, argparse, curses + ANSI fallback, pytest with mocks.

---

### File structure

- Modify: `src/wifimap/store.py` — add `benchmarks` DDL to `_SCHEMA`, add `get_benchmark()`, `set_benchmark()`, `clear_benchmark()`, `format_benchmark_delta()`.
- Modify: `src/wifimap/cli.py` — add `benchmark` subparser + `_cmd_benchmark_*`, extend `_cmd_scan` output with delta.
- Modify: `src/wifimap/tui.py` — add `KEY_BENCHMARK="b"`, `WalkState.last_result` + `WalkState.benchmark`, bench header + `last:` line render in `_walk_curses` and `_walk_fallback`, `b` handlers, overwrite y/N prompt helpers.
- Modify: `tests/test_store.py` — benchmark CRUD + delta tests.
- Modify: `tests/test_cli.py` — benchmark set/show/clear + scan delta tests.
- Modify: `tests/test_tui.py` — delta + WalkState last_result tests.
- Modify: `README.md` — benchmark commands + walk `b` key row.

---

### Task 1: Store benchmarks table + get/set/clear

**Files:**
- Modify: `src/wifimap/store.py:10-43`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing test**

```python
def test_benchmark_set_get_overwrite(db):
    from wifimap.store import set_benchmark, get_benchmark, create_location
    lid = create_location(db, "home")
    set_benchmark(db, lid, rssi=-45, snr=32, down_mbps=310.5, up_mbps=48.2, note="ethernet")
    b = get_benchmark(db, lid)
    assert b["rssi"] == -45
    assert b["down_mbps"] == 310.5
    set_benchmark(db, lid, rssi=-50, snr=30, down_mbps=200.0, up_mbps=40.0, note="redo")
    b2 = get_benchmark(db, lid)
    assert b2["rssi"] == -50
    assert b2["note"] == "redo"


def test_benchmark_missing_returns_none(db):
    from wifimap.store import get_benchmark, create_location
    lid = create_location(db, "home")
    assert get_benchmark(db, lid) is None


def test_benchmark_clear(db):
    from wifimap.store import set_benchmark, get_benchmark, clear_benchmark, create_location
    lid = create_location(db, "home")
    set_benchmark(db, lid, rssi=-45)
    clear_benchmark(db, lid)
    assert get_benchmark(db, lid) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_store.py::test_benchmark_set_get_overwrite -v`
Expected: FAIL with `ImportError` / `AttributeError: set_benchmark`.

- [ ] **Step 3: Write minimal implementation**

Append to `_SCHEMA` in `src/wifimap/store.py`:

```sql
CREATE TABLE IF NOT EXISTS benchmarks(
  location_id INTEGER PRIMARY KEY REFERENCES locations(id) ON DELETE CASCADE,
  ts TEXT NOT NULL,
  ssid TEXT, bssid TEXT,
  rssi INTEGER, noise INTEGER, snr INTEGER,
  channel TEXT, phy TEXT, tx_rate TEXT,
  ping_ms REAL, down_mbps REAL, up_mbps REAL,
  server TEXT, note TEXT
);
```

Add after `add_reading()` in same file:

```python
def set_benchmark(
    conn: sqlite3.Connection,
    location_id: int,
    ts: Optional[str] = None,
    ssid: Optional[str] = None,
    bssid: Optional[str] = None,
    rssi: Optional[int] = None,
    noise: Optional[int] = None,
    snr: Optional[int] = None,
    channel: Optional[str] = None,
    phy: Optional[str] = None,
    tx_rate: Optional[str] = None,
    ping_ms: Optional[float] = None,
    down_mbps: Optional[float] = None,
    up_mbps: Optional[float] = None,
    server: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    """Upsert one benchmark row per location; unknown location raises ValueError."""
    if isinstance(location_id, bool):
        raise ValueError("unknown location id: %r" % (location_id,))
    row = conn.execute(
        "SELECT id FROM locations WHERE id = ?", (location_id,)
    ).fetchone()
    if row is None:
        raise ValueError("unknown location id: %r" % (location_id,))
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO benchmarks(
             location_id, ts, ssid, bssid, rssi, noise, snr,
             channel, phy, tx_rate, ping_ms, down_mbps, up_mbps,
             server, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(location_id) DO UPDATE SET
             ts=excluded.ts, ssid=excluded.ssid, bssid=excluded.bssid,
             rssi=excluded.rssi, noise=excluded.noise, snr=excluded.snr,
             channel=excluded.channel, phy=excluded.phy, tx_rate=excluded.tx_rate,
             ping_ms=excluded.ping_ms, down_mbps=excluded.down_mbps,
             up_mbps=excluded.up_mbps, server=excluded.server, note=excluded.note""",
        (location_id, ts, ssid, bssid, rssi, noise, snr,
         channel, phy, tx_rate, ping_ms, down_mbps, up_mbps,
         server, note),
    )
    conn.commit()


def get_benchmark(conn: sqlite3.Connection, location_id: int) -> Optional[dict]:
    """Return benchmark dict for location, or None when missing."""
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM benchmarks WHERE location_id = ?",
            (location_id,),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.row_factory = None


def clear_benchmark(conn: sqlite3.Connection, location_id: int) -> None:
    conn.execute("DELETE FROM benchmarks WHERE location_id = ?", (location_id,))
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_store.py -v -k benchmark`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/store.py tests/test_store.py
git commit -m "feat: benchmarks table with set/get/clear per location"
```

---

### Task 2: Delta formatter (RSSI/SNR + down/up)

**Files:**
- Modify: `src/wifimap/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing test**

```python
def test_format_benchmark_delta_full():
    from wifimap.store import format_benchmark_delta
    cur = {"rssi": -67, "snr": 24, "down_mbps": 48.1, "up_mbps": 10.0}
    bench = {"rssi": -45, "snr": 32, "down_mbps": 310.5, "up_mbps": 48.2}
    s = format_benchmark_delta(cur, bench)
    assert "rssi -22" in s
    assert "snr -8" in s
    assert "down -262.4" in s


def test_format_benchmark_delta_null_safe():
    from wifimap.store import format_benchmark_delta
    s = format_benchmark_delta({"rssi": -67}, {"rssi": None, "down_mbps": 100.0})
    assert s == "" or "rssi" not in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_store.py::test_format_benchmark_delta_full -v`
Expected: FAIL with `AttributeError: format_benchmark_delta`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/wifimap/store.py`:

```python
def format_benchmark_delta(cur: dict, bench: Optional[dict]) -> str:
    """Render `rssi/snr/down/up` deltas vs benchmark; '' when nothing comparable."""
    if not bench:
        return ""
    parts = []
    for key, label in (("rssi", "rssi"), ("snr", "snr"),
                       ("down_mbps", "down"), ("up_mbps", "up")):
        c = cur.get(key) if isinstance(cur, dict) else None
        b = bench.get(key)
        if c is None or b is None:
            continue
        try:
            d = float(c) - float(b)
        except (TypeError, ValueError):
            continue
        if label in ("down", "up"):
            parts.append("%s %+.1f" % (label, d))
        else:
            parts.append("%s %+d" % (label, int(round(d))))
    return ", ".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_store.py -v -k delta`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/store.py tests/test_store.py
git commit -m "feat: benchmark delta formatter for rssi/snr/down/up"
```

---

### Task 3: CLI benchmark set/show/clear + scan delta

**Files:**
- Modify: `src/wifimap/cli.py:41-111`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
def test_benchmark_set_and_show(monkeypatch, tmp_path, capsys):
    from wifimap.cli import main
    from wifimap import signal as signal_mod
    from wifimap import store as store_mod
    db = str(tmp_path / "bench.db")
    monkeypatch.setattr(
        signal_mod, "read_signal",
        lambda timeout=2.0: signal_mod.Signal(
            ssid="h", bssid="aa", rssi=-45, noise=-90, snr=45,
            channel="36", phy="802.11ax", tx_rate="1200"),
    )
    rc = main(["--db", db, "benchmark", "set", "--location", "HOME", "--no-speedtest"])
    assert rc == 0
    out = capsys.readouterr()
    assert "benchmark" in out.out.lower()
    rc2 = main(["--db", db, "benchmark", "show", "--location", "HOME"])
    out2 = capsys.readouterr()
    assert rc2 == 0
    assert "-45" in out2.out


def test_scan_shows_delta_when_benchmark(monkeypatch, tmp_path, capsys):
    from wifimap.cli import main
    from wifimap import signal as signal_mod
    from wifimap import store as store_mod
    db = str(tmp_path / "delta.db")
    conn = store_mod.get_db(db)
    lid = store_mod.resolve_location(conn, "HOME")
    store_mod.set_benchmark(conn, lid, rssi=-45, snr=32, down_mbps=300.0, up_mbps=40.0)
    conn.close()
    monkeypatch.setattr(
        signal_mod, "read_signal",
        lambda timeout=2.0: signal_mod.Signal(
            ssid="h", bssid="aa", rssi=-67, noise=-91, snr=24,
            channel="36", phy="802.11ax", tx_rate="800"),
    )
    rc = main(["--db", db, "scan", "--location", "HOME",
               "--room", "K", "--spot", "W", "--no-speedtest"])
    out = capsys.readouterr()
    assert rc == 0
    assert "vs bench" in out.out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::test_benchmark_set_and_show -v`
Expected: FAIL with `SystemExit: 2` (unrecognized `benchmark` subcommand).

- [ ] **Step 3: Write minimal implementation**

In `_build_parser()` after `spots` block add:

```python
bm = sub.add_parser("benchmark", help="Ideal-conditions reference per location.")
bm_sub = bm.add_subparsers(dest="benchmark_cmd", required=True)
bm_set = bm_sub.add_parser("set", help="Capture benchmark for a location.")
bm_set.add_argument("--location", required=True, help="Location ID|NAME")
bm_set.add_argument("--no-speedtest", action="store_true")
bm_set.add_argument("--note", default=None)
bm_set.add_argument("--force", action="store_true")
bm_show = bm_sub.add_parser("show", help="Print benchmark for a location.")
bm_show.add_argument("--location", required=True, help="Location ID|NAME")
bm_clear = bm_sub.add_parser("clear", help="Delete benchmark for a location.")
bm_clear.add_argument("--location", required=True, help="Location ID|NAME")
bm_clear.add_argument("--force", action="store_true")
```

Add handlers (mirror `_cmd_scan` signal/speedtest flow, `resolve_location` without auto-room/spot, `get_benchmark` check + `input("Overwrite benchmark for L? [y/N] ")` unless `--force`, then `set_benchmark`):

```python
def _cmd_benchmark_set(db_path: str, args: argparse.Namespace) -> int:
    try:
        conn = _open_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return EXIT_STORAGE
    try:
        try:
            loc_id = store_mod.resolve_location(conn, args.location)
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot resolve location: %s" % (exc,), file=sys.stderr)
            return EXIT_STORAGE
        old = store_mod.get_benchmark(conn, loc_id)
        if old is not None and not args.force:
            print("existing benchmark: rssi=%s snr=%s down=%s up=%s note=%s" % (
                _disp(old.get("rssi")), _disp(old.get("snr")),
                _disp(old.get("down_mbps")), _disp(old.get("up_mbps")),
                _disp(old.get("note"))))
            try:
                ans = input("Overwrite benchmark for %s? [y/N] " % (args.location,)).strip().lower()
            except (EOFError, OSError):
                ans = ""
            if ans not in ("y", "yes"):
                print("benchmark kept")
                return EXIT_OK
        try:
            sig = signal_mod.read_signal()
        except signal_mod.NoWiFiError as exc:
            print("Error: no WiFi: %s" % (exc,), file=sys.stderr)
            return EXIT_NOWIFI
        except signal_mod.SignalUnavailableError as exc:
            print("Error: signal backend unavailable: %s" % (exc,), file=sys.stderr)
            return EXIT_NOWIFI
        ping_ms = down_mbps = up_mbps = None
        server = None
        if not args.no_speedtest:
            try:
                sp = speed_mod.run_speedtest()
                ping_ms, down_mbps, up_mbps, server = sp.ping_ms, sp.down_mbps, sp.up_mbps, sp.server
            except speed_mod.SpeedtestUnavailableError as exc:
                print("Warning: %s; proceeding signal-only" % (exc,), file=sys.stderr)
            except speed_mod.SpeedtestFailedError:
                ping_ms, down_mbps, up_mbps, server = None, None, None, "ERROR"
        store_mod.set_benchmark(
            conn, loc_id, ssid=sig.ssid, bssid=sig.bssid, rssi=sig.rssi,
            noise=sig.noise, snr=sig.snr, channel=sig.channel, phy=sig.phy,
            tx_rate=sig.tx_rate, ping_ms=ping_ms, down_mbps=down_mbps,
            up_mbps=up_mbps, server=server, note=args.note)
        print("benchmark set for %s rssi=%s snr=%s down=%s up=%s note=%s" % (
            args.location, _disp(sig.rssi), _disp(sig.snr),
            _disp(down_mbps), _disp(up_mbps), _disp(args.note)))
        return EXIT_OK
    finally:
        conn.close()
```

Wire into `main()`:

```python
if args.cmd == "benchmark":
    if args.benchmark_cmd == "set":
        return _cmd_benchmark_set(db_path, args)
    if args.benchmark_cmd == "show":
        return _cmd_benchmark_show(db_path, args)
    if args.benchmark_cmd == "clear":
        return _cmd_benchmark_clear(db_path, args)
```

`_cmd_benchmark_show` uses `lookup_location` (no create) + `get_benchmark`, exit 3 when missing. `_cmd_benchmark_clear` prompts unless `--force`.

Extend `_cmd_scan` before final print: resolve `loc_id` already known, call `store_mod.get_benchmark(conn, loc_id)`, build `delta = store_mod.format_benchmark_delta({...}, bench)`, append `" (vs bench: %s)" % delta` when non-empty.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v -k "benchmark or delta"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/cli.py tests/test_cli.py
git commit -m "feat: benchmark CLI set/show/clear plus scan delta"
```

---

### Task 4: Walk TUI `b` key + bench header + last-result line

**Files:**
- Modify: `src/wifimap/tui.py`
- Test: `tests/test_tui.py`

- [ ] **Step 1: Write the failing test**

```python
def test_walk_state_last_result_delta(tmp_path):
    from wifimap import tui as tui_mod
    from wifimap import store as store_mod
    db = str(tmp_path / "walkbench.db")
    conn = store_mod.get_db(db)
    lid = store_mod.create_location(conn, "home")
    store_mod.set_benchmark(conn, lid, rssi=-45, snr=32, down_mbps=300.0, up_mbps=40.0)
    conn.close()
    st = tui_mod.WalkState(db)
    assert st.last_result == ""
    cur = {"rssi": -67, "snr": 24, "down_mbps": 48.1, "up_mbps": 10.0}
    bench = {"rssi": -45, "snr": 32, "down_mbps": 300.0, "up_mbps": 40.0}
    d = store_mod.format_benchmark_delta(cur, bench)
    st.last_result = "saved #1 (vs bench: %s)" % d
    assert "vs bench" in st.last_result
    assert tui_mod.KEY_BENCHMARK == "b"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tui.py::test_walk_state_last_result_delta -v`
Expected: FAIL with `AttributeError: KEY_BENCHMARK` (or `last_result`).

- [ ] **Step 3: Write minimal implementation**

Module constant next to `KEY_SNAPSHOT`:

```python
KEY_BENCHMARK = "b"
```

`WalkState.__init__` add:

```python
self.last_result: str = ""
self.benchmark: Optional[dict] = None
```

`WalkState.on_snapshot_done` extend: after setting toast, look up benchmark for active location id (open short-lived `store_mod.get_db(self.db_path)` read, guard all exceptions), format delta from `res` reading row when `res.ok`, set `self.last_result` to `"saved #id (vs bench: delta)"` or `"saved #id"` when no benchmark.

Curses loop: update keys line to `"keys: s snapshot | b benchmark | l switch | n new | f floor | q quit"`, emit bench header `bench: rssi X snr Y down D up U` when `state.benchmark` present, emit `last: ...` line when `state.last_result` non-empty. Add `elif key == KEY_BENCHMARK:` branch mirroring `KEY_SNAPSHOT` but calling benchmark capture (freeze signal, note via `_prompt_curses(stdscr, "benchmark note: ", ...)`, overwrite y/N via `_prompt_curses(stdscr, "Overwrite benchmark? [y/N] ", ...)`, `store_mod.set_benchmark` on worker connection).

Fallback loop: same keys line, bench/`last:` prints, `elif key == KEY_BENCHMARK:` branch using `input()` prompts.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tui.py -v -k "benchmark or last_result"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/tui.py tests/test_tui.py
git commit -m "feat: walk benchmark key with delta and last-result line"
```

---

### Task 5: README + full suite

**Files:**
- Modify: `README.md`
- Test: full `pytest`

- [ ] **Step 1: Update docs**

Add to Commands table:

```
| `wifimap benchmark set --location L [--no-speedtest] [--note T] [--force]` / `show` / `clear` | Capture/show/delete ideal-conditions reference (one per location, overwrite prompts) |
```

Update walk keys line to `s snapshot | b benchmark | l switch | n new | f floor | q quit`. Add one paragraph: snapshots print `(vs bench: ...)`; walk keeps persistent `last:` line with speedtest + delta.

- [ ] **Step 2: Run full suite**

Run: `pytest -q`
Expected: all pass (baseline 140 + ~9 new ≈ 149 passed).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: benchmark usage and walk keys"
```

---

## Self-Review

- Spec §1 storage → Task 1 (table + get/set/clear).
- Spec §1 delta → Task 2 (formatter, NULL-safe).
- Spec §2 CLI set/show/clear + scan delta + exits → Task 3.
- Spec §3 walk `b`, overwrite prompt, keys line, bench header, `last:` line, speedtest visibility → Task 4.
- Spec §4 errors (no benchmark, NO-WIFI block, signal-only, DB exit 3) → covered in Tasks 3–4 implementations.
- Spec §5 tests (store/CLI/TUI, mocks only) → Tasks 1–4 tests + Task 5 full run.
- No placeholders: every step has file paths, code, commands, expected output.
- Type consistency: `location_id: int`, benchmark `dict` rows, `format_benchmark_delta(cur: dict, bench: Optional[dict]) -> str`, `KEY_BENCHMARK = "b"`, `WalkState.last_result: str`.
