---
layout: layouts/base.njk
title: Walk TUI
description: Walk TUI controls, measurements, snapshots, benchmarks, and comparison behavior.
navKey: walk-tui
permalink: /walk-tui/index.html
---

# Walk TUI

`walk` displays live measurements and saves readings at named spots.

<pre class="command-block" data-copy><code>wifimap walk --location HOME --ssid MY-WIFI --name before-install</code></pre>

The line-buffered fallback uses the same controls followed by Enter.

## Walk controls

<section class="generated-reference" aria-labelledby="walk-controls-title">
  <div class="generated-reference__header">
    <h3 id="walk-controls-title">Keyboard shortcuts</h3>
  </div>
  <dl class="key-reference">
  {% for control in capabilities.walk.controls %}
    <div class="key-reference__item">
      <dt><kbd>{{ control.key }}</kbd> <span>{{ control.name | replace("_", " ") }}</span></dt>
      <dd>
      {% if control.name == "snapshot" %}
        Measure and save a snapshot for the active spot.
      {% elif control.name == "speed_probe" %}
        Run an unsaved throughput probe for the active spot.
      {% elif control.name == "compare" %}
        Choose a previous named walk as the live comparison, or turn comparison off.
      {% elif control.name == "benchmark" %}
        Capture the location's ideal-conditions benchmark.
      {% elif control.name == "switch" %}
        Switch to an existing location, room, and spot.
      {% elif control.name == "new" %}
        Create and select a room and spot without leaving the walk.
      {% elif control.name == "quit" %}
        Finish the named walk and leave the TUI.
      {% else %}
        Perform this walk action.
      {% endif %}
      </dd>
    </div>
  {% endfor %}
  </dl>
  <div class="generated-reference__sample">
    <p class="instrument-label">CURRENT COMMAND BAR</p>
  {% for footer in [capabilities.walk.footer] %}
    <pre><code>{{ footer }}</code></pre>
  {% endfor %}
  </div>
</section>

### Room and spot picker controls

Move with `↑`/`↓` or `j`/`k`. Select with `Enter`; return with `Escape` or `q`.
Number keys select numbered rows. `+` creates a room or spot.

## Live signal

`SIGNAL` samples RSSI, noise, and SNR about once per second. RSSI and SNR use
the rating thresholds below. Missing values display as `UNKNOWN`. `NO-WIFI`
blocks saves while the Mac is disconnected.

## Link details

`LINK` shows channel, PHY, transmit rate, MCS, band, security, local IP,
default router, and interface address. Link rate is not Internet throughput.

## Network path

`PATH` probes the default router and `1.1.1.1` once per second. A missed
Internet reply is retried against `8.8.8.8`; `×` means both targets missed.
The display includes latency, p95, loss, and outages.

Changing spots clears the live path window. Saved readings contain aggregate
count, median, p95, loss, and longest outage. Internet targets see the public
IP and probe timing, but receive no survey data.

## Passive traffic

`TRAFFIC` reads default-interface byte counters through `netstat` and `route`.
A VPN may become the default interface. The graph is blank when counters are
unavailable. Traffic is utilization, not capacity; it does not start a speed
test.

## Access points

`AP` shows the BSSID and its local alias. Identity refreshes every ten seconds
above -70 dBm, every second near -75 dBm, and before each save. Manage aliases
with the `aps` commands.

## Location benchmark

A location stores one benchmark, normally captured near the router. It contains
RSSI, SNR, download, upload, note, server, and capture time. Replacing it
requires confirmation. Later readings show their deltas from the benchmark.

## Snapshots and throughput

`s` saves the active network, AP, radio, link, path aggregates, walk, and
optional throughput result. `t` runs an unsaved throughput probe.

`--no-speedtest` records signal without Ookla. A failed speed test produces a
warning and preserves the signal reading. Snapshot, benchmark, and throughput
jobs do not run concurrently.

## Rooms and spots

Each reading belongs to a spot, room, and location. Floor `0` is ground level;
negative floors are basements. Switching spots clears comparison, unsaved
throughput, and path data.

SSIDs belong to locations. `--ssid NAME` selects or creates one without a
prompt.

## Prior-walk comparison

`--compare-to ID|NAME|latest` loads a walk from the same location. Repeated
baseline readings are reduced to a median for each spot. The comparison shows
RSSI, noise, SNR, transmit rate, ping, download, and upload.

Duplicate walk names require ID selection. Spots absent from the baseline show
no baseline instead of zero.

<pre class="command-block" data-copy><code>wifimap walk --location HOME --name after-install \
  --compare-to before-install</code></pre>

Completed walks can also be compared in [evaluation](/evaluate/).

## Signal quality ratings

<section class="generated-reference" aria-labelledby="walk-ratings-title">
  <h3 id="walk-ratings-title">Rating thresholds</h3>
  <div class="table-scroll" tabindex="0" role="region" aria-label="Walk rating thresholds">
    <table>
      <thead>
        <tr>
          <th scope="col">Metric</th>
          <th scope="col">GREAT</th>
          <th scope="col">OK</th>
          <th scope="col">WEAK</th>
        </tr>
      </thead>
      <tbody>
      {% for threshold in capabilities.walk.status_thresholds %}
        <tr>
          <th scope="row">{{ threshold.metric | upper }}</th>
          <td>at least {{ threshold.great_min }} {{ threshold.unit }}</td>
          <td>at least {{ threshold.ok_min }} {{ threshold.unit }}, below {{ threshold.great_min }} {{ threshold.unit }}</td>
          <td>below {{ threshold.ok_min }} {{ threshold.unit }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</section>
