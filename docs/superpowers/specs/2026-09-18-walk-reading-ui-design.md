# Walk reading UI evolution (wifimap) — Design

Date: 2026-09-18
Status: approved
Goal: walk mode readings use space better, colour-code signal strength, graphs grow right-aligned while 60s history fills.

Decisions from user: newest sample at right edge, empty space left until 60s fills; colour in curses + fallback ANSI; wide terminals use side-by-side panels.

## 1. Layout

- Narrow (<100 cols): stacked as now. Rows: big RSSI+rating, SNR/noise/ch/phy/tx, Net/loc/pending, 3 graph lines, keys, toast.
- Wide (>=100 cols): two panels on same rows. Left panel = readings (RSSI, SNR, noise, ch/phy/tx, Net, loc). Right panel = 3 graph lines. Keys + toast full-width bottom.
- Height <10: skip graphs (existing rule). Height <6: readings truncated top-first.
- Fallback (no curses): same stacked order; no side-by-side (line-buffered output can't do columns reliably).

## 2. Right-aligned graphs

- `SparkHistory.sparkline(lo, hi, width)` left-pads with spaces to `width` so newest sample sticks to right edge.
- Partial fill (walk start): e.g. 5 samples in 40-wide graph renders 35 spaces + 5 blocks.
- Full (60s): no padding, scrolls left as now (oldest drops).
- Gaps (None / NO-WIFI): space char, same as now, padding indistinguishable from gaps by design.
- Fixed scales unchanged: RSSI -90..-30, SNR 0..40, noise -100..-60.

## 3. Colour coding

- Source rating: existing `rate_rssi` / `rate_snr` (GREAT/OK/WEAK/UNKNOWN). Noise line follows RSSI rating.
- Curses: `init_pair(1..4)` green/yellow/red/white-on-black guarded by `has_colors()` + try/except; `color_pair` applied to rating token + big RSSI/SNR values. Mono fallback when colours unavailable.
- Fallback ANSI: `\x1b[32m` green / `33m` yellow / `31m` red / `37m` white, reset `\x1b[0m`. Respects `NO_COLOR` env (plain when set).
- NO-WIFI / UNKNOWN: no colour (default white), avoids false signal.

## 4. Pure helpers (unit-tested)

- `sparkline(lo, hi, width, align="right")`: default right; left-pad to width. Keeps existing clamping + gap behaviour.
- `rating_style(rating) -> (curses_pair, ansi_code)`: GREAT->(1,32), OK->(2,33), WEAK->(3,31), UNKNOWN->(0/4,37). Pure mapping, no curses import.
- `layout_mode(width) -> "wide"|"narrow"`: width>=100 wide else narrow. Pure, tested at boundary 99/100.
- Render functions take plain strings + styles; curses `addstr` with `attr` and fallback `print` with ANSI kept in thin I/O layer (not unit-tested, same as now).

## 5. Error handling

- Colour init fail -> plain text, walk continues.
- Tiny terminal -> stacked + truncated, no crash.
- `NO_COLOR=1` in fallback -> strip ANSI.
- No DB/thread/state changes; `WalkState.poll` untouched.

## 6. Testing

- sparkline right-align: 2 samples in width 5 -> len 5, last 2 are blocks, first 3 spaces.
- rating_style maps all 4 ratings to distinct pairs/codes.
- layout_mode boundary 99 narrow / 100 wide.
- Existing TUI tests keep passing (sparkline callers updated with default align).
