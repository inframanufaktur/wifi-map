---
layout: layouts/base.njk
title: Map your WiFi with confidence
description: Capture room-by-room WiFi readings, compare named walks, and evaluate weak spots with wifimap.
navKey: overview
permalink: /index.html
---

# Know where your WiFi works—and where it does not

wifimap turns a walk around your home into evidence you can use. Capture signal,
local and Internet path quality, and optional throughput at precise spots; repeat
the same route after a change; then rank the results in a focused terminal
dashboard.

It is a local-first, macOS command-line tool for Python
{{ capabilities.package.python_requires }}. Readings stay in SQLite until you
choose to export them as CSV.

<p class="action-row">
  <a class="button-link" href="/quick-start/">Complete a first survey</a>
  <a class="text-link" href="/walk-tui/">Explore the walk TUI</a>
</p>

## One route from question to answer

<figure class="terminal-panel workflow-panel" aria-labelledby="workflow-panel-title" aria-describedby="workflow-panel-caption">
  <div class="terminal-panel__bar" aria-hidden="true">
    <span>WIFI-MAP</span>
    <span>HOME · ready</span>
  </div>
  <h3 id="workflow-panel-title" class="terminal-panel__title">CAPTURE · COMPARE · EVALUATE</h3>
  <ol class="workflow-steps">
    <li class="workflow-step">
      <span class="workflow-step__number" aria-hidden="true">01</span>
      <div>
        <h4>Capture each spot</h4>
        <p>Walk room to room and save radio, path, access-point, and optional throughput readings.</p>
      </div>
    </li>
    <li class="workflow-step">
      <span class="workflow-step__number" aria-hidden="true">02</span>
      <div>
        <h4>Compare a change</h4>
        <p>Name the first walk, make an infrastructure change, then see live values beside its per-spot medians.</p>
      </div>
    </li>
    <li class="workflow-step">
      <span class="workflow-step__number" aria-hidden="true">03</span>
      <div>
        <h4>Evaluate coverage</h4>
        <p>Rank spots by signal, link, latency, throughput, or benchmark deltas and inspect the underlying readings.</p>
      </div>
    </li>
  </ol>
  <figcaption id="workflow-panel-caption">
    Repeatable named walks connect measurements taken in the same physical spots,
    so before-and-after results remain comparable.
  </figcaption>
</figure>

## What a walk observes

- **Radio and link:** RSSI, noise, SNR, channel, PHY, transmit rate, band, security, SSID, and BSSID.
- **Network path:** rolling router and Internet latency, p95, loss, and outage history from small ICMP probes.
- **Traffic and capacity:** passive interface utilization plus optional, explicitly triggered Ookla throughput tests.
- **Place and context:** location, room, floor, indoor or outdoor status, precise spot, notes, and a recognizable access-point name.

The live traffic graphs show current interface use, not Internet capacity. They
are intentionally kept separate from stored throughput results.

## Built for repeatable decisions

Use a quick snapshot when you need one measurement. Use a named walk when you
want consistent coverage before and after moving a router, adding a mesh node,
or changing a channel. Use evaluation to find the weakest spots first and drill
into exact readings without sending your home layout or network names to a web
service.

Continue with the [quick start](/quick-start/), learn every
[walk control and instrument](/walk-tui/), or open the
[evaluation guide](/evaluate/).
