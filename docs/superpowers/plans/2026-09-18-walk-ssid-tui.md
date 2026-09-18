# Walk SSID Override + Readable TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `walk --ssid` overrides broken auto-detect for the whole session and tags snapshots; TUI shows big rated readings plus 60s RSSI/noise/SNR graphs; `list`/`export` gain `--ssid` filter.

**Architecture:** Session override in `WalkState` (no schema change, `readings.ssid` column already exists); pure helpers `rate_rssi`/`rate_snr`/`SparkHistory` for rating and graphs; thread override + history cap through `run_walk` into both curses and fallback loops.

**Tech Stack:** Python 3.9 stdlib only, sqlite3, curses, pytest.

---

## File structure

- Modify `src/wifimap/store.py` — `list_readings(..., ssid=None)` filter + `idx_readings_ssid` index. No migration needed (`CREATE INDEX IF NOT EXISTS` runs in existing schema init).
- Modify `src/wifimap/cli.py` — `walk/list/export --ssid` args, blank rejection, pass-through to store/tui.
- Modify `src/wifimap/tui.py` — `WalkState.ssid_override` + histories, `rate_rssi`, `rate_snr`, `SparkHistory`, big rated render + 60s sparklines in `_walk_curses`/`_walk_fallback`, `run_walk(..., ssid=None)` threading.
- Modify `tests/test_tui.py` — rating, sparkline, override backfill/snapshot tests.
- Modify `tests/test_store.py` — ssid filter test.
- Modify `tests/test_cli.py` — `--ssid` flag parse + blank rejection + list/export filter pass-through tests.

---

### Task 1: Store SSID filter

**Files:**
- Modify: `src/wifimap/store.py:10-29` (`_SCHEMA`), `src/wifimap/store.py:218-270` (`list_readings`)
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing test**

```python
def test_list_readings_ssid_filter(db):
    conn = store_mod.get_db(db)
    try:
        a = store_mod.create_location(conn, "den", floor=0)
        store_mod.add_reading(conn, a, ssid="home-5g", rssi=-55)
        store_mod.add_reading(conn, a, ssid="iot-2g", rssi=-80)
        rows = store_mod.list_readings(conn, ssid="home-5g")
        assert len(rows) == 1
        assert rows[0]["ssid"] == "home-5g"
        assert len(store_mod.list_readings(conn)) == 2
    finally:
        conn.close()
```

Check the `db` fixture name in `tests/test_store.py` first; adapt the fixture name to match the file's existing convention.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_store.py::test_list_readings_ssid_filter -v`
Expected: FAIL with `TypeError: list_readings() got an unexpected keyword argument 'ssid'`

- [ ] **Step 3: Write minimal implementation**

In `src/wifimap/store.py`, add the index to `_SCHEMA`:

```sql
CREATE INDEX IF NOT EXISTS idx_readings_ssid ON readings(ssid);
```

Change the signature:

```python
def list_readings(
    conn: sqlite3.Connection,
    location: Optional[Union[int, str]] = None,
    floor: Optional[int] = None,
    ssid: Optional[str] = None,
    limit: int = 50,
) -> List[dict]:
```

Add the clause after the `floor` block, before `if clauses:`:

```python
    if ssid is not None:
        if isinstance(ssid, bool):
            raise ValueError("invalid ssid filter: %r" % (ssid,))
        clauses.append("r.ssid = ?")
        params.append(ssid)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/test_store.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/store.py tests/test_store.py
git commit -m "feat(store): filter readings by ssid"
```

---

### Task 2: CLI `--ssid` flags

**Files:**
- Modify: `src/wifimap/cli.py:60-63` (walk args), `src/wifimap/cli.py:73-81` (list/export args), `src/wifimap/cli.py:162-169` (`_cmd_walk`), `src/wifimap/cli.py:216-273` (`_cmd_list`, `_cmd_export`)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_walk_ssid_flag_parses():
    args = cli_mod._build_parser().parse_args(["walk", "--ssid", "home-5g"])
    assert args.ssid == "home-5g"


def test_walk_blank_ssid_rejected(capsys, tmp_path):
    rc = cli_mod.main(["--db", str(tmp_path / "w.db"), "walk",
                       "--ssid", "  ", "--no-speedtest"])
    assert rc == cli_mod.EXIT_STORAGE


def test_list_ssid_filter_passes_through(tmp_path, monkeypatch, capsys):
    seen = {}

    def _fake(conn, location=None, floor=None, ssid=None, limit=50):
        seen["ssid"] = ssid
        return []

    monkeypatch.setattr(store_mod, "list_readings", _fake)
    rc = cli_mod.main(["--db", str(tmp_path / "w.db"), "list",
                       "--ssid", "home-5g"])
    assert rc == cli_mod.EXIT_OK
    assert seen["ssid"] == "home-5g"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/test_cli.py -q -k "ssid"`
Expected: FAIL (no `--ssid` argument / `main` ignores it)

- [ ] **Step 3: Write minimal implementation**

Walk args (after `--location`):

```python
    w.add_argument("--ssid", default=None, help="Session SSID override (manual)")
```

List/export args (after `--floor` in each):

```python
    li.add_argument("--ssid", default=None, help="Filter by SSID")
    ex.add_argument("--ssid", default=None, help="Filter by SSID")
```

`_cmd_walk` — reject blank, pass through:

```python
def _cmd_walk(db_path: str, args: argparse.Namespace) -> int:
    from wifimap import tui as tui_mod

    ssid = getattr(args, "ssid", None)
    if ssid is not None and not ssid.strip():
        print("Error: --ssid must not be blank", file=sys.stderr)
        return EXIT_STORAGE
    return tui_mod.run_walk(
        db_path, interval=args.interval,
        location_preset=args.location,
        no_speedtest=args.no_speedtest,
        ssid=ssid.strip() if ssid is not None else None,
    )
```

`_cmd_list` / `_cmd_export` — reject blank, pass `ssid`:

```python
    ssid = getattr(args, "ssid", None)
    if ssid is not None and not ssid.strip():
        print("Error: --ssid must not be blank", file=sys.stderr)
        return EXIT_STORAGE
```

then in each `list_readings(...)` call add `ssid=ssid,`:

```python
            rows = store_mod.list_readings(
                conn, location=args.location, floor=args.floor,
                ssid=ssid, limit=args.limit)
```

(export uses `limit=1000000`, same `ssid=ssid` addition).

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/test_cli.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/cli.py tests/test_cli.py
git commit -m "feat(cli): add --ssid to walk/list/export"
```

---

### Task 3: `WalkState` session override

**Files:**
- Modify: `src/wifimap/tui.py:281-380` (`WalkState`)
- Test: `tests/test_tui.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_ssid_override_skips_autodetect(tmp_path):
    st = tui_mod.WalkState(str(tmp_path / "w.db"), ssid_override="home-5g")
    calls = []

    def _boom():
        calls.append(1)
        raise AssertionError("autodetect must not run")

    st.ensure_identity(identity_fn=_boom)
    assert st.net_ssid == "home-5g"
    assert calls == []


def test_ssid_override_backfills_and_tags_snapshot(tmp_path):
    import sqlite3

    db = str(tmp_path / "w.db")
    conn = store_mod.get_db(db)
    try:
        lid = store_mod.create_location(conn, "den", floor=0)
    finally:
        conn.close()
    st = tui_mod.WalkState(db, no_speedtest=True, ssid_override="home-5g")
    st.active_id = lid
    st.poll(read_fn=lambda: signal_mod.Signal(ssid="other", rssi=-60))
    assert st.sig.ssid == "home-5g"
    t = st.try_snapshot()
    assert t is not None
    t.join(timeout=10)
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert rows[0]["ssid"] == "home-5g"
    finally:
        conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/test_tui.py -q -k "ssid_override"`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'ssid_override'`

- [ ] **Step 3: Write minimal implementation**

`__init__` gains the field (strip, keep `None` when unset):

```python
    def __init__(self, db_path: str, no_speedtest: bool = False,
                 ssid_override: Optional[str] = None) -> None:
        self.db_path = db_path
        self.no_speedtest = no_speedtest
        self.ssid_override = ssid_override.strip() if ssid_override else None
```

`ensure_identity` short-circuits at the top:

```python
        if self.ssid_override is not None:
            self.net_ssid = self.ssid_override
            self._backfill_identity()
            return (self.net_ssid, self.net_bssid)
```

`_backfill_identity` gives the override top precedence:

```python
    def _backfill_identity(self) -> None:
        if self.ssid_override is not None:
            self.sig.ssid = self.ssid_override
            return
        if self.net_ssid is not None and self.sig.ssid is None:
            self.sig.ssid = self.net_ssid
        if self.net_bssid is not None and self.sig.bssid is None:
            self.sig.bssid = self.net_bssid
```

`try_snapshot` forces the tag on the frozen copy (after `sig_copy = copy.deepcopy(self.sig)`):

```python
        if self.ssid_override is not None:
            sig_copy.ssid = self.ssid_override
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/test_tui.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/tui.py tests/test_tui.py
git commit -m "feat(tui): session ssid override tags snapshots"
```

---

### Task 4: Signal rating helpers

**Files:**
- Modify: `src/wifimap/tui.py:56-79` (pure helpers area), `src/wifimap/tui.py:843-865` (`__all__`)
- Test: `tests/test_tui.py`

- [ ] **Step 1: Write the failing test**

```python
def test_rate_rssi_snr_thresholds():
    assert tui_mod.rate_rssi(-55) == "GREAT"
    assert tui_mod.rate_rssi(-65) == "OK"
    assert tui_mod.rate_rssi(-80) == "WEAK"
    assert tui_mod.rate_rssi(None) == "UNKNOWN"
    assert tui_mod.rate_snr(30) == "GREAT"
    assert tui_mod.rate_snr(20) == "OK"
    assert tui_mod.rate_snr(5) == "WEAK"
    assert tui_mod.rate_snr(None) == "UNKNOWN"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_tui.py::test_rate_rssi_snr_thresholds -v`
Expected: FAIL with `AttributeError: module 'wifimap.tui' has no attribute 'rate_rssi'`

- [ ] **Step 3: Write minimal implementation**

Place directly after `format_net_line`:

```python
def rate_rssi(v: Optional[int]) -> str:
    """Rate RSSI: >=-60 GREAT, >=-70 OK, else WEAK, None UNKNOWN."""
    if v is None:
        return "UNKNOWN"
    if v >= -60:
        return "GREAT"
    if v >= -70:
        return "OK"
    return "WEAK"


def rate_snr(v: Optional[int]) -> str:
    """Rate SNR: >=25 GREAT, >=15 OK, else WEAK, None UNKNOWN."""
    if v is None:
        return "UNKNOWN"
    if v >= 25:
        return "GREAT"
    if v >= 15:
        return "OK"
    return "WEAK"
```

Add `"rate_rssi"` and `"rate_snr"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/test_tui.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/tui.py tests/test_tui.py
git commit -m "feat(tui): rate rssi/snr good-ok-weak"
```

---

### Task 5: 60s sparkline history helper

**Files:**
- Modify: `src/wifimap/tui.py` (pure helpers area + `__all__`)
- Test: `tests/test_tui.py`

- [ ] **Step 1: Write the failing test**

```python
def test_sparkline_vectors_gaps_and_window():
    h = tui_mod.SparkHistory(maxlen=8)
    for v in [-90, -70, -50, -30]:
        h.append(v)
    line = h.sparkline(-90, -30, 4)
    assert line == "\u2581\u2583\u2585\u2588"
    h2 = tui_mod.SparkHistory(maxlen=8)
    h2.append(-50)
    h2.append(None)
    h2.append(-50)
    assert h2.sparkline(-90, -30, 3)[1] == " "
    h3 = tui_mod.SparkHistory(maxlen=3)
    for v in [1, 2, 3, 4]:
        h3.append(v)
    assert h3.sparkline(1, 4, 10) == "\u2581\u2584\u2588"
    assert tui_mod.SparkHistory(maxlen=4).sparkline(0, 1, 4) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_tui.py::test_sparkline_vectors_gaps_and_window -v`
Expected: FAIL with `AttributeError: module 'wifimap.tui' has no attribute 'SparkHistory'`

- [ ] **Step 3: Write minimal implementation**

Imports at top of `tui.py`: add `import math` and extend the `collections` import. The file already has `from collections import ...`? No — it has no collections import, so add:

```python
import math
from collections import deque
```

Place the class after `rate_snr`:

```python
_SPARK_CHARS = "▁▂▃▄▅▆▇█"


class SparkHistory:
    """Fixed-length sample ring; renders ASCII block sparkline."""

    def __init__(self, maxlen: int = 60) -> None:
        self._buf: deque = deque(maxlen=max(1, maxlen))

    def append(self, v: Optional[float]) -> None:
        self._buf.append(v)

    def sparkline(self, lo: float, hi: float, width: int) -> str:
        vals = list(self._buf)[-width:] if width > 0 else []
        if not vals:
            return ""
        span = hi - lo
        out = []
        for v in vals:
            if v is None:
                out.append(" ")
                continue
            if span <= 0:
                lvl = 7
            else:
                frac = (v - lo) / span
                frac = 0.0 if frac < 0.0 else (1.0 if frac > 1.0 else frac)
                lvl = int(round(frac * 7))
            out.append(_SPARK_CHARS[lvl])
        return "".join(out)


def history_cap(interval: float) -> int:
    """Samples covering ~60s at the poll interval; at least 1."""
    return max(1, int(math.ceil(60.0 / interval)))
```

Add `"SparkHistory"` and `"history_cap"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/test_tui.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/tui.py tests/test_tui.py
git commit -m "feat(tui): sparkline history helper"
```

---

### Task 6: Walk loops — thread override, collect history, render big + graphs

**Files:**
- Modify: `src/wifimap/tui.py:281-300` (`WalkState.__init__`), `poll`, `run_walk`, `_walk_curses`, `_walk_fallback`
- Test: `tests/test_tui.py` (history wiring test below)

- [ ] **Step 1: Write the failing test**

```python
def test_poll_appends_history(tmp_path):
    st = tui_mod.WalkState(str(tmp_path / "w.db"), history_max=5)
    st.poll(read_fn=lambda: signal_mod.Signal(rssi=-60, noise=-90, snr=30))
    st.poll(read_fn=lambda: signal_mod.Signal(rssi=-61, noise=-91, snr=29))
    assert st.hist_rssi.sparkline(-90, -30, 5) != ""
    assert len(st.hist_rssi.sparkline(-90, -30, 5)) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_tui.py::test_poll_appends_history -v`
Expected: FAIL with `TypeError` (no `history_max` param) or `AttributeError` (no `hist_rssi`)

- [ ] **Step 3: Write minimal implementation**

`WalkState.__init__` gains history storage:

```python
    def __init__(self, db_path: str, no_speedtest: bool = False,
                 ssid_override: Optional[str] = None,
                 history_max: int = 60) -> None:
        self.db_path = db_path
        self.no_speedtest = no_speedtest
        self.ssid_override = ssid_override.strip() if ssid_override else None
        self.history_max = max(1, history_max)
        self.hist_rssi: SparkHistory = SparkHistory(maxlen=self.history_max)
        self.hist_noise: SparkHistory = SparkHistory(maxlen=self.history_max)
        self.hist_snr: SparkHistory = SparkHistory(maxlen=self.history_max)
```

`poll` appends after computing `self.sig` (both success and NO-WIFI paths — append `None` on NO-WIFI so graphs show a gap):

```python
        try:
            self.sig = attempt_read(fn)
            self.no_wifi = False
            self.no_wifi_msg = ""
            self._backfill_identity()
        except signal_mod.NoWiFiError as exc:
            self.no_wifi = True
            self.no_wifi_msg = str(exc)
            self.hist_rssi.append(None)
            self.hist_noise.append(None)
            self.hist_snr.append(None)
            return
        self.hist_rssi.append(self.sig.rssi)
        self.hist_noise.append(self.sig.noise)
        self.hist_snr.append(self.sig.snr)
```

`run_walk` gains `ssid` with blank rejection:

```python
def run_walk(db_path: str, interval: float = 1.0,
             location_preset: Optional[str] = None,
             no_speedtest: bool = False,
             ssid: Optional[str] = None) -> int:
    """Run walk loop; curses when tty available else ANSI fallback."""
    if interval <= 0:
        print("Error: --interval must be > 0", file=sys.stderr)
        return 3
    if ssid is not None and not ssid.strip():
        print("Error: --ssid must not be blank", file=sys.stderr)
        return 3
    ...
```

Pass `ssid` into both loops: `_walk_curses(stdscr, db_path, interval, location_preset, no_speedtest, ssid)` and `_walk_fallback(db_path, interval, location_preset, no_speedtest, ssid)`. Inside each, construct state as:

```python
    cap = history_cap(interval)
    state = WalkState(db_path, no_speedtest=no_speedtest,
                      ssid_override=ssid, history_max=cap)
```

and skip auto-detect noise when override is set (Task 3 handles it).

Curses render — replace the three emission lines:

```python
                if state.no_wifi:
                    _emit("NO-WIFI: %s" % (state.no_wifi_msg,))
                    _emit("`s` blocked; fix WiFi or quit with `q`.")
                else:
                    _emit(format_signal_line(state.sig))
                _emit(format_net_line(state.net_ssid))
```

with:

```python
                if state.no_wifi:
                    _emit("NO-WIFI: %s" % (state.no_wifi_msg,))
                    _emit("`s` blocked; fix WiFi or quit with `q`.")
                else:
                    rssi_s = "UNKNOWN" if state.sig.rssi is None else "%d dBm" % state.sig.rssi
                    snr_s = "UNKNOWN" if state.sig.snr is None else "%d dB" % state.sig.snr
                    noise_s = "UNKNOWN" if state.sig.noise is None else "%d dBm" % state.sig.noise
                    _emit("RSSI %s [%s]" % (rssi_s, rate_rssi(state.sig.rssi)))
                    _emit("SNR %s [%s]  noise %s  ch %s  phy %s  tx %s" % (
                        snr_s, rate_snr(state.sig.snr), noise_s,
                        state.sig.channel or "-", state.sig.phy or "-",
                        state.sig.tx_rate or "-"))
                manual = " (manual)" if state.ssid_override else ""
                _emit("Net: %s%s" % (state.net_ssid or "unknown", manual))
```

then after the `loc:` line, graphs when the screen is tall enough:

```python
                if h >= 10 and not state.no_wifi:
                    gw = max(10, w - 12)
                    _emit("RSSI  %s [60s]" % state.hist_rssi.sparkline(-90, -30, gw))
                    _emit("SNR   %s [60s]" % state.hist_snr.sparkline(0, 40, gw))
                    _emit("noise %s [60s]" % state.hist_noise.sparkline(-100, -60, gw))
```

Fallback loop — same big lines and graphs via `print`:

```python
            if state.no_wifi:
                print("NO-WIFI: %s (`s` blocked)" % state.no_wifi_msg)
            else:
                rssi_s = "UNKNOWN" if state.sig.rssi is None else "%d dBm" % state.sig.rssi
                print("RSSI %s [%s]" % (rssi_s, rate_rssi(state.sig.rssi)), flush=True)
                print("SNR %s [%s]  noise %s" % (
                    "UNKNOWN" if state.sig.snr is None else "%d dB" % state.sig.snr,
                    rate_snr(state.sig.snr),
                    "UNKNOWN" if state.sig.noise is None else "%d dBm" % state.sig.noise),
                    flush=True)
            manual = " (manual)" if state.ssid_override else ""
            print("Net: %s%s" % (state.net_ssid or "unknown", manual), flush=True)
            gw = 40
            print("RSSI  %s [60s]" % state.hist_rssi.sparkline(-90, -30, gw), flush=True)
```

Keep `format_signal_line`/`format_net_line` untouched (existing tests rely on them).

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/ -q`
Expected: all PASS

Manual check: `PYTHONPATH=src python3 -m wifimap.cli --db /tmp/ssid-plan-check.db walk --ssid home-5g --no-speedtest` (quit with `q`), plus fallback by piping: `echo q | PYTHONPATH=src python3 -m wifimap.cli --db /tmp/ssid-plan-check.db walk --ssid home-5g --no-speedtest`.

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/tui.py tests/test_tui.py
git commit -m "feat(tui): big rated readings plus 60s graphs"
```

---

### Task 7: Full verification

- [ ] **Step 1: Run the whole suite**

Run: `PYTHONPATH=src python3 -m pytest tests/ -q`
Expected: all PASS (89 + new tests)

- [ ] **Step 2: Manual CLI checks**

Run: `PYTHONPATH=src python3 -m wifimap.cli --help` and `PYTHONPATH=src python3 -m wifimap.cli walk --help`
Expected: `--ssid` listed under walk, list, export.

- [ ] **Step 3: Commit any leftovers**

```bash
git status --short
git log --oneline -8
```

Only commit if the tree is dirty; otherwise no-op.

---

## Self-review

- Spec §1 CLI: Task 2 (flags + blank rejection) + Task 6 (`run_walk` blank guard). Covered.
- Spec §2 state/data flow: Task 3 (override, backfill, snapshot tag) + Task 1 (ssid filter + index). Covered. CSV needs no change (ssid column already exported).
- Spec §3 TUI: Task 4 (ratings) + Task 5 (sparkline + cap) + Task 6 (curses + fallback render, manual marker, height guard). Covered.
- Spec §4 pure helpers: Tasks 4–5 with exact thresholds, chars, scales. Covered.
- Spec §5 errors: blank `--ssid` → 3 (Tasks 2 + 6); override authoritative, no toast change (Task 3 keeps `ensure_identity` silent on override); UNKNOWN → `UNKNOWN` + gap (Tasks 5–6); DB paths untouched. Covered.
- Spec §6 testing: unit tests per task + manual walk + full suite (Task 7). Covered.
- No placeholders: every code step shows exact code, paths, commands.
- Type consistency: `ssid: Optional[str]`, `poll` appends `Optional[int]` into `SparkHistory.append(Optional[float])`, `sparkline(lo, hi, width) -> str`, `history_cap(interval) -> int` used by both loops.
