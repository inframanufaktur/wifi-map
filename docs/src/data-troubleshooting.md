---
layout: layouts/base.njk
title: Data and troubleshooting
description: Understand wifimap's SQLite and CSV data, privacy boundaries, optional integrations, exit codes, migrations, and recovery steps.
navKey: data-troubleshooting
permalink: /data-troubleshooting/index.html
---

# Data and troubleshooting

wifimap stores survey data locally and keeps optional integrations
failure-tolerant wherever a useful signal-only reading can still be saved.
This page documents the storage contract, data leaving the machine, and the
first recovery steps when a command cannot collect or persist a reading.

## Storage model

The default database is `db/wifi-map.db` inside the project. Override it with
the global option before a command, for example
`wifimap --db /path/to/survey.db list`. The containing directory must already
exist.

The place and network model is deliberately normalized:

- A **location** is a physical site such as `HOME`.
- An **SSID** is a network name owned by one location.
- A **room** belongs to a location and carries its floor and outdoors flag.
- A **spot** is a precise point in a room, such as a desk, window, or corner.
- A **reading** belongs to a spot and references the selected SSID. Its BSSID
  stays on the reading so mesh access-point handoffs remain visible.
- An **access-point name** is an optional local alias for a BSSID.
- A **named walk** wraps the readings saved during one `walk` invocation and
  records its location, start, and end. Names may repeat; IDs are unambiguous.

Existing readings remain valid when they are not assigned to a walk. Unknown
place names passed to `scan` and the `add` commands are created with their
parents. The read-only `rooms list` and `spots list` commands never create
unknown places and exit with code 3 instead.

Floor `0` is ground level, `-1` is the first basement, and `+1` is the first
upper floor. Outdoors `1` means a garden, balcony, or similar outside spot.
The creation flags intentionally differ: `scan --room-outdoors 0|1` accepts a
value, while `rooms add --outdoors` is a boolean flag.

## CSV export contract

`wifimap export --csv readings.csv` writes joined place, network, radio,
throughput, path, benchmark, and walk data. The following 41-column order is
the exact `_EXPORT_FIELDS` sequence assembled by the current application
source:

<div class="reference-block generated-reference source-reference">
  <p class="reference-label">Source-backed CSV reference</p>
  <pre><code>id,ts,spot_id,room_id,location_id,location_name,room_name,spot_name,floor,outdoors,ssid,bssid,rssi,noise,snr,channel,phy,tx_rate,ping_ms,down_mbps,up_mbps,server,note,delta_rssi,delta_snr,delta_down_mbps,delta_up_mbps,ap_name,path_probe_count,gateway_rtt_ms,gateway_p95_ms,gateway_loss_pct,gateway_max_outage_ms,internet_rtt_ms,internet_p95_ms,internet_loss_pct,internet_max_outage_ms,walk_id,walk_name,walk_started_at,walk_ended_at</code></pre>
</div>

`bssid` is the access point observed for that snapshot; `ap_name` is its
optional alias. NULL values render as `-` in terminal tables and as an empty
quoted cell in CSV. The four delta fields are reading minus location benchmark
and stay blank when no benchmark exists or either input is missing.

CSV evaluation requires the current core fields through
`delta_up_mbps`. Access-point, path, and walk fields are accepted as optional
extensions. Blank values are valid. Missing required columns, non-finite or
malformed numbers, and invalid integers produce a nonzero exit instead of
being silently coerced.

## Privacy and external integrations

Survey structure, readings, aliases, and exports remain in the local SQLite
database or the file path you explicitly choose. Optional tools add the
following boundaries.

### CoreWLAN signal reads

Install `pyobjc-framework-CoreWLAN` for fast macOS signal polling:

```sh
python -m pip install --upgrade pip
python -m pip install pyobjc-framework-CoreWLAN
```

CoreWLAN supplies RSSI, noise, channel, PHY, TX rate, and sometimes SSID and
BSSID. Without PyObjC, `scan` normally exits 2 with an install hint; the much
slower `system_profiler` path is only a last-resort fallback. These reads are
local and wifimap does not request administrator privileges.

### WifiWand network identity

On macOS 14 and later, the optional signed WifiWand helper is the preferred
way to obtain an unredacted SSID and current BSSID. The helper owns its own
Location Services permission, runs only when queried, returns local JSON, and
does not send or persist the result on wifimap's behalf.

Install WifiWand with Ruby 3.2 or newer, then complete its one-time setup:

```sh
gem install wifi-wand
wifiwand-macos-setup
```

wifimap finds the newest helper under
`~/Library/Application Support/WifiWand/`. Set
`WIFIMAP_WIFIWAND_HELPER` to an executable for a custom installation. Identity
detection tries unredacted CoreWLAN first, then WifiWand, then `networksetup`
as an SSID-only fallback. Detection only suggests a choice; `--ssid NAME`
skips it and selects or creates the location's SSID non-interactively. See
[WifiWand's helper documentation](https://github.com/keithrbennett/wifiwand/blob/main/docs/MACOS_HELPER_APP_DETAILS.md)
for the helper's own security and permission model.

During a walk, BSSID refreshes every 10 seconds when RSSI is stronger than
-70 dBm, every second near Apple's -75 dBm roaming threshold, and immediately
before a save. Without WifiWand, manual SSID selection and all available
signal measurements continue to work.

### Ookla throughput

Throughput is optional and invokes the official Ookla `speedtest` executable
with JSON output plus its license and GDPR acceptance flags. It stores ping,
download, upload, and server identity. The official installer is linked from
[speedtest.net](https://www.speedtest.net/apps/cli); the unrelated Python
`speedtest-cli` uses a different interface and is not compatible.

An Ookla test contacts external infrastructure, which can observe the public
source IP and request timing. If the executable is missing or fails, wifimap
warns and keeps a signal-only row with blank throughput values. Use
`--no-speedtest` to skip it intentionally. The transient `t` probe in walk
mode is not saved; a snapshot made with `s` is saved.

### Path and passive traffic instruments

The walk PATH instrument sends small ICMP probes once per second to the local
default router and Cloudflare's `1.1.1.1`. A missed Internet response is
checked against Google's `8.8.8.8`, so a displayed failure means both external
targets missed. The saved reading contains only aggregate count, median, p95,
loss, and longest outage—not every sample.

These are not DNS queries and wifimap does not send the SSID, BSSID, room, or
spot. External targets can still observe the public source IP and timing; the
router probe remains on the local network. Live traffic graphs read local
default-interface byte counters through `route` and `netstat`. They measure
utilization, not link capacity, count a VPN tunnel when it is the default
route, and remain blank when those tools are unavailable.

## Exit codes

<div class="reference-block generated-reference source-reference">
  <p class="reference-label">Application exit-code reference</p>
  <dl class="reference-list">
    <div><dt><code>0</code></dt><dd>Command completed successfully.</dd></div>
    <div><dt><code>2</code></dt><dd>WiFi or signal data is unavailable, including a missing signal backend with its installation hint.</dd></div>
    <div><dt><code>3</code></dt><dd>Storage, lookup, CSV, or persistence error.</dd></div>
    <div><dt><code>4</code></dt><dd>Reserved for a required missing speed-test binary. Current capture commands warn and save signal-only data instead.</dd></div>
  </dl>
</div>

## Migrate a legacy SSID database

Databases that still store SSIDs as strings require an explicit one-time
migration. The script creates a timestamped sibling backup before changing the
database:

```sh
.venv/bin/python scripts/migrate_ssid_entities.py db/wifi-map.db
```

Rerunning it is safe and prints `already migrated`. Keep the backup until the
migrated database has passed your normal `list`, `export`, walk, and evaluation
checks.

## Troubleshooting

| Symptom | Cause and recovery |
| --- | --- |
| `scan` exits 2 with an install hint | Upgrade pip, then install `pyobjc-framework-CoreWLAN`. |
| `SSID selection requires a terminal` | For non-interactive `scan` or `walk`, pass both `--location NAME` and `--ssid NAME`. |
| `legacy SSID schema` | Run `.venv/bin/python scripts/migrate_ssid_entities.py db/wifi-map.db`; it creates a backup first. |
| SSID detection is blank or redacted | Run `wifiwand-macos-setup`, choose an existing SSID, or pass `--ssid NAME`. BSSID capture requires WifiWand or unredacted CoreWLAN. |
| `Warning: … speedtest …; proceeding signal-only` | The Ookla binary is missing or failed. The row was kept with blank throughput; use `--no-speedtest` to silence the probe. |
| `walk` falls back to line-buffered keys | Curses or a TTY is unavailable, as with piped output. Use the same `s`/`t`/`c`/`l`/`n`/`b`/`q` keys followed by Enter. `NO_COLOR=1` disables ANSI colors. |
| `Error: cannot open DB` or `cannot store reading` (exit 3) | Check the `--db` path and permissions. The containing directory must exist. |
| CSV evaluation reports missing columns | Re-export with the current wifimap version; older or edited CSV files may not match the required schema. |
| CSV evaluation reports a malformed cell | Correct the named row and field, or re-export. Blank is valid; malformed and non-finite numbers are not. |

## Contributor checks

Install test dependencies, then run the complete suite and the documentation
contracts:

```sh
python -m pip install -e '.[test]'
pytest
pytest tests/test_docs.py

npm ci
npm run docs:build
```

Tests use fixtures and mocks and do not require live network access. Manual
release gates remain `scan`, the complete walk key flow
(`s`/`t`/`c`/`l`/`n`/`b`/`q`), evaluation by Location and SSID including walk
comparison, `list`, and `export`.

## Architecture map

```text
src/wifimap/  cli.py    argparse, live commands + dispatch facade
              cli_common.py shared exit codes + command helpers
              cli_storage_commands.py storage, export + benchmark commands
              evaluation.py analysis + comparison facade
              evaluation_io.py CSV/SQLite report loading
              evaluation_models.py immutable report + metric contracts
              eval_render_common.py shared formatting + selector screens
              eval_render_dashboard.py dashboard + reading-detail views
              eval_state.py evaluation navigation state machine
              eval_tui.py comparison-detail rendering + terminal runtime
              path_monitor.py rolling gateway/Internet ICMP path probes
              ssid.py   location-scoped existing/detected/manual selector
              store.py  SQLite CRUD/query facade
              store_benchmarks.py benchmark persistence + delta formatting
              store_networks.py location, SSID + named-walk persistence
              store_places.py location/room/spot hierarchy persistence
              store_readings.py reading persistence + joined queries
              store_schema.py schema setup + persistent record types (WAL, FK on)
              signal.py CoreWLAN backend + network/address identity
              signal_models.py signal readings + backend errors
              signal_profiler.py slow system_profiler fallback
              wifiwand.py signed-helper discovery, JSON validation + identity cache
              speed.py  Ookla subprocess wrapper (graceful missing-binary path)
              traffic.py default-iface byte counters (netstat/route) for live traffic
              tui.py    compatibility facade + walk-mode entry point
              walk_baseline.py prior-walk resolution + per-spot aggregation
              walk_curses.py curses renderer + input loop
              walk_fallback.py plain ANSI renderer + input loop
              walk_keys.py shared walk controls + timing constants
              walk_picker.py curses + line-based room/spot workflows
              walk_session.py comparison display + named-session lifecycle
              walk_snapshot.py signal/speed capture + benchmark workers
              walk_state.py mutable session state + worker coordination
              walk_ui.py pure walk formatting, meters, sparklines + picker input
tests/        fixtures/mocks only, no live network
scripts/      explicit one-off local database migrations
spikes/       M0 signal-backend probes
```
