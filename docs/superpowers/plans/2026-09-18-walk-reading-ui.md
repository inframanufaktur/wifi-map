# Walk reading UI evolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Right-aligned 60s graphs, colour-coded ratings, wide side-by-side walk layout.

**Architecture:** Pure helpers in `src/wifimap/tui.py` (sparkline align, rating_style, layout_mode) drive thin curses/ANSI render layer. No DB/thread/state changes.

**Tech Stack:** Python 3.9 stdlib, curses color_pair, ANSI escapes, pytest.

---

### Task 1: Right-aligned sparkline

**Files:**
- Modify: `src/wifimap/tui.py:108-134`
- Test: `tests/test_tui.py`

- [ ] **Step 1: Write the failing test**

```python
def test_sparkline_right_aligns_newest():
    h = tui_mod.SparkHistory(maxlen=60)
    h.append(-60)
    h.append(-50)
    line = h.sparkline(-90, -30, 5)
    assert len(line) == 5
    assert line[:3] == "   "
    assert line[3:] != "   "
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tui.py::test_sparkline_right_aligns_newest -v`
Expected: FAIL (len 2, not 5 — current clips to sample count, no left pad)

- [ ] **Step 3: Write minimal implementation**

In `src/wifimap/tui.py`, change signature and tail of `SparkHistory.sparkline`:

```python
def sparkline(self, lo: float, hi: float, width: int, align: str = "right") -> str:
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
    s = "".join(out)
    if align == "right" and len(s) < width:
        s = " " * (width - len(s)) + s
    return s
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tui.py -v -k "sparkline or history"`
Expected: PASS (existing `test_sparkline_vectors_gaps_and_window` still passes: full-width has no pad; `test_poll_appends_history` len 2 in width 5 now becomes len 5 — update that assertion to `len(...) == 5` if it fails)

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/tui.py tests/test_tui.py
git commit -m "feat(tui): right-aligned sparklines newest at right"
```

### Task 2: rating_style + layout_mode pure helpers

**Files:**
- Modify: `src/wifimap/tui.py:83-102`
- Test: `tests/test_tui.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_rating_style_maps_all_ratings():
    assert tui_mod.rating_style("GREAT") == (1, "32")
    assert tui_mod.rating_style("OK") == (2, "33")
    assert tui_mod.rating_style("WEAK") == (3, "31")
    assert tui_mod.rating_style("UNKNOWN") == (0, "37")


def test_layout_mode_boundary():
    assert tui_mod.layout_mode(99) == "narrow"
    assert tui_mod.layout_mode(100) == "wide"
    assert tui_mod.layout_mode(200) == "wide"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tui.py::test_rating_style_maps_all_ratings tests/test_tui.py::test_layout_mode_boundary -v`
Expected: FAIL with "has no attribute 'rating_style'"

- [ ] **Step 3: Write minimal implementation**

In `src/wifimap/tui.py` after `rate_snr`, insert:

```python
_RATING_STYLE = {
    "GREAT": (1, "32"),
    "OK": (2, "33"),
    "WEAK": (3, "31"),
    "UNKNOWN": (0, "37"),
}

WIDE_MIN_WIDTH = 100


def rating_style(rating: str) -> Tuple[int, str]:
    """Map GREAT/OK/WEAK/UNKNOWN to (curses_pair, ansi_code)."""
    return _RATING_STYLE.get(rating, (0, "37"))


def layout_mode(width: int) -> str:
    """Wide side-by-side at >=100 cols, else stacked narrow."""
    return "wide" if width >= WIDE_MIN_WIDTH else "narrow"
```

Add `"rating_style"`, `"layout_mode"`, `"WIDE_MIN_WIDTH"` to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tui.py -v -k "rating_style or layout_mode"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/tui.py tests/test_tui.py
git commit -m "feat(tui): rating_style and layout_mode helpers"
```

### Task 3: Curses colour + wide side-by-side render

**Files:**
- Modify: `src/wifimap/tui.py:621-702` (`_walk_curses` render block)

- [ ] **Step 1: Write the failing test (colour init guard is thin I/O — test the wiring helper)**

```python
def test_curses_pair_for_rating_uses_rating_style():
    pair, _ = tui_mod.rating_style(tui_mod.rate_rssi(-55))
    assert pair == 1
    pair, _ = tui_mod.rating_style(tui_mod.rate_rssi(-80))
    assert pair == 3
```

- [ ] **Step 2: Run test to verify it passes already (wiring exists via Task 2)**

Run: `pytest tests/test_tui.py::test_curses_pair_for_rating_uses_rating_style -v`
Expected: PASS (this is a pin test; render itself is manual-verified, same as existing curses code)

- [ ] **Step 3: Write minimal implementation (curses render)**

In `_walk_curses`, before loop, after `curses.curs_set(0)`:

```python
has_col = False
try:
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        curses.init_pair(3, curses.COLOR_RED, -1)
        has_col = True
except Exception:
    has_col = False
```

Replace `_emit(s: str)` with `_emit(s: str, attr: int = 0)` passing attr to `addstr`:

```python
def _emit(s: str, attr: int = 0) -> None:
    nonlocal row
    if row < h - 1:
        try:
            stdscr.addstr(row, 0, s[: w - 1], attr)
        except Exception:
            pass
        row += 1
```

Colour attrs (concrete wiring at emit sites):

```python
r_pair, _ = rating_style(rate_rssi(state.sig.rssi))
s_pair, _ = rating_style(rate_snr(state.sig.snr))
r_attr = curses.color_pair(r_pair) if (has_col and r_pair) else 0
s_attr = curses.color_pair(s_pair) if (has_col and s_pair) else 0
```

Use `r_attr` on the `RSSI ...` line, `s_attr` on the `SNR ...` line. Wide layout:

```python
mode = layout_mode(w)
if mode == "wide" and h >= 10 and not state.no_wifi:
    gw = max(10, (w // 2) - 12)
    rssi_g = state.hist_rssi.sparkline(-90, -30, gw)
    snr_g = state.hist_snr.sparkline(0, 40, gw)
    noise_g = state.hist_noise.sparkline(-100, -60, gw)
    _emit("RSSI %s [%s] | RSSI  %s" % (rssi_s, rate_rssi(state.sig.rssi), rssi_g), r_attr)
    _emit("SNR %s [%s] | SNR   %s" % (snr_s, rate_snr(state.sig.snr), snr_g), s_attr)
    _emit("noise %s ch %s | noise %s" % (noise_s, state.sig.channel or "-", noise_g))
else:
    existing stacked emits (graphs right-aligned via Task 1)
```

Keep narrow path byte-identical except sparkline now right-pads.

- [ ] **Step 4: Run full TUI tests**

Run: `pytest tests/test_tui.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/tui.py tests/test_tui.py
git commit -m "feat(tui): curses colours plus wide side-by-side"
```

### Task 4: Fallback ANSI colours + right-aligned graphs

**Files:**
- Modify: `src/wifimap/tui.py:776-823` (`_walk_fallback`)

- [ ] **Step 1: Write the failing test**

```python
def test_ansi_wrap_respects_no_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert tui_mod.ansi_wrap("x", "32") == "\x1b[32mx\x1b[0m"
    monkeypatch.setenv("NO_COLOR", "1")
    assert tui_mod.ansi_wrap("x", "32") == "x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tui.py::test_ansi_wrap_respects_no_color -v`
Expected: FAIL with "has no attribute 'ansi_wrap'"

- [ ] **Step 3: Write minimal implementation**

Add near `rating_style`:

```python
import os


def ansi_wrap(s: str, code: str) -> str:
    """Wrap s in ANSI colour; plain when NO_COLOR is set."""
    if os.environ.get("NO_COLOR"):
        return s
    return "\x1b[%sm%s\x1b[0m" % (code, s)
```

Add `"ansi_wrap"` to `__all__`. In `_walk_fallback`, wrap rating tokens:

```python
r_rating = rate_rssi(state.sig.rssi)
s_rating = rate_snr(state.sig.snr)
_, r_code = rating_style(r_rating)
_, s_code = rating_style(s_rating)
rssi_coloured = ansi_wrap("RSSI %s [%s]" % (rssi_s, r_rating), r_code)
```

Graphs keep `sparkline(...)` (now right-aligned). Print coloured header lines.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tui.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/wifimap/tui.py tests/test_tui.py
git commit -m "feat(tui): fallback ANSI colours respect NO_COLOR"
```

### Task 5: Full verification

- [ ] **Step 1: Run full suite**

Run: `pytest -q`
Expected: all pass (baseline 77 + 4 new ≈ 81)

- [ ] **Step 2: Manual curses smoke (tty only)**

Run: `python -m wifimap walk --no-speedtest --location office` (quit with `q`)
Expected: colours visible, graphs right-touch edge, wide terminal splits panels

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix(tui): review fixes for reading UI"
```
