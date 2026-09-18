# WiFi Home Mapper (wifimap) — Design

Date: 2026-09-18
Status: approved (sections validated incrementally)
Goal: map home WiFi for connectivity planning. Capture signal strength + internet throughput, tag by room/location.

## Constraints
- macOS only (current machine, ARM64)
- Python (3.9+, 3.9-compatible code), stdlib-first
- SQLite storage
- Throughput = internet speedtest (Ookla binary, external dep)
- Mode: hybrid CLI + walk (option C) with in-walk snapshot, walkthrough-first

## 1. Architecture + CLI UX
Single Python package, no server, no daemon. DB default `~/wifi-map.db` (override `--db`).

Commands:
- `wifimap walk [--interval 1.0] [--no-speedtest] [--location ID|NAME]`
  Primary mode for house walkthrough. Live RSSI/noise/SNR/BSSID/channel table, no fixed room. Keys:
  - `s` — snapshot now: pick location from list (prefill active) or create inline (name + floor int + outdoors flag), signal saved instant, speedtest in background thread, DB write on completion, walk keeps refreshing
  - `l` — switch active location (numbered list)
  - `n` — new location inline (name + floor int + outdoors)
  - `f` — edit floor of active location
  - `q` / Ctrl-C — quit
  Walk writes DB only on `s`. `--location` presets active tag by id or name.
- `wifimap scan --location ID|NAME [--location-floor N] [--location-outdoors 0|1] [--no-speedtest] [--note TEXT]`
  Single snapshot row, print, exit. For scripting only. Unknown name auto-creates location row (floor/outdoors from flags, defaults 0/0).
- `wifimap locations [list|add]` — `list` prints id/name/floor/outdoors; `add --name X --floor N [--outdoors]` creates.
- `wifimap list [--location ID|NAME] [--floor N] [--limit 50]` — history table per room/floor (joins locations).
- `wifimap export --csv out.csv [--location ID|NAME] [--floor N]` — dump for plotting/heatmap later (location columns joined).

## 2. Components
- `signal.py`: CoreWLAN via PyObjC if available, fallback `airport -I` parse.
  Returns: RSSI dBm, noise dBm, SNR dB, SSID, BSSID, channel, PHY mode, txRate. Poll timeout 2s, 1s default interval.
- `speed.py`: runs `speedtest --format=json`, timeout 120s. Parses down/up Mbps, ping ms, server name/id. `--no-speedtest` skips.
- `store.py`: sqlite3, WAL mode, `PRAGMA foreign_keys=ON`. Owns locations CRUD + readings insert + join queries. Append-only readings. Creates schema on first run.
- `tui.py`: stdlib-only walk loop (curses if tty, else ANSI refresh). Non-blocking key read. Background thread for speedtest so UI never blocks.
- `cli.py`: argparse dispatch, exit codes, human table print.

Interface rule: `signal.py` and `speed.py` return plain dataclasses; `store.py` accepts dataclasses; `tui.py` never touches SQL directly except via `store.py`.

## 3. Data flow + schema
Scan: resolve location (id|name, auto-create with flags) → poll signal once → optional speedtest → insert → print row.
Walk: live poll+render (no fixed room) → `s` picks/creates location + freezes signal copy + spawns speedtest thread → on done insert + toast → `l` switches active, `n` creates, `f` edits active floor.

Table `locations` (one row per room/spot):
```sql
CREATE TABLE locations(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  floor INTEGER NOT NULL DEFAULT 0,
  outdoors INTEGER NOT NULL DEFAULT 0 CHECK(outdoors IN (0,1)),
  UNIQUE(name, floor)
);
```
Floor convention: integer storeys, 0 = ground, negative = basement (-1 first below), positive = upper (+1 first above). `outdoors`: 0 indoor, 1 garden/balcony/etc.

Table `readings` (one row per snapshot, FK → locations):
```sql
CREATE TABLE readings(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  location_id INTEGER NOT NULL REFERENCES locations(id),
  ssid TEXT, bssid TEXT,
  rssi INTEGER, noise INTEGER, snr INTEGER,
  channel TEXT, phy TEXT, tx_rate TEXT,
  ping_ms REAL, down_mbps REAL, up_mbps REAL,
  server TEXT, note TEXT
);
```
Note (macOS 26): SSID/BSSID redacted without Location permission → NULL is normal. Readings keyed by manual location tags, not SSID.
```
One row per `s` / per `scan`. No updates to readings; locations editable (`f`, `locations add`). Query joins floor+name+time for room/storey comparison. Positioning: manual location rows only — no GPS/elevation (Mac has no GPS chip, indoor WiFi geolocation 20-100m, no barometer; phone GPS still can't separate rooms/floors).

## 4. Error handling
- WiFi off / no assoc → `NO-WIFI` display, `s` blocked with hint, `scan` exit 2.
- `airport` parse fail → retry 3x, then UNKNOWN fields, never crash walk.
- Speedtest timeout/fail → row stored with NULL down/up + server=ERROR, signal kept.
- DB locked / disk full → stderr + exit 3 (`scan`), walk continues, toast error.
- Missing Ookla binary → install hint, auto fallback to `--no-speedtest`.
- Exit codes: 0 ok, 2 no-wifi, 3 storage, 4 speedtest binary missing (when required).

## 5. Testing + scope
- pytest: `airport -I` fixture parsing, SNR calc, locations CRUD + FK enforcement + store insert/list join, CLI parsing. Mock speedtest JSON. No live network in tests.
- Manual: scan, walk key flow (s/l/n/f/q), locations add/list, list, export on macOS.
- Out of scope: heatmap rendering, Linux support, iperf3 local test, auto location detection, daemon/long-run logging.
- Deps: Python 3.11+, Ookla `speedtest` binary only. No Textual/Rich (stdlib TUI keeps install trivial).

## Open follow-ups (post-v1)
- CSV → floorplan heatmap script
- `walk --log-all` mode for unattended sampling
- iperf3 local-throughput option
