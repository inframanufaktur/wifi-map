# Walk SSID override + readable TUI (wifimap) — Design

Date: 2026-09-18
Status: approved
Goal: one walk monitors one network config. Manual `--ssid` replaces broken auto-detect. TUI shows big readings, good/bad rating, 60s graphs.

Decisions from user: `--ssid` = override live display + tag all snapshots; graphs = RSSI + noise + SNR; rating = standard thresholds; `list`/`export` get `--ssid` filter; no multi-network walks.

## 1. CLI

- `walk --ssid NAME`: session override for whole walk. Empty string rejected (`--ssid` must be non-blank).
- `list --ssid NAME`, `export --ssid NAME`: filter saved rows by ssid.
- `scan` untouched. No schema migration (readings.ssid col already exists).

## 2. State + data flow

- `WalkState.ssid_override: Optional[str]`, set from CLI at startup.
- When set: skip `ensure_identity` auto-detect (or run but override wins); header shows `Net: NAME (manual)`.
- Backfill precedence: override > poll value. Snapshot freeze forces `sig.ssid = override`.
- `store.list_readings(..., ssid=None)`: adds `r.ssid = ?` clause; index on readings(sid) if cheap (`CREATE INDEX IF NOT EXISTS idx_readings_ssid`).
- CSV export already includes ssid field; filter applies before dump.

## 3. TUI layout (curses + fallback)

- Row 0 (big): `RSSI -55 dBm [GREAT]` — largest element, rating in brackets.
- Row 1: `SNR 38 dB [GREAT]  noise -93 dBm  ch 36  phy ax  tx 240`.
- Row 2: `Net: NAME (manual)  loc: #1 den (floor 0)  pending: 0`.
- Rows 3-5: 60s history graphs, one line each:
  - `RSSI ▁▂▄▅▇ [60s]`, `SNR ...`, `noise ...`
  - Fixed scales: RSSI -90..-30, SNR 0..40, noise -100..-60. UNKNOWN renders as gap (space).
  - Width-truncated to terminal; skipped entirely when height < 10.
- Fallback (no curses): prints same lines + same sparkline strings each poll.
- Keys line + toast unchanged.

## 4. Pure helpers (unit-tested)

- `rate_rssi(v) -> GREAT|OK|WEAK|UNKNOWN`: >=-60 great, >=-70 ok, else weak, None unknown.
- `rate_snr(v) -> GREAT|OK|WEAK|UNKNOWN`: >=25 great, >=15 ok, else weak, None unknown.
- `SparkHistory`: `append(v)`, `sparkline(lo, hi, width)` using `▁▂▃▄▅▆▇█`. Gap char for None.
- `WalkState` keeps 3 histories (rssi/noise/snr), capped by `ceil(60/interval)` samples.
- History cap derived from walk interval; interval<=0 still rejected as today.

## 5. Error handling

- `--ssid ""` / blank → exit 3 with message (storage/cli usage error path, same as bad interval).
- Auto-detect failure + override set → no warning toast (override is authoritative).
- No auto-detect + no override → current `Net: unknown` behavior unchanged.
- UNKNOWN signal → big line shows `UNKNOWN`, graphs gap, no crash.
- DB errors → existing toast/continue paths unchanged.

## 6. Testing

- Unit: override wins backfill; snapshot payload carries override; sparkline fixed vectors incl. gaps/truncation; rating boundaries; ssid filter SQL.
- Manual: curses walk with/without `--ssid`; fallback same; `list/export --ssid` grouping check.
- Full `pytest tests/` green.
