---
layout: layouts/base.njk
title: Quick start
description: Install wifimap on macOS and record a survey in a temporary database.
navKey: quick-start
permalink: /quick-start/index.html
---

# Quick start

These commands use `/tmp/demo.db`. The default database is not modified.

## Requirements

- macOS
- Rust {{ capabilities.package.rust_requires }} or newer and Xcode Command Line Tools
- an active WiFi connection
- native CoreWLAN (included in macOS)
- optional: the official Ookla `speedtest` binary for throughput measurements

The commands below assume you are in a checkout of the wifimap repository.

## 1. Build the executable

The launcher builds the Rust application when needed. No Python environment is
required. Run demo mode to explore without live WiFi or a saved database.

<pre class="command-block" data-copy><code>scripts/cargo build --release
./wifimap --demo
scripts/cargo install --locked --path .</code></pre>

## 2. Inspect the current connection

Open the live dashboard:

<pre class="command-block" data-copy><code>wifimap --db /tmp/demo.db walk</code></pre>

Press `Escape` to cancel the initial picker, then `d` for connection diagnostics.
Inspect gateway loss/jitter and the roaming timeline; `Tab` switches to
correlated signal/rate/AP samples. With the optional Ookla binary installed,
`t` measures latency under load. No survey location or spot is required for this
unsaved probe. Press `d` or `Escape` to return and `q` to quit.

These diagnostic details stay in memory. The next steps record readings you can
save and compare later.

## 3. Save a first snapshot

This records a signal-only reading. `scan` creates the supplied location, room,
spot, and SSID names when they do not exist.

<pre class="command-block" data-copy><code>wifimap --db /tmp/demo.db scan --no-speedtest \
  --location HOME --room KITCHEN --spot WINDOW --room-floor 0 \
  --ssid DEMO-WIFI</code></pre>

Global options such as `--db` work before or after the subcommand. The default database is
`db/wifi-map.db`.

## 4. Record a named walk

Open the walk TUI:

<pre class="command-block" data-copy><code>wifimap --db /tmp/demo.db walk \
  --location HOME --ssid DEMO-WIFI --name before-install</code></pre>

Select or create a room and spot. Press `s` to save a reading and `q` to finish
the walk.

## 5. Compare after a change

Record a second walk against the first:

<pre class="command-block" data-copy><code>wifimap --db /tmp/demo.db walk \
  --location HOME --ssid DEMO-WIFI --name after-install \
  --compare-to before-install</code></pre>

Matching spots show the current measurements beside the first walk's medians.

## 6. Evaluate weak spots

Open the evaluation TUI:

<pre class="command-block" data-copy><code>wifimap --db /tmp/demo.db eval</code></pre>

Press `l` to filter by location or `w` by SSID. Select a row to inspect its readings. Press `c` to
compare `before-install` and `after-install`.

Export and evaluate CSV data with:

<pre class="command-block" data-copy><code>wifimap --db /tmp/demo.db export --csv readings.csv
wifimap eval --csv readings.csv</code></pre>
