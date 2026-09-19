# wifimap

Map home WiFi for connectivity planning. Walk room to room, snapshot
signal strength (+ internet throughput), tag by location/room/spot. Rows land
in SQLite; CSV export feeds plotting/heatmap later.

Model: `location` = physical site (e.g. HOME); `room` = room/place with
floor + outdoors flag; `spot` = precise point (window/bed/corner/desk).

macOS only. Python 3.9+ (3.9-compatible code). Stdlib-first — no TUI
framework deps.

## Quick Start (2 min, no hardware needed)

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip   # REQUIRED: stock pip pulls pyobjc-core source and fails
pip install -e .[test]

# 1. First snapshot (signal-only, throwaway DB):
#    Requires pyobjc-framework-CoreWLAN above; without it scan exits 2
#    with an install hint (expected on a fresh machine).
wifimap --db /tmp/demo.db scan --no-speedtest \
  --location HOME --room KITCHEN --spot WINDOW --room-floor 0

# 2. House walkthrough (live RSSI/noise/SNR table):
wifimap --db /tmp/demo.db walk --no-speedtest --location HOME
# keys: s snapshot (room→spot drilldown, Enter confirms) | b benchmark |
#       l switch | n new room+spot | f edit floor | q quit. DB writes only on s.

# 3. Review + export:
wifimap --db /tmp/demo.db list
wifimap --db /tmp/demo.db export --csv readings.csv
```

Real DB defaults to `~/wifi-map.db` — pass `--db PATH` (global flag,
before the subcommand) to override. Unknown location/room/spot names
auto-create on `scan`.

## Commands

| Command | What it does |
|---------|--------------|
| `wifimap scan --location L --room R --spot S [--room-floor N] [--room-outdoors 0\|1] [--no-speedtest] [--note TEXT]` | Single snapshot row, print, exit |
| `wifimap walk [--interval 1.0] [--no-speedtest] [--location L] [--ssid NAME]` | Live RSSI/noise/SNR/BSSID/channel table + snapshot keys above |
| `wifimap locations list` / `add --name X` | List sites / create one (prints id) |
| `wifimap rooms list --location L` / `add --location L --name X --floor N [--outdoors]` | List / create rooms in a site |
| `wifimap spots list --location L --room R` / `add --location L --room R --name X` | List / create spots in a room |
| `wifimap list [--location L] [--room R] [--spot S] [--floor N] [--ssid S] [--limit 50]` | History, joined with location/room/spot (newest first) |
| `wifimap export --csv out.csv [same filters as list]` | CSV dump (up to 1M rows) |
| `wifimap benchmark set --location L [--no-speedtest] [--note T] [--force]` / `show` / `clear` | Capture/show/delete ideal-conditions reference (one per location, overwrite prompts) |

All location/room/spot arguments accept ID or NAME. Filters accept ID or NAME.
`scan` and `add` auto-create unknown names (parents included);
`rooms list` / `spots list` never write — unknown names exit 3.

Exit codes: 0 ok, 2 no-wifi/signal-unavailable (missing signal backend
maps here with an install hint), 3 storage error. Code 4 is reserved —
a missing speedtest binary never exits nonzero; the scan warns and stores
a signal-only row instead.

Benchmarks: capture an ideal-conditions reference reading per location
(e.g. right next to the router) with `benchmark set`; later snapshots and
walk reads print the delta against it as `(vs bench: +5dBm/-12Mbps …)`.
Walk shows the benchmark in its header and keeps a persistent `last:`
line with the last speedtest result + delta. While the Ookla binary runs
(both `scan` capture and `benchmark set`), `speedtest running...` prints
to stderr so the wait is visible.

Floor convention (on room): 0 ground, -1 first basement, +1 first upper.
`outdoors`: 1 = garden/balcony/etc. Note the flag shapes differ:
`scan --room-outdoors 0|1` vs `rooms add --outdoors` (store-true flag).

## Install (full detail)

```sh
python3 -m venv .venv   # or /tmp/wifimap-venv (both gitignored)
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
pip install -e .[test]              # for pytest
pip install pyobjc-framework-CoreWLAN   # real 1s signal reads on macOS
```

Signal backend: CoreWLAN poll ~5ms, live read
`rssi=-60 noise=-92 snr=32 ch=36 (160MHz) 802.11ac 650Mbps`.
Without PyObjC, `scan` exits 2 with an install hint
(`system_profiler` fallback is ~4s, last resort only).

Throughput (optional) shells out to the **official Ookla** `speedtest`
binary with `--format=json` (parsed: down/up Mbps, ping ms, server
name/id). First-run EULA/GDPR prompt is auto-accepted via
`--accept-license --accept-gdpr` (Ookla 1.2 otherwise blocks on stdin and
the scan stores NULLs). Verify: official installer is linked from
https://www.speedtest.net/apps/cli — download + install command not yet
verified from this machine. Homebrew only carries unofficial clients —
`brew info speedtest-cli` shows sivel/speedtest-cli 2.1.3 (deprecated
upstream, "will be disabled on 2027-01-18"), which uses different
flags/output and does **not** match our `--format=json` parser. Do not
substitute it. Without the Ookla binary everything still works via
`--no-speedtest` (rows stored with NULL down/up).

## Network name (SSID/BSSID)

At session start (`scan` / `walk`) wifimap makes one privileged
`wdutil info` call to fill SSID/BSSID. One sudo prompt at start, cached
~5min; skip/abort = `Net: unknown`, tagging unaffected. On macOS 26
SSID/BSSID come back NULL without Location permission on all APIs —
that is normal, readings are keyed by your manual location tags.
`walk --ssid NAME` forces a session SSID (shown as `Net: NAME (manual)`);
`list`/`export --ssid S` filter by stored SSID.

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `scan` exits 2 with install hint | PyObjC missing: `pip install pyobjc-framework-CoreWLAN` (after `pip install --upgrade pip`) |
| `Net: unknown`, SSID/BSSID `-` | sudo skipped or macOS 26 redaction — normal; use `--ssid NAME` or keep manual tags |
| `Warning: … speedtest …; proceeding signal-only` | Ookla binary missing/failed — row kept with NULL down/up; pass `--no-speedtest` to silence |
| `walk` falls back to `s/l/n/f/q + Enter` mode | No curses/tty (e.g. piped output) — same keys, line-buffered; `NO_COLOR=1` disables ANSI colours |
| `Error: cannot open DB` / `cannot store reading` (exit 3) | Bad `--db` path or permissions; directory must exist |

## CSV + tests

`export` columns: `id, ts, spot_id, room_id, location_id,
location_name, room_name, spot_name, floor, outdoors, ssid, bssid,
rssi, noise, snr, channel, phy, tx_rate, ping_ms, down_mbps, up_mbps,
server, note`. NULLs render as `-` in terminal tables, `""` in CSV.

```sh
pip install -e .[test]
pytest   # 140 passed (verified 2026-09-18, clean venv). Fixtures/mocks only — no live network in tests.
```

Manual gates (no automation): `scan`, walk key flow (`s`/`l`/`n`/`f`/`q`),
`list`, `export`.

## Architecture

```
src/wifimap/  cli.py    argparse + exit codes, CSV export
              store.py  SQLite (locations→rooms→spots→readings, WAL, FK on)
              signal.py CoreWLAN backend + slow fallback + wdutil identity
              speed.py  Ookla subprocess wrapper (graceful missing-binary path)
              tui.py    curses walk loop + ANSI fallback, snapshot worker thread
tests/        fixtures/mocks only, no live network
docs/         plans/ (build history) + superpowers/ (specs)
spikes/       M0 signal-backend probes (pyobjc vs swift vs wdutil vs system_profiler)
```

Design specs: `docs/superpowers/specs/` (location/spot model, walk UI).
Build plan with decision log: `docs/plans/wifimap-v1.md`.
