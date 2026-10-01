---
layout: layouts/base.njk
title: wifimap documentation
description: Diagnose the current WiFi connection, record measurements at named spots, and compare surveys.
navKey: overview
permalink: /index.html
---

# wifimap

wifimap diagnoses the current WiFi connection and records measurements at named
spots on macOS. Saved readings stay in a local SQLite database. CSV export is
optional. The application is written in Rust; building requires
Rust {{ capabilities.package.rust_requires }} or newer.

## Commands

Run `wifimap` to open the live dashboard. No subcommand is required:

<pre class="command-block" data-copy><code>wifimap</code></pre>

Explore simulated measurements:

<pre class="command-block" data-copy><code>wifimap --demo</code></pre>

Give the survey an explicit name. The `walk` subcommand remains available for
survey options:

<pre class="command-block" data-copy><code>wifimap walk --name before-install</code></pre>

Rank saved readings and compare walks:

<pre class="command-block" data-copy><code>wifimap eval</code></pre>

Export saved readings:

<pre class="command-block" data-copy><code>wifimap export --csv readings.csv</code></pre>

For a single reading, `scan` requires a location, room, and spot. Supply an SSID
to avoid a network-selection prompt:

<pre class="command-block" data-copy><code>wifimap scan --location HOME --room KITCHEN --spot WINDOW --ssid HOME-WIFI</code></pre>

## Recorded data

- RSSI, noise, SNR, channel, PHY, transmit rate, band, SSID, and BSSID
- router and Internet latency, p95, packet loss, and longest outage
- optional Ookla ping, download, upload, and server details
- location, room, floor, indoor or outdoor status, spot, note, and AP name

Traffic graphs show current WiFi interface use. They do not measure Internet
capacity and are not stored as throughput results.

## Current connection diagnostics

In the dashboard, press `d` for gateway and Internet loss/jitter, latency under load,
and a correlated signal/rate/AP timeline with observed roaming events. `Tab`
switches events and samples; `t` runs the optional unsaved speed probe. You can
cancel the initial pickers with `Escape` and inspect the current connection
without selecting a survey place.

Jitter, load reports, and timeline history stay in memory for the current
session. Saved readings and CSV contain the existing radio, throughput, and
path aggregates. See [Walk TUI](/walk-tui/) for
controls and measurement limits.

## Documentation

- [Installation and first survey](/quick-start/)
- [Walk TUI](/walk-tui/)
- [TUI controls](/tui-controls/)
- [Evaluation](/evaluate/)
- [CLI reference](/cli-reference/)
- [Data and troubleshooting](/data-troubleshooting/)
- [Design language](/design-language/)
- [Development and deployment](/development/)
