---
layout: layouts/base.njk
title: Data and troubleshooting
description: Understand wifimap's SQLite and CSV data, privacy boundaries, optional integrations, exit codes, migrations, and recovery steps.
navKey: data-troubleshooting
permalink: /data-troubleshooting/index.html
---

# Data and troubleshooting

wifimap stores survey data in SQLite.

## Storage model

The default database is `db/wifi-map.db` inside the project. Override it with
the global option before a command, for example
`wifimap --db /path/to/survey.db list`. The containing directory must already
exist.

Data is organized as follows:

- A **location** is a physical site such as `HOME`.
- An **SSID** is a network name owned by one location.
- A **room** belongs to a location and carries its floor and outdoors flag.
- A **spot** is a precise point in a room, such as a desk, window, or corner.
- A **reading** belongs to a spot and references the selected SSID. Its BSSID
  stays on the reading so mesh access-point handoffs remain visible.
- An **access-point name** is an optional local alias for a BSSID.
- A **named walk** wraps the readings saved during one `walk` invocation and
  records its location, start, and end. Names may repeat; IDs are unambiguous.

Readings do not require a walk. `scan` and `add` create unknown place names and
their parents. `rooms list` and `spots list` are read-only and exit 3 for an
unknown place.

Floors use signed integers: `0` is ground level, `-1` is the first basement,
and `1` is the first upper floor. Outdoor status is a separate boolean field.
`scan` accepts `--room-outdoors 1` for outdoors and `0` for indoors;
`rooms add` uses the `--outdoors` flag.

## CSV export

`wifimap export --csv readings.csv` writes joined place, network, radio,
throughput, path, benchmark, and walk data. Every export uses the following
41-column order:

<div class="reference-block generated-reference source-reference">
  <p class="reference-label">CSV column order</p>
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

Survey data remains in SQLite or the requested export file. The integrations
below access the network or macOS services.

### CoreWLAN signal reads

Install `pyobjc-framework-CoreWLAN` for fast macOS signal polling:

```sh
python -m pip install --upgrade pip
python -m pip install pyobjc-framework-CoreWLAN
```

CoreWLAN supplies RSSI, noise, channel, PHY, TX rate, and sometimes SSID and
BSSID. A missing PyObjC installation normally exits 2. `system_profiler` is the
slower fallback. Neither path requires administrator privileges.

### WifiWand network identity

On macOS 14 and later, the optional signed WifiWand helper can return an
unredacted SSID and BSSID. It has its own Location Services permission and
returns local JSON.

Install WifiWand with Ruby 3.2 or newer, then complete its one-time setup:

```sh
gem install wifi-wand
wifiwand-macos-setup
```

wifimap searches `~/Library/Application Support/WifiWand/`. Set
`WIFIMAP_WIFIWAND_HELPER` for a custom path. Detection order is CoreWLAN,
WifiWand, then `networksetup` for SSID only. `--ssid NAME` bypasses detection.
See
[WifiWand's helper documentation](https://github.com/keithrbennett/wifiwand/blob/main/docs/MACOS_HELPER_APP_DETAILS.md)
for its security and permission model.

BSSID refreshes every 10 seconds above -70 dBm, every second near -75 dBm, and
before a save. Manual SSID selection works without WifiWand.

### Ookla throughput

The optional Ookla `speedtest` integration stores ping, download, upload, and
server identity. Install the official binary from
[speedtest.net](https://www.speedtest.net/apps/cli); the unrelated Python
`speedtest-cli` uses a different interface and is not compatible.

Ookla sees the public IP and request timing. Failures preserve the signal
reading with blank throughput fields. `--no-speedtest` disables the test. In
walk mode, `t` is temporary and `s` saves the result.

### Path and passive traffic instruments

PATH sends one ICMP probe per second to the default router and `1.1.1.1`.
Missed Internet replies are checked against `8.8.8.8`. Saved readings contain
count, median, p95, loss, and longest outage, not individual probes.

No SSID, BSSID, room, or spot is sent. External targets see the public IP and
timing. Traffic graphs read default-interface byte counters through `route` and
`netstat`; a VPN can be the default interface. They show utilization, not link
capacity.

## Exit codes

<div class="reference-block generated-reference source-reference">
  <p class="reference-label">Exit-code reference</p>
  <dl class="reference-list">
    <div><dt><code>0</code></dt><dd>Command completed successfully.</dd></div>
    <div><dt><code>2</code></dt><dd>WiFi or signal data is unavailable, including a missing signal backend with its installation hint.</dd></div>
    <div><dt><code>3</code></dt><dd>Storage, lookup, CSV, or persistence error.</dd></div>
    <div><dt><code>4</code></dt><dd>Reserved for a required missing speed-test binary. Current capture commands warn and save signal-only data instead.</dd></div>
  </dl>
</div>

## Troubleshooting

<div class="table-scroll" tabindex="0" role="region" aria-label="Troubleshooting symptoms and recovery steps">
  <table>
    <thead>
      <tr>
        <th scope="col">Symptom</th>
        <th scope="col">Cause and recovery</th>
      </tr>
    </thead>
    <tbody>
      <tr><th scope="row"><code>scan</code> exits 2 with an install hint</th><td>Upgrade pip, then install <code>pyobjc-framework-CoreWLAN</code>.</td></tr>
      <tr><th scope="row"><code>SSID selection requires a terminal</code></th><td>For non-interactive <code>scan</code> or <code>walk</code>, pass both <code>--location NAME</code> and <code>--ssid NAME</code>.</td></tr>
      <tr><th scope="row">SSID detection is blank or redacted</th><td>Run <code>wifiwand-macos-setup</code>, choose an existing SSID, or pass <code>--ssid NAME</code>. BSSID capture requires WifiWand or unredacted CoreWLAN.</td></tr>
      <tr><th scope="row"><code>Warning: … speedtest …; proceeding signal-only</code></th><td>The Ookla binary is missing or failed. The row was kept with blank throughput; use <code>--no-speedtest</code> to silence the probe.</td></tr>
      <tr><th scope="row"><code>walk</code> falls back to line-buffered keys</th><td>Curses or a TTY is unavailable, as with piped output. Use the same <code>s</code>/<code>t</code>/<code>c</code>/<code>l</code>/<code>n</code>/<code>b</code>/<code>q</code> keys followed by Enter. <code>NO_COLOR=1</code> disables ANSI colors.</td></tr>
      <tr><th scope="row"><code>Error: cannot open DB</code> or <code>cannot store reading</code> (exit 3)</th><td>Check the <code>--db</code> path and permissions. The containing directory must exist.</td></tr>
      <tr><th scope="row">CSV evaluation reports missing columns</th><td>Re-export with the current wifimap version; older or edited CSV files may not match the required schema.</td></tr>
      <tr><th scope="row">CSV evaluation reports a malformed cell</th><td>Correct the named row and field, or re-export. Blank is valid; malformed and non-finite numbers are not.</td></tr>
    </tbody>
  </table>
</div>
