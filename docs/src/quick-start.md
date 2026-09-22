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
- Python {{ capabilities.package.python_requires }}
- an active WiFi connection
- PyObjC for CoreWLAN signal readings
- optional: the official Ookla `speedtest` binary for throughput measurements

The commands below assume you are in a checkout of the wifimap repository.

## 1. Create the environment

Upgrade pip before installing PyObjC; older pip versions may fail while building
`pyobjc-core`.

<pre class="command-block" data-copy><code>python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
python -m pip install pyobjc-framework-CoreWLAN</code></pre>

## 2. Save a first snapshot

This records a signal-only reading. `scan` creates unknown location, room, spot,
and SSID names.

<pre class="command-block" data-copy><code>wifimap --db /tmp/demo.db scan --no-speedtest \
  --location HOME --room KITCHEN --spot WINDOW --room-floor 0 \
  --ssid DEMO-WIFI</code></pre>

Global options such as `--db` go before the subcommand. The default database is
`db/wifi-map.db`.

## 3. Record a named walk

Open the walk TUI:

<pre class="command-block" data-copy><code>wifimap --db /tmp/demo.db walk \
  --location HOME --ssid DEMO-WIFI --name before-install</code></pre>

Select or create a room and spot. Press `s` to save a reading and `q` to finish
the walk.

## 4. Compare after a change

Record a second walk against the first:

<pre class="command-block" data-copy><code>wifimap --db /tmp/demo.db walk \
  --location HOME --ssid DEMO-WIFI --name after-install \
  --compare-to before-install</code></pre>

Matching spots show the current measurements beside the first walk's medians.

## 5. Evaluate weak spots

Open the evaluation TUI:

<pre class="command-block" data-copy><code>wifimap --db /tmp/demo.db eval</code></pre>

Choose a location or SSID. Select a row to inspect its readings. Press `c` to
compare `before-install` and `after-install`.

Export and evaluate CSV data with:

<pre class="command-block" data-copy><code>wifimap --db /tmp/demo.db export --csv readings.csv
wifimap eval --csv readings.csv</code></pre>
