---
layout: layouts/base.njk
title: Walk TUI
description: Understand every wifimap walk control, live instrument, snapshot, benchmark, and prior-walk comparison.
navKey: walk-tui
permalink: /walk-tui/index.html
---

# Survey a route in the walk TUI

Walk mode keeps the measurements you need in one keyboard-driven view. Select a
location, network, room, and spot; watch the live instruments settle; then save
a reading before moving on. Give the walk a name when you want its readings to
act as a repeatable baseline.

<pre class="command-block" data-copy><code>wifimap walk --location HOME --ssid MY-WIFI --name before-install</code></pre>

The full-screen curses view and the line-buffered fallback expose the same core
actions. In the fallback, type a control key and press Enter.

## Controls generated from the application

<section class="generated-reference" aria-labelledby="walk-controls-title">
  <div class="generated-reference__header">
    <h3 id="walk-controls-title">Current walk controls</h3>
    <p>Generated from {{ capabilities.package.name }} {{ capabilities.package.version }}</p>
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
        Application-defined walk action.
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

The command bar is live reference data rather than authored documentation. If a
control changes in the application, the next docs build renders the new key and
footer.

### Room and spot picker controls

Picker screens use `↑`/`↓` or `j`/`k` to move, `Enter` to select, and `Escape`
or `q` to return. When numbered choices are shown, a number selects that item;
`+` starts the create-room or create-spot flow. The line-buffered fallback uses
the same letters and numbered choices followed by Enter.

## Live signal

The `SIGNAL` instrument samples RSSI, noise, and signal-to-noise ratio (SNR)
about once per second. Each metric has a short history chart, so a momentary dip
is visible instead of being lost behind the latest number. RSSI and SNR include
plain-text quality ratings; noise is reported without a quality label.

A missing reading appears as `UNKNOWN`. If WiFi is off or the Mac is not
associated, the dashboard remains readable but saving is blocked with a
specific `NO-WIFI` message.

## Link details

The `LINK` rail describes the current association: channel, PHY, transmit rate,
MCS, band, and security. Where available, the TUI also shows the Mac's local IP,
default router, and interface address. These are live connection facts, not a
throughput test; a high link rate does not guarantee equivalent Internet speed.

## Network path

The `PATH` instrument sends small ICMP probes to the default router and an
Internet target once per second. It shows current latency, rolling p95 latency,
loss, outage history, and a compact sparkline for each path. If Cloudflare's
`1.1.1.1` misses a reply, wifimap checks Google's `8.8.8.8`; a `×` means both
independent Internet targets missed.

Changing spots resets the live path window. Saving freezes only the aggregate
probe count, median, p95, loss, and longest outage into the reading—not every
one-second sample. The router probe stays on the local network. Internet probe
operators can observe the public source IP and timing, but the ICMP requests do
not contain the SSID, BSSID, room, or spot.

## Passive traffic

`TRAFFIC` graphs the default-route interface's current download and upload use.
It reads byte counters through macOS `netstat` and `route`; a VPN interface can
therefore become the measured path. If those tools are unavailable, the graph
is left blank.

Traffic is utilization, not capacity. It is never compared with a stored Ookla
result, and watching it does not start a speed test.

## Access points

The `AP` line shows the current BSSID and its optional local alias, helping you
recognize which router or mesh node serves the active spot. wifimap refreshes
the identity every ten seconds while signal is stronger than -70 dBm, every
second near the -75 dBm roaming threshold, and immediately before a save. This
captures handoffs without querying the optional WifiWand helper on every frame.

Without WifiWand, an unredacted CoreWLAN identity or a manually selected SSID is
still enough to survey signal. You can assign friendly AP names with the `aps`
commands outside the walk.

## Location benchmark

A benchmark is one ideal-conditions reference per location—typically captured
close to the router. The benchmark panel shows its date, RSSI, SNR, download,
upload, note, and server when available. Creating another benchmark asks before
overwriting the existing one.

Later snapshots report RSSI, SNR, download, and upload deltas against that
reference. The walk header keeps the benchmark visible, while the persistent
`last:` result preserves the latest saved or probed throughput result and its
delta.

## Snapshots and throughput

A snapshot samples signal over a short window and saves one reading for the
active spot. It includes the current network and AP identity, radio and link
data, the aggregated path window, the walk identity, and optional throughput.
The worker runs without freezing the live meters; the header reports pending
saves.

An unsaved throughput probe updates live comparison values without creating a
reading. A snapshot can run the same official Ookla test and persist ping,
download, upload, and server data. Use `--no-speedtest` for signal-only walks;
a missing or failed binary produces a warning rather than discarding the
reading. Overlapping snapshot, benchmark, and throughput work is blocked so two
tests cannot compete for the same connection.

## Rooms and spots

Every reading belongs to a precise spot inside a room and location. Switching
the active spot clears its comparison and unsaved throughput results and starts
a fresh path window; the signal charts retain continuous walk context. Creating
a room records its floor and whether it is outdoors; floor `0` is ground,
negative floors are basements, and positive floors are above ground.

The active SSID belongs to the selected location. Supplying `--ssid` selects or
creates it without a prompt; otherwise the startup flow lets you confirm a
detected network, select an existing one, or enter one manually.

## Prior-walk comparison

Start with `--compare-to ID|NAME|latest`, or choose a baseline while the TUI is
running. wifimap matches the active physical spot to the selected named walk and
reduces repeated baseline readings to medians. The comparison panel places
current RSSI, noise, SNR, transmit rate, ping, download, and upload beside those
medians and labels the change in words as well as numbers.

Only walks from the active location are eligible. Reused human-readable names
are allowed; if a name is ambiguous, wifimap presents the matching IDs. A spot
that did not exist in the baseline gets an explicit no-baseline message rather
than a misleading zero. Switching spots also clears an unsaved throughput probe
so results cannot leak into the next location.

<pre class="command-block" data-copy><code>wifimap walk --location HOME --name after-install \
  --compare-to before-install</code></pre>

For a route-wide before-and-after view after both walks are saved, continue to
the [evaluation guide](/evaluate/).

## Generated ratings and chart scale

<section class="generated-reference" aria-labelledby="walk-ratings-title">
  <div class="generated-reference__header">
    <h3 id="walk-ratings-title">Quality labels</h3>
    <p>Generated from the walk renderer</p>
  </div>
  <ul class="status-reference" aria-label="Walk quality labels">
  {% for status in capabilities.walk.statuses %}
    <li class="status-reference__item status-{{ status | lower }}">
      <span class="status-reference__marker" aria-hidden="true"></span>
      <span>{{ status }}</span>
    </li>
  {% endfor %}
  </ul>

  <h3>Rating thresholds</h3>
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
  <p><code>UNKNOWN</code> is used when the application cannot obtain a value. Color reinforces these labels but never replaces their text.</p>

  <h3>Chart glyphs</h3>
  <p class="sparkline-reference" aria-label="Eight chart levels from low to high">
  {% for glyph in capabilities.walk.chart_glyphs %}
    <span aria-hidden="true">{{ glyph }}</span>
  {% endfor %}
  </p>
</section>
