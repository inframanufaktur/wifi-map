---
layout: layouts/base.njk
title: Walk TUI
description: Live connection diagnostics, roaming timeline, walk controls, snapshots, and comparisons.
navKey: walk-tui
permalink: /walk-tui/index.html
---

# Walk TUI

`walk` displays live measurements and saves readings at named spots.

<pre class="command-block" data-copy><code>wifimap walk --location HOME --ssid MY-WIFI --name before-install</code></pre>

The Rust dashboard uses Ratatui and Crossterm. It shares `usmaybe`'s dark palette,
turquoise accents, place sidebar, charts, details, and keyboard help. Use a terminal
at least 76 columns by 24 rows. `wifimap --demo` explores isolated simulated data.

## Walk controls

<section class="generated-reference" aria-labelledby="walk-controls-title">
  <div class="generated-reference__header">
    <h3 id="walk-controls-title">Keyboard shortcuts</h3>
  </div>
  <dl class="key-reference">
  {% for control in capabilities.walk.controls %}
    <div class="key-reference__item">
      <dt><kbd>{{ control.key }}</kbd> <span>{{ control.name | replace("_", " ") }}</span></dt>
      <dd>{%- if control.name == "snapshot" -%}
        Measure and save a snapshot for the active spot.
      {%- elif control.name == "speed_probe" -%}
        Run an unsaved throughput probe with idle, loaded, and recovery latency.
      {%- elif control.name == "diagnostics" -%}
        Inspect gateway loss/jitter, loaded latency, and the roaming timeline.
      {%- elif control.name == "compare" -%}
        Choose a previous named walk as the live comparison, or turn comparison off.
      {%- elif control.name == "benchmark" -%}
        Capture the location's ideal-conditions benchmark.
      {%- elif control.name == "switch" -%}
        Switch to an existing location, room, and spot.
      {%- elif control.name == "new" -%}
        Create and select a room and spot without leaving the walk.
      {%- elif control.name == "quit" -%}
        Finish the named walk and leave the TUI.
      {%- else -%}
        Perform this walk action.
      {%- endif -%}</dd>
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

Move with `↑`/`↓` or `j`/`k`. Select with `Enter`; return with `Escape`.
`+` or `n` creates a room or spot. `q` finishes the survey.

## Live signal

By default, `SIGNAL` samples RSSI, noise, and SNR about once per second. RSSI and SNR use
the rating thresholds below: green for strong signal, amber for fair, and red
for weak. These colors also appear in the signal history. Missing readings stay
muted and leave gaps in the chart. `WIFI UNAVAILABLE` means the latest radio
sample failed; the message explains the connection or backend error. A sampling
failure alone does not establish that WiFi disconnected.

## Link details

Link details show channel, PHY, transmit rate, band, security, and available MCS.
Native CoreWLAN does not expose MCS through its public API, so it remains blank
for live readings. Link rate is not Internet throughput.

## Network path

`PATH` probes the connected Wi-Fi interface's router and `1.1.1.1` about once
per second. If `1.1.1.1` misses and `8.8.8.8` replies, subsequent probes use `8.8.8.8`. The
Internet window resets when switching endpoints; missing replies count as loss.
The display includes latency, p95, loss, and outages.

Press `d` for the connection diagnostics view. It shows median RTT, p95 RTT,
loss, longest observed outage, and jitter for both paths. Jitter is the mean
absolute RTT change between consecutive successful probes; a timeout breaks the
pair. A single reply cannot establish jitter. Gateway and Internet windows reset
when their probe target changes, so different targets' RTTs are not mixed.

Replies are green and nonzero loss is amber. Missing RTT is red only when the
path window confirms 100% loss; an unknown target stays muted.

Changing spots clears the live path window. Saved readings contain aggregate
count, median, p95, loss, and longest outage. Internet targets see the public
IP and probe timing, but receive no survey data.

## Passive traffic

`TRAFFIC` reads the Wi-Fi interface's byte counters through `netstat`, even when
a VPN or Ethernet connection supplies the global default route. Values are marked unavailable when counters cannot be read. Traffic is
utilization, not capacity; it does not start a speed test.

## Access points

`AP` shows the BSSID and its local alias. When CoreWLAN redacts identity, the
existing optional helper cache refreshes about once per second and before each save. Manage aliases
with the `aps` commands.

## Connection and roaming timeline

Press `d` to inspect observed AP, network, and channel changes. A changed BSSID
on the same known SSID is recorded as a **roam**. The event shows the old and new
BSSID, signal strength, PHY transmit rate, and available gateway/Internet RTTs.
The interval describes the gap between observations, not the actual handoff
duration. Short transitions between polls can be missed. No channels are scanned
or changed to collect this timeline.

Missing or redacted identity does not imply disconnection. AP changes after an
identity gap are labeled conservatively. Sampling failures and recovery appear
as separate events; a measurement error does not establish that Wi-Fi disconnected.
When BSSID is unavailable, roaming and AP dwell cannot be determined.

`Tab` switches between events and correlated signal/rate/AP samples. Use `↑`/`↓`
or `j`/`k` to scroll, `Home` for newest, and `End` for oldest. Sample times are
elapsed time within this session. RTT columns use fresh individual probes, while
the path summary uses rolling aggregates; `—` means unavailable, including a
missing fresh probe. AP dwell is time observed on this AP, not association age.

The session retains up to 120 samples and 60 events. Moving between rooms or
spots preserves the timeline; a new walk, location, or selected survey network
resets it. This timeline and the additional jitter/load details remain in memory
and are not written into
saved readings or CSV. `d` or `Escape` returns to the survey dashboard.

## Latency under load

The existing speed probe (`t`) now measures five idle probes, latency during
the throughput test, and five recovery probes. Diagnostics show separate
gateway and Internet medians, p95, and loss, plus loaded-minus-idle median RTT.
The probe uses the Wi-Fi interface and the existing optional Ookla backend.
It works without a selected survey location or spot; cancel any initial picker
with `Escape`, then use `d` and `t`.
Snapshot and benchmark captures also update the load report when throughput
testing is enabled.
The idle and recovery windows each take approximately five seconds; timeouts
can extend them. Other applications may already be using bandwidth during the
idle window.

The loaded window spans the complete speed-test run, including server selection;
upload and download phases are not separated. The Internet endpoint selected
during idle is kept for the loaded and recovery windows. A failed speed test
leaves loaded and recovery measurements unavailable. If the gateway changes
during the test, gateway comparisons are unavailable. A known network switch
makes loaded/recovery comparisons unavailable even if its gateway has the same
address. Completed reports are cleared on observed AP/network changes.
An increase in RTT is
evidence of delay under load, not proof of its cause.

## Location benchmark

A location stores one benchmark, normally captured near the router. It contains
RSSI, SNR, download, upload, note, server, and capture time. Replacing it
requires confirmation. Later readings show their deltas from the benchmark.

## Snapshots and throughput

`s` starts a new capture and saves the selected survey network, observed AP,
radio, link, path aggregates, walk, and optional throughput result. `t` runs an
unsaved throughput probe. Pressing `s` afterward starts another capture.

`--no-speedtest` records signal without Ookla. A failed speed test produces a
warning and preserves the signal reading. Snapshot, benchmark, and throughput
jobs do not run concurrently. Quitting cancels unfinished captures and probes;
completed readings remain saved.

## Rooms and spots

Each reading belongs to a spot, room, and location. Floor `0` is ground level;
negative floors are basements. Switching spots clears unsaved throughput and the live path window, and reloads
the selected baseline for the new spot.

SSIDs belong to locations. `--ssid NAME` selects or creates one without a
prompt.

## Prior-walk comparison

`--compare-to ID|NAME|latest` loads a walk from the same location. Repeated
baseline readings are reduced to a median for each spot. The comparison shows
RSSI, noise, SNR, transmit rate, ping, download, and upload.

Duplicate walk names require ID selection. Spots absent from the baseline show
no baseline instead of zero.

Comparison deltas are green for improvements and red for regressions. Unchanged
and unavailable deltas stay muted.

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
