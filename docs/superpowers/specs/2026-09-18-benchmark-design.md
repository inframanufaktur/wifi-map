# Location benchmark (wifimap) — Design

Date: 2026-09-18
Status: approved
Goal: capture ideal conditions per location (close to router / ethernet), compare every snapshot vs benchmark, surface speedtest during walk.

Decisions from user: location-only scope; full snapshot (signal + speedtest + note); deltas on RSSI/SNR + down/up; overwrite prompts y/N; walk shows dedicated last-result line.

## 1. Storage

- New `benchmarks(location_id INTEGER PRIMARY KEY REFERENCES locations(id), ts, ssid, bssid, rssi, noise, snr, channel, phy, tx_rate, ping_ms, down_mbps, up_mbps, server, note)`.
- Created via `CREATE TABLE IF NOT EXISTS` in `_SCHEMA`, same WAL/FK path as existing tables.
- One row per location enforced by PK. Overwrite = upsert after confirm.
- `store.py` additions: `get_benchmark(conn, location_id) -> Optional[dict]`, `set_benchmark(conn, location_id, **fields) -> None` (upsert), `format_benchmark_delta(cur: dict, bench: dict) -> str` (NULL-safe: skips missing fields, renders e.g. `rssi -22, snr -8, down -262.4, up -41.0`).

## 2. CLI

- `wifimap benchmark set --location L [--no-speedtest] [--note T] [--force]`: live `read_signal()` + `run_speedtest()` (same pipeline/error paths as `scan`: exit 2 on no-WiFi, signal-only warning on missing speedtest binary). Existing row without `--force` prints old benchmark and prompts `Overwrite benchmark for L? [y/N]`; non-y aborts exit 0, row kept.
- `wifimap benchmark show --location L`: prints benchmark row or exit 3 `no benchmark for L`.
- `wifimap benchmark clear --location L`: deletes row (prompt y/N unless `--force`).
- `scan` output appends delta when benchmark exists: `#id L/R/S rssi=X snr=Y down=D up=U (vs bench: rssi Δ, snr Δ, down Δ, up Δ)`.
- Exit codes unchanged: 0 ok, 2 no-WiFi, 3 storage/unknown location.

## 3. Walk TUI

- Key `b` captures benchmark for `active_location_id` (toast `no preset location` when unset). Freezes current `Signal`, runs speedtest worker, prompts note via `_prompt_curses` / `input()`. Overwrite asks y/N in same prompt layer; abort toasts `benchmark kept`.
- Key help line becomes `s snapshot | b benchmark | l switch | n new | f floor | q quit` (curses + fallback).
- Snapshot `s` result: toast `saved #N (vs bench ...)` plus persistent `last:` line below `loc:` row: `last: rssi -67 (bench -45 Δ-22) down 48.1 (bench 310.5 Δ-262.4) up ...`. Line persists across polls until next snapshot.
- Header shows `bench: rssi -45 snr 32 down 310.5 up 48.2` when benchmark exists for active location; omitted otherwise.
- `WalkState` gains `last_result: str` + `benchmark: Optional[dict]` (lock-guarded like toast); worker callback fills both via `on_snapshot_done`.

## 4. Error handling

- No benchmark: no delta, no `bench:`/`last:` lines, no crash.
- NO-WIFI blocks `b` same as `s` (`b` blocked toast).
- Speedtest missing/failed: signal-only benchmark + `(signal-only)` notice, same as scan/walk snapshots.
- DB open/store failure: CLI exit 3; walk toast `DB error`, loop continues.
- Blank `--note` allowed (NULL); blank `--ssid` still rejected as today.

## 5. Testing

- Store: upsert overwrites single row per location; FK violation on unknown location; `get_benchmark` None when missing.
- Delta formatter: full fields, NULL fields skipped, zero-delta renders `+0`.
- CLI: `benchmark set` new + overwrite y/N + `--force`; `show` missing exits 3; `scan` appends delta only when benchmark exists.
- TUI: `b` blocked on NO-WIFI; `last_result` set by `on_snapshot_done` with delta; header omitted without benchmark.
- All fixtures/mocks only, no live network. Existing suite keeps passing.
