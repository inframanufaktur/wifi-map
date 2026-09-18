# wifimap v1 — Implementation Plan

Spec: `docs/superpowers/specs/2026-09-18-wifimap-design.md` (commits 9dcb2c2, 758fc6a, 1609ea5, 004eb93).

## Goal
Working `wifimap` CLI on macOS: `walk` (live signal + `s` snapshot with location pick/create, `l`/`n`/`f` keys), `scan` one-shot, `locations` CRUD, `list`, `export`. SQLite with `locations` + `readings` (FK). Observable done: walk house, tag rooms+floors, rows in DB, CSV out.

## Non-goals (v1)
Heatmaps, Linux, iperf3, GPS/elevation, daemon mode, Textual/Rich deps.

## Current-state findings
- Repo empty except `docs/superpowers/specs/`. Fresh `git`, branch `production`, brew git works (`/opt/homebrew/bin/git`), Apple `/usr/bin/git` shim blocked.
- macOS 25.6 ARM64. `airport` CLI gone (`/usr/sbin/airport` missing). System `python3` has no PyObjC/CoreWLAN. Ookla `speedtest` not installed.
- `system_profiler SPAirPortDataType` works but slow (seconds) — fallback only, not 1s poll path.
- Spec assumes `airport -I` fallback; that path is dead on this machine. Signal backend must be resolved by spike (milestone 0).

## Proposed approach
stdlib-first Python package `src/wifimap/` + `tests/`. Signal backend behind `signal.py` interface so spike result plugs in. Speedtest via Ookla JSON subprocess, graceful missing-binary path. Walk TUI in curses with ANSI fallback. Background thread for speedtest.

## Milestones (critical path order)
1. **M0 spike: signal backend (blocks all sampling).** Try in order: (a) `pip install pyobjc-framework-CoreWLAN` in venv + `CoreWLAN.CWInterface` poll timing; (b) tiny `swiftc` CoreWLAN helper binary; (c) `wdutil info` timing/privileges; (d) `system_profiler` as last-resort fallback. Decision: fastest unprivileged <0.5s reader. Files: `spikes/signal-*.py`, note in plan. Validation: 10 rapid polls, print RSSI/noise/BSSID/channel + latency.
2. **M1 store.** `src/wifimap/store.py`: schema (locations + readings per spec), WAL, FK on, locations CRUD, readings insert, join queries for list/export. Validation: pytest CRUD + FK violation test + join test on tmp DB.
3. **M2 signal + speed.** `signal.py` (M0 winner + slow fallback), `speed.py` (Ookla `--format=json` parse, timeout 120s, missing-binary error type). Fixtures: captured `airport -I`-style parse if kept, CoreWLAN sample, Ookla JSON sample. Validation: pytest with fixtures/mocks, no live network.
4. **M3 CLI skeleton.** `cli.py`: `scan`, `locations`, `list`, `export` (no walk yet). Location resolve (id|name, auto-create with `--location-floor/--location-outdoors`). Exit codes 0/2/3/4. Validation: pytest CLI parsing + manual `scan --no-speedtest` + `list` + `export`.
5. **M4 walk TUI.** `tui.py`: curses loop, 1s render, keys `s/l/n/f/q`, location picker + inline create, background speedtest thread + toast, `NO-WIFI` state. Validation: manual walkthrough on your Mac (multi-room + floor change), DB rows verified via `list`.
6. **M5 tests + packaging.** `pyproject.toml` (console script `wifimap`), README quickstart (incl. Ookla install cmd), full pytest run, `python -m build`? No—stdlib + pip install `-e`. Validation: clean-venv install, `wifimap --help`, all commands smoke test.

## Validation strategy
- `pytest` (fixtures/mocks, no live network) gates every milestone.
- Manual gates: M3 `scan`, M4 real walk. Acceptance: 3 rooms × 2 floors snapshot → `list` shows 6 joined rows → `export` CSV has location+floor columns.

## Risks and open questions
- Signal backend may need `pyobjc` (compiled dep, slow install) or `swiftc` (Xcode present? license just fixed, unverified). Fallback `system_profiler` too slow for 1s watch → degrades to ~5s interval.
- Ookla `speedtest` install: `brew install --cask speedtest-cli`? Verify exact formula during M2; `--no-speedtest` path must always work.
- curses on macOS Terminal: key handling for `s/l/n/f` fine, but inline text prompt in curses is fiddly → may use simple `input()` pause or minimal form. Spike during M4.
- Branch name `production` (repo default) — leave alone, single-user repo.

## Recommended first step
M0 spike (a): create venv, `pip install pyobjc-framework-CoreWLAN`, time 10 polls. If >2min friction, fall to (b) swiftc helper.

## Decision log
- M0 (2026-09-18, 3 parallel probes): **pyobjc CoreWLAN wins** — 5.4ms median poll, pip wheels fast after `pip install --upgrade pip` (stock pip 21 pulls pyobjc-core 12.0 source, fails; upgrade → 0.5s). Swift helper viable fallback (11.6ms/run, 1.5s build, 53K) but adds compiled-artifact friction. `wdutil` needs sudo (out), `system_profiler` 4.5s (fallback only). macOS 26 redacts SSID/BSSID w/o Location permission on all APIs → spec notes NULL normal. Probe notes: `spikes/probe-{a-pyobjc,b-swift,c-cli}.md`.
