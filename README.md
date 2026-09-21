# wifimap

Map home WiFi for connectivity planning. Walk room to room, snapshot
signal strength (+ internet throughput), tag by location/room/spot. Rows land
in SQLite; CSV export feeds plotting/heatmap later.

Model: `location` = physical site (e.g. HOME); `SSID` = a network name owned
by one location; `room` = room/place with floor + outdoors flag; `spot` =
precise point (window/bed/corner/desk). Readings and walks reference the SSID
entity; BSSID remains per reading so mesh access points stay distinguishable.

macOS only. Python 3.9+ (3.9-compatible code). Stdlib-first — no TUI
framework deps.

## Quick Start (2 min, no hardware needed)

```sh
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip   # REQUIRED: stock pip pulls pyobjc-core source and fails
python -m pip install -e '.[test]'
python -m pip install pyobjc-framework-CoreWLAN

# 1. First snapshot (signal-only, throwaway DB):
#    Uses pyobjc-framework-CoreWLAN installed above; without it scan exits 2
#    with an install hint (expected on a fresh machine).
wifimap --db /tmp/demo.db scan --no-speedtest \
  --location HOME --room KITCHEN --spot WINDOW --room-floor 0 \
  --ssid DEMO-WIFI

# 2. Named house walkthrough (live signal + passive traffic):
wifimap --db /tmp/demo.db walk --location HOME --name before-install
# keys: s snapshot+save | t unsaved throughput probe | c compare walk |
#       l switch spot | n new room+spot | f floor | b benchmark | q quit.

# After changing the infrastructure, compare live against the first walk:
wifimap --db /tmp/demo.db walk --location HOME --name after-install \
  --compare-to before-install

# 3. Review + export:
wifimap --db /tmp/demo.db list
wifimap --db /tmp/demo.db export --csv readings.csv

# 4. Evaluate in the terminal (live DB or portable export):
wifimap --db /tmp/demo.db eval
wifimap eval --csv readings.csv
```

Real DB defaults to `db/wifi-map.db` inside this project (gitignored) — pass
`--db PATH` (global flag, before the subcommand) to override. Unknown
location/room/spot names auto-create on `scan`. `--ssid NAME` selects or
creates that location's SSID without prompting; omit it in a terminal to
choose an existing SSID, confirm the automatically detected name, or enter
one manually.

Existing databases with SSID strings require the explicit one-off migration.
It makes a timestamped sibling backup before changing the local database:

```sh
.venv/bin/python scripts/migrate_ssid_entities.py db/wifi-map.db
```

Rerunning the command is safe and prints `already migrated`.

## Commands

| Command | What it does |
|---------|--------------|
| `wifimap scan --location L --room R --spot S [--ssid NAME] [--room-floor N] [--room-outdoors 0\|1] [--no-speedtest] [--note TEXT]` | Select a location SSID, save one snapshot row, print, exit |
| `wifimap walk [--interval 1.0] [--no-speedtest] [--location L] [--ssid NAME] [--name WALK] [--compare-to ID\|NAME\|latest]` | Named walkthrough with live signal, passive traffic, saved snapshots, and optional prior-walk comparison |
| `wifimap eval [--csv REPORT.csv]` | Terminal evaluation dashboard; defaults to the project DB, or reads a current-schema CSV export |
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
walk reads print the delta against it as
`(vs bench: rssi -22, snr -8, down -262.4, up -41.0 …)`.
Walk shows the benchmark in its header and keeps a persistent `last:`
line with the last speedtest result + delta. While the Ookla binary runs
(both `scan` capture and `benchmark set`), `speedtest running...` prints
to stderr so the wait is visible.

## Evaluation TUI

`wifimap eval` opens `db/wifi-map.db` read-only. Use the global database
override before the command (`wifimap --db PATH eval`), or evaluate an
immutable export with `wifimap eval --csv export/report.csv`.

Startup first asks whether to evaluate by Location or SSID, then asks for the
specific value. `Unknown` includes readings without an SSID. The dashboard
starts in `spot summary` mode with per-spot medians ranked worst-first; `m`
switches to `individual readings` mode. `Tab`/`Shift+Tab` cycles
forward/backward through RSSI/noise/SNR/TX rate/ping/down/up and
benchmark-delta metrics,
`r` reverses the ranking, arrows or `j`/`k` move, and Escape returns to
selection. Rows identify each result as `room / floor / spot`; RSSI/SNR ratings
use the same GREAT/OK/WEAK thresholds as walk mode. Enter opens the selected
spot's newest-first reading history (or the selected individual reading), with
SSID/BSSID, channel, PHY, tx rate, signal and benchmark deltas, throughput,
server, and notes.

Press `c` from the dashboard to compare two named walks. Choose the before
walk and after walk; the comparison matches physical spots, reduces repeated
reads to per-walk medians, and shows `BETTER`, `WORSE`, `NEW`, or
`NOT REVISITED`. `Tab`/`Shift+Tab` cycles the seven raw measurements, `r`
changes improvement order, `x` swaps before/after, and Enter opens both
walk histories with their exact radio and throughput data. Walks from
different locations are never paired, including when evaluation was entered
through an SSID selection.

## Named walks and live comparison

A named walk is a database record wrapping all readings saved during one
`walk` invocation. It stores the location, name, start time, and end time;
each reading points back to its walk. Names are human-facing and may repeat,
while IDs remain unambiguous. If a repeated name is used with `--compare-to`,
the command reports the matching IDs to choose from. Existing readings remain
valid but unassigned until explicitly migrated into a walk.

With `--compare-to`, the active spot shows rolling live RSSI, noise, SNR, and
TX-rate values beside the selected walk's per-spot medians. `t` runs an
asynchronous Ookla throughput probe without saving a reading; `s` measures and
saves into the current walk. Both show ping/down/up changes when finished.
Switching spots clears spot-specific live results, and overlapping speed tests
are blocked.

The continuously updating `traffic down/up` graphs are interface utilization,
not a capacity test, so they are intentionally never compared with stored
Ookla results.

CSV evaluation requires all current export columns, including the four delta
columns. Blank values are valid; missing required columns and malformed
numeric cells are reported with a nonzero exit.

Floor convention (on room): 0 ground, -1 first basement, +1 first upper.
`outdoors`: 1 = garden/balcony/etc. Note the flag shapes differ:
`scan --room-outdoors 0|1` vs `rooms add --outdoors` (store-true flag).

## Install (full detail)

```sh
python3 -m venv .venv   # or /tmp/wifimap-venv (both gitignored)
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -e '.[test]'              # for pytest
python -m pip install pyobjc-framework-CoreWLAN # real 1s signal reads on macOS
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
`--no-speedtest` (rows stored with NULL down/up). In `walk`, the live
traffic down/up meters come from default-route interface byte counters via
`netstat`/`route`; VPN tunnels (utun) count as the internet path, and
the graph is blank if netstat/route is unavailable.

## Network name (SSID/BSSID)

At `scan` / `walk` startup, wifimap selects an SSID entity belonging to the
chosen location. It tries CoreWLAN, then `networksetup`, and, if needed, makes
one privileged `wdutil info` call. Detection only suggests a choice: confirm
an existing/detected SSID or create one manually. macOS 26
may redact the name even after sudo; existing and manual choices still work,
so new captures are never stored as `Net: unknown`.

`--ssid NAME` selects or creates that SSID non-interactively and skips the
privileged detection step. `list` / `export --ssid S` filter by the joined
SSID name.

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `scan` exits 2 with install hint | PyObjC missing: `python -m pip install pyobjc-framework-CoreWLAN` (after upgrading pip) |
| `SSID selection requires a terminal` | Non-interactive scan/walk: pass both `--location NAME` and `--ssid NAME` |
| `legacy SSID schema` | Run `.venv/bin/python scripts/migrate_ssid_entities.py db/wifi-map.db`; the script creates a backup first |
| SSID detection is blank/redacted | Choose an existing SSID or create one manually; use `--ssid NAME` to skip detection/sudo |
| `Warning: … speedtest …; proceeding signal-only` | Ookla binary missing/failed — row kept with NULL down/up; pass `--no-speedtest` to silence |
| `walk` falls back to line-buffered keys | No curses/tty (e.g. piped output) — same `s/t/c/l/n/f/b/q` keys followed by Enter; `NO_COLOR=1` disables ANSI colours |
| `Error: cannot open DB` / `cannot store reading` (exit 3) | Bad `--db` path or permissions; directory must exist |

## CSV + tests

`export` columns: `id, ts, spot_id, room_id, location_id,
location_name, room_name, spot_name, floor, outdoors, ssid, bssid,
rssi, noise, snr, channel, phy, tx_rate, ping_ms, down_mbps, up_mbps,
server, note, delta_rssi, delta_snr, delta_down_mbps, delta_up_mbps,
walk_id, walk_name, walk_started_at, walk_ended_at`.
NULLs render as `-` in terminal tables, `""` in CSV. Delta columns are
reading minus the location benchmark; blank when no benchmark exists or
either value is missing.

```sh
python -m pip install -e '.[test]'
pytest   # 300 passed (verified 2026-09-21). Fixtures/mocks only — no live network in tests.
```

Manual gates (no automation): `scan`, walk key flow
(`s`/`t`/`c`/`l`/`n`/`f`/`b`/`q`), eval Location/SSID and walk-comparison
selection, `list`, `export`.

## Architecture

```
src/wifimap/  cli.py    argparse + exit codes, CSV export
              evaluation.py DB/CSV loading + analysis facade
              evaluation_models.py immutable report + metric contracts
              eval_state.py evaluation navigation state machine
              eval_tui.py terminal evaluation rendering + runtime
              ssid.py   location-scoped existing/detected/manual selector
              store.py  SQLite CRUD/query facade
              store_networks.py location, SSID + named-walk persistence
              store_schema.py schema setup + persistent record types (WAL, FK on)
              signal.py CoreWLAN backend + slow fallback + wdutil identity
              speed.py  Ookla subprocess wrapper (graceful missing-binary path)
              traffic.py default-iface byte counters (netstat/route) for live traffic
              tui.py    walk state + curses/ANSI runtime loops
              walk_picker.py curses + line-based room/spot workflows
              walk_snapshot.py signal/speed capture + benchmark workers
              walk_ui.py pure walk formatting, meters, sparklines + picker input
tests/        fixtures/mocks only, no live network
docs/         plans/ (build history) + superpowers/ (specs)
scripts/      explicit one-off local database migrations
spikes/       M0 signal-backend probes (pyobjc vs swift vs wdutil vs system_profiler)
```

Design specs: `docs/superpowers/specs/` (location/spot model, walk UI,
location-owned SSIDs).
Build plan with decision log: `docs/plans/wifimap-v1.md`.
