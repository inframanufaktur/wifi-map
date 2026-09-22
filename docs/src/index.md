---
layout: layouts/base.njk
title: wifimap documentation
description: Install wifimap, record WiFi measurements, compare walks, and inspect survey data.
navKey: overview
permalink: /index.html
---

# wifimap

wifimap records WiFi measurements at named spots on macOS. Data stays in a
local SQLite database. CSV export is optional. Python
{{ capabilities.package.python_requires }} is required.

## Commands

- `wifimap scan` records one reading.
- `wifimap walk` opens the survey TUI and groups readings into a named walk.
- `wifimap eval` ranks readings and compares walks.
- `wifimap export --csv FILE` writes the database to CSV.

## Recorded data

- RSSI, noise, SNR, channel, PHY, transmit rate, band, SSID, and BSSID
- router and Internet latency, p95, packet loss, and longest outage
- optional Ookla ping, download, upload, and server details
- location, room, floor, indoor or outdoor status, spot, note, and AP name

Traffic graphs show current interface use. They do not measure Internet
capacity and are not stored as throughput results.

## Documentation

- [Installation and first survey](/quick-start/)
- [Walk TUI](/walk-tui/)
- [Evaluation](/evaluate/)
- [CLI reference](/cli-reference/)
- [Data and troubleshooting](/data-troubleshooting/)
