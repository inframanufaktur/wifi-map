---
layout: layouts/base.njk
title: Quick start
description: Install wifimap on macOS and complete a first snapshot, named walk, comparison, and evaluation.
navKey: quick-start
permalink: /quick-start/index.html
---

# Complete a first WiFi survey

Set up wifimap, save a reading, and establish a named baseline you can compare
after changing your network. The example uses a throwaway database, so it will
not alter readings in the default project database.

## Requirements

- **macOS:** wifimap reads live WiFi data through Apple's CoreWLAN framework.
- **Python {{ capabilities.package.python_requires }}:** the application remains compatible with Python 3.9 and newer versions allowed by this requirement.
- **A WiFi connection:** no dedicated survey hardware is required.
- **Optional throughput tests:** install the official Ookla `speedtest` binary if you want ping, download, and upload results. Signal-only surveys work without it.

The commands below assume you are in a checkout of the wifimap repository.

## 1. Create the environment

Upgrade pip before installing PyObjC. The stock pip bundled with some Python
installations may otherwise try to build `pyobjc-core` from source and fail.

<pre class="command-block" data-copy><code>python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
python -m pip install pyobjc-framework-CoreWLAN</code></pre>

Keep the environment active for the remaining steps.

## 2. Save a first snapshot

Capture a signal-only reading at a precise spot. Unknown location, room, spot,
and SSID names are created automatically by `scan`.

<pre class="command-block" data-copy><code>wifimap --db /tmp/demo.db scan --no-speedtest \
  --location HOME --room KITCHEN --spot WINDOW --room-floor 0 \
  --ssid DEMO-WIFI</code></pre>

Global options such as `--db` go before the subcommand. A real database defaults
to `db/wifi-map.db`; this guide uses `/tmp/demo.db` so you can start over easily.

## 3. Record a named walk

Start a baseline survey and visit the spots you care about. In the walk TUI,
create or switch to a room and spot, then save at least one snapshot at each.

<pre class="command-block" data-copy><code>wifimap --db /tmp/demo.db walk \
  --location HOME --ssid DEMO-WIFI --name before-install</code></pre>

The live instruments update continuously. Saving a snapshot records the active
spot's radio reading and aggregated path window; with speed tests enabled it
also records the Ookla result. Quit when the route is complete.

## 4. Compare after a change

Move the router, add a mesh node, or make another controlled change. Start a
second named walk with the first walk selected as its baseline:

<pre class="command-block" data-copy><code>wifimap --db /tmp/demo.db walk \
  --location HOME --ssid DEMO-WIFI --name after-install \
  --compare-to before-install</code></pre>

At each matching spot, the walk shows current radio and throughput measurements
beside the first walk's medians. Repeat the route and save fresh snapshots so the
two named walks can also be compared later in evaluation.

## 5. Evaluate weak spots

Open the read-only evaluation dashboard for the demo database:

<pre class="command-block" data-copy><code>wifimap --db /tmp/demo.db eval</code></pre>

Choose a Location or SSID, rank the spot summaries by a metric, and open a row
to inspect its exact readings. From the dashboard, enter walk comparison to
compare `before-install` with `after-install`, including spots that were new or
not revisited.

You can also export a portable report and evaluate it without the database:

<pre class="command-block" data-copy><code>wifimap --db /tmp/demo.db export --csv readings.csv
wifimap eval --csv readings.csv</code></pre>

Next, learn the [walk controls and live instruments](/walk-tui/) or read the
[evaluation workflow](/evaluate/).
