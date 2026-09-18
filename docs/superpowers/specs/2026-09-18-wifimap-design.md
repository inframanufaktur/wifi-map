# WiFi Home Mapper (wifimap) — Design

Date: 2026-09-18
Status: approved (sections validated incrementally)
Goal: map home WiFi for connectivity planning. Capture signal strength + internet throughput, tag by room/location.

## Constraints
- macOS only (current machine, ARM64)
- Python (3.11+), stdlib-first
- SQLite storage
- Throughput = internet speedtest (Ookla binary, external dep)
- Mode: hybrid CLI + walk (option C) with in-walk snapshot, walkthrough-first

## 1. Architecture + CLI UX
Single Python package, no server, no daemon. DB default `~/wifi-map.db` (override `--db`).

Commands:
- `wifimap walk [--interval 1.0] [--no-speedtest] [--location X]`
  Primary mode for house walkthrough. Live RSSI/noise/SNR/BSSID/channel table, no fixed room. Keys:
  - `s` — snapshot now: prompt location (prefill last tag), signal saved instant, speedtest in background thread, DB write on completion, walk keeps refreshing
  - `l` — set default location tag (skips prompt on next `s`)
  - `q` / Ctrl-C — quit
  Walk writes DB only on `s`. `--location` presets initial tag.
- `wifimap scan --location "living-room" [--no-speedtest] [--note TEXT]`
  Single snapshot row, print, exit. For scripting only.
- `wifimap list [--location X] [--limit 50]` — history table per room.
- `wifimap export --csv out.csv [--location X]` — dump for plotting/heatmap later.

## 2. Components
- `signal.py`: CoreWLAN via PyObjC if available, fallback `airport -I` parse.
  Returns: RSSI dBm, noise dBm, SNR dB, SSID, BSSID, channel, PHY mode, txRate. Poll timeout 2s, 1s default interval.
- `speed.py`: runs `speedtest --format=json`, timeout 120s. Parses down/up Mbps, ping ms, server name/id. `--no-speedtest` skips.
- `store.py`: sqlite3, WAL mode. Append-only insert. Creates schema on first run.
- `tui.py`: stdlib-only walk loop (curses if tty, else ANSI refresh). Non-blocking key read. Background thread for speedtest so UI never blocks.
- `cli.py`: argparse dispatch, exit codes, human table print.

Interface rule: `signal.py` and `speed.py` return plain dataclasses; `store.py` accepts dataclasses; `tui.py` never touches SQL directly except via `store.py`.

## 3. Data flow + schema
Scan: parse location → poll signal once → optional speedtest → insert → print row.
Walk: live poll+render (no fixed room) → `s` prompts location (prefill last) + freezes signal copy + spawns speedtest thread → on done insert + toast → `l` sets default tag.

Table `readings`:
```sql
CREATE TABLE readings(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  location TEXT NOT NULL,
  ssid TEXT, bssid TEXT,
  rssi INTEGER, noise INTEGER, snr INTEGER,
  channel TEXT, phy TEXT, tx_rate TEXT,
  ping_ms REAL, down_mbps REAL, up_mbps REAL,
  server TEXT, note TEXT
);
```
One row per `s` / per `scan`. No updates. Query by location+time for room comparison.

## 4. Error handling
- WiFi off / no assoc → `NO-WIFI` display, `s` blocked with hint, `scan` exit 2.
- `airport` parse fail → retry 3x, then UNKNOWN fields, never crash walk.
- Speedtest timeout/fail → row stored with NULL down/up + server=ERROR, signal kept.
- DB locked / disk full → stderr + exit 3 (`scan`), walk continues, toast error.
- Missing Ookla binary → install hint, auto fallback to `--no-speedtest`.
- Exit codes: 0 ok, 2 no-wifi, 3 storage, 4 speedtest binary missing (when required).

## 5. Testing + scope
- pytest: `airport -I` fixture parsing, SNR calc, store insert/list, CLI parsing. Mock speedtest JSON. No live network in tests.
- Manual: scan, walk key flow (s/l/q), list, export on macOS.
- Out of scope: heatmap rendering, Linux support, iperf3 local test, auto location detection, daemon/long-run logging.
- Deps: Python 3.11+, Ookla `speedtest` binary only. No Textual/Rich (stdlib TUI keeps install trivial).

## Open follow-ups (post-v1)
- CSV → floorplan heatmap script
- `walk --log-all` mode for unattended sampling
- iperf3 local-throughput option
