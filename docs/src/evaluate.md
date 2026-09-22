---
layout: layouts/base.njk
title: Evaluate readings and compare walks
description: Rank WiFi readings by place, inspect raw captures, and compare named walks in wifimap's evaluation TUI.
navKey: evaluate
permalink: /evaluate/index.html
---

# Evaluate readings and compare walks

Use `wifimap eval` after a survey to find weak spots, inspect individual
captures, or compare two named walks. Evaluation opens the project database
read-only by default. It can also read an immutable, current-schema CSV
export:

```sh
wifimap --db /path/to/wifi-map.db eval
wifimap eval --csv readings.csv
```

## Choose what to evaluate

The first selector chooses a scope type, followed by a specific value. The
`Unknown` SSID scope includes readings that have no SSID. After selection, the
dashboard opens in **spot summary** mode.

All selector screens use `↑`/`↓` or `j`/`k` to move, `Home`/`End` to jump,
and `Enter` to choose. `Escape` returns to the previous screen. At any point,
`q`, `Q`, or `Ctrl-C` exits evaluation.

<section class="reference-block generated-reference" aria-labelledby="evaluation-scopes">
  <p class="reference-label">Generated from the evaluation state machine</p>
  <h2 id="evaluation-scopes">Available scopes</h2>
  <dl class="reference-list">
  {% for kind in capabilities.evaluation.kinds %}
    <div>
      <dt><code>{{ kind.label }}</code></dt>
      <dd>Internal scope key: <code>{{ kind.key }}</code></dd>
    </div>
  {% endfor %}
  </dl>
</section>

Walks selected for comparison must belong to the same location. This remains
true when you enter evaluation through an SSID scope.

## Rank places and readings

Spot summary mode reduces repeated captures at each physical spot to medians,
then ranks the spots worst-first for the active metric. Press `m` to switch
between spot summaries and individual readings. Use `Tab` and `Shift+Tab` to
cycle metrics, `r` to reverse the ranking, and the arrow keys or `j`/`k` to
move through results. `Escape` returns to scope selection.

RSSI and SNR rows use the same GREAT, OK, and WEAK thresholds as the walk TUI.
Benchmark-delta metrics are available when the location has a benchmark and
both values needed for the delta were captured.

<section class="reference-block generated-reference" aria-labelledby="evaluation-metrics">
  <p class="reference-label">Generated from the evaluation metric contracts</p>
  <h2 id="evaluation-metrics">Ranking metrics</h2>
  <div class="table-scroll" tabindex="0" role="region" aria-label="Evaluation ranking metrics">
    <table>
      <thead>
        <tr>
          <th scope="col">Metric</th>
          <th scope="col">Key</th>
          <th scope="col">Unit</th>
          <th scope="col">Preferred direction</th>
          <th scope="col">Decimals</th>
        </tr>
      </thead>
      <tbody>
      {% for metric in capabilities.evaluation.metrics %}
        <tr>
          <th scope="row">{{ metric.label }}</th>
          <td><code>{{ metric.key }}</code></td>
          <td>{{ metric.unit or "—" }}</td>
          <td>{% if metric.higher_is_better %}Higher{% else %}Lower{% endif %}</td>
          <td>{{ metric.decimals }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</section>

## Inspect a result

Press `Enter` on a spot summary to open that spot's newest-first reading
history. From individual-reading mode it opens the selected capture directly.
The detail view includes:

- location, room, floor, and spot;
- SSID, BSSID, and the access point's local name when one exists;
- channel, PHY, TX rate, RSSI, noise, and SNR;
- benchmark deltas and throughput results;
- speed-test server, path-quality aggregates, and notes.

Blank measurements stay blank rather than being treated as zero. This matters
for signal-only snapshots and for readings captured without a benchmark.
Use `↑`/`↓`, `j`/`k`, or `Home`/`End` to move through a spot's reading history;
`Escape` returns to the dashboard.

## Compare named walks

Press `c` from the dashboard, then choose a before walk and an after walk.
wifimap matches physical spots and reduces repeated readings to one median per
walk and spot. The initial **matched** coverage mode shows only places captured
in both walks. Press `f` for **all spots**, which also marks places as `NEW` or
`NOT REVISITED`.

Use `Tab` and `Shift+Tab` to cycle comparison metrics, `r` to reverse the
improvement order, and `x` to swap before and after. Press `Enter` to inspect
both walks' exact reading histories, including radio, throughput, and path
measurements.

In comparison detail, `←`/`→` switches between the before and after histories,
`↑`/`↓` or `j`/`k` moves through readings, and `Tab`/`Shift+Tab` cycles the
radio, throughput, and path detail pages. `Escape` returns to the comparison.

<section class="reference-block generated-reference" aria-labelledby="comparison-metrics">
  <p class="reference-label">Generated from the walk-comparison contracts</p>
  <h2 id="comparison-metrics">Walk comparison metrics</h2>
  <div class="table-scroll" tabindex="0" role="region" aria-label="Walk comparison metrics">
    <table>
      <thead>
        <tr>
          <th scope="col">Metric</th>
          <th scope="col">Key</th>
          <th scope="col">Unit</th>
          <th scope="col">Improves when</th>
        </tr>
      </thead>
      <tbody>
      {% for metric in capabilities.evaluation.comparison_metrics %}
        <tr>
          <th scope="row">{{ metric.label }}</th>
          <td><code>{{ metric.key }}</code></td>
          <td>{{ metric.unit or "—" }}</td>
          <td>{% if metric.higher_is_better %}the value increases{% else %}the value decreases{% endif %}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</section>

## Portable CSV evaluation

CSV evaluation accepts the current export schema. Blank cells are valid, but
missing required columns and malformed numeric values produce a nonzero exit.
Named-walk comparison is available when the export includes the optional walk
columns. See [Data and troubleshooting](/data-troubleshooting/) for the exact
column order and recovery advice.
