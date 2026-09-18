# wifimap

Map home WiFi for connectivity planning. Walk room to room, snapshot
signal strength (+ internet throughput), tag by location/room/spot. Rows land
in SQLite; CSV export feeds plotting/heatmap later.

Model: `location` = physical site (bare name, e.g. HOME); `room` =
room/place with floor + outdoors flag; `spot` = precise point
(window/bed/corner/desk).

macOS only. Python 3.9+ (3.9-compatible code). Stdlib-first — no TUI
framework deps.

## Commands

- `wifimap scan --location HOME --room KITCHEN --spot WINDOW [--room-floor N] [--room-outdoors 0|1] [--no-speedtest] [--note TEXT]` —
  single snapshot row, print, exit. Unknown location/room/spot names
  auto-create (room uses `--room-floor`/`--room-outdoors`).
- `wifimap locations list|add --name X` — `list` prints id/name;
  `add --name X` creates a site.
- `wifimap rooms list|add --location HOME --name X --floor N [--outdoors]` —
  `list` prints id/name/floor/outdoors for the site; `add` creates a room.
- `wifimap spots list|add --location HOME --room KITCHEN --name X` —
  `list` prints id/name for the room; `add` creates a spot.
- `wifimap walk [--interval 1.0] [--no-speedtest] [--location HOME]` —
  live RSSI/noise/SNR/BSSID/channel table, preset site. Keys: `s` snapshot
  (room→spot drilldown), `l` switch room/spot, `n` new room/spot,
  `f` edit room floor, `q` quit. DB writes only on `s`.
- `wifimap list [--location HOME] [--room K] [--spot WINDOW] [--floor N] [--limit 50]` —
  history, joined with location/room/spot.
- `wifimap export --csv out.csv [--location HOME] [--room K] [--spot WINDOW] [--floor N]` —
  CSV dump.

DB default `~/wifi-map.db`, override with `--db PATH`.
Exit codes: 0 ok, 2 no-wifi/signal-unavailable, 3 storage,
4 speedtest binary missing (when required).

Floor convention (on room): 0 ground, -1 first basement, +1 first upper.
`outdoors`: 1 = garden/balcony/etc. Readings are keyed by manual location
tags — on macOS 26 SSID/BSSID come back NULL without Location permission;
that is normal.

## Network name via sudo wdutil

At session start (`scan` / `walk`) wifimap makes one privileged
`wdutil info` call to fill SSID/BSSID (NULL under macOS 26 redaction).
One sudo prompt at start, cached ~5min; skip/abort = `Net: unknown`,
tagging unaffected.

## Install

```sh
python3 -m venv /tmp/wifimap-venv   # or .venv in repo (gitignored)
source /tmp/wifimap-venv/bin/activate
pip install --upgrade pip           # REQUIRED on older Pythons: stock pip
                                    # pulls pyobjc-core source and fails
pip install -e .
pip install -e .[test]              # for pytest
```

Signal backend (real 1s reads):

```sh
pip install pyobjc-framework-CoreWLAN
```

Verified 2026-09-18: CoreWLAN poll ~5ms, live read
`rssi=-60 noise=-92 snr=32 ch=36 (160MHz) 802.11ac 650Mbps`.
Without PyObjC, `scan` exits 2 with an install hint
(`system_profiler` fallback is ~4s, last resort only).

Throughput (optional):

- `wifimap` shells out to the **official Ookla** `speedtest` binary with
  `--format=json` (parsed: down/up Mbps, ping ms, server name/id).
- First-run EULA/GDPR prompt is auto-accepted via `--accept-license
  --accept-gdpr` (Ookla 1.2 otherwise blocks on stdin and the scan
  stores NULLs).
- verify: official installer is linked from https://www.speedtest.net/apps/cli —
  download + install command not yet verified from this machine.
- verify: Homebrew only carries unofficial clients — `brew info
  speedtest-cli` shows sivel/speedtest-cli 2.1.3 (deprecated upstream,
  "will be disabled on 2027-01-18"), which uses different flags/output and
  does **not** match our `--format=json` parser. Do not substitute it.
- Without the Ookla binary everything still works via `--no-speedtest`
  (rows stored with NULL down/up).

## Quickstart

```sh
wifimap --help
wifimap --db /tmp/demo.db scan --no-speedtest --location home --room kitchen --spot window --room-floor 0
wifimap --db /tmp/demo.db locations list
wifimap --db /tmp/demo.db rooms list --location home
wifimap --db /tmp/demo.db spots list --location home --room kitchen
wifimap --db /tmp/demo.db list
wifimap --db /tmp/demo.db export --csv readings.csv
wifimap walk --no-speedtest --location home   # house walkthrough
```

## Smoke test (verified 2026-09-18, clean venv)

```
pip install -e . && wifimap --help                                  # ok
scan --no-speedtest --location test --room r1 --spot s1 --room-floor 0  # exit 2 without PyObjC (hint shown)
pip install pyobjc-framework-CoreWLAN                                # pyobjc 11.1, fast after pip upgrade
scan ...                                                             # exit 0: #1 test rssi=-60 snr=32
locations list / rooms list / spots list / list / export --csv       # 1 row, CSV has location+room+spot+floor columns
pytest                                                               # 140 passed
```

## Tests

```sh
pip install -e .[test]
pytest
```

Fixtures/mocks only — no live network in tests.
Manual gates: `scan`, walk key flow (`s`/`l`/`n`/`f`/`q`), `list`, `export`.
