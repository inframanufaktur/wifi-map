---
layout: layouts/base.njk
title: Evaluation
description: Evaluation controls, ranking metrics, reading details, and walk comparison.
navKey: evaluate
permalink: /evaluate/index.html
---

# Evaluation

`eval` reads a SQLite database or CSV export. Database access is read-only.

```sh
wifimap --db /path/to/wifi-map.db eval
wifimap eval --csv readings.csv
```

## Choose what to evaluate

Choose a location or SSID, then choose its value. `Unknown` contains readings
without an SSID. Results open in spot-summary mode.

Move with `↑`/`↓` or `j`/`k`. `Home`/`End` jump to the first or last row.
`Enter` selects, `Escape` returns, and `q` or `Ctrl-C` exits.

<section class="reference-block generated-reference" aria-labelledby="evaluation-scopes">
  <h2 id="evaluation-scopes">Available scopes</h2>
  <dl class="reference-list">
  {% for kind in capabilities.evaluation.kinds %}
    <div>
      <dt><code>{{ kind.label }}</code></dt>
      <dd>
      {% if kind.key == "location" %}
        Review every captured network and spot at one physical location.
      {% elif kind.key == "ssid" %}
        Review readings for one network name across its location.
      {% else %}
        Review readings within this scope.
      {% endif %}
      </dd>
    </div>
  {% endfor %}
  </dl>
</section>

## Rank places and readings

Spot summaries show the median at each spot and sort worst-first. `m` switches
between summaries and individual readings. `Tab` changes the metric; `r`
reverses the order.

RSSI and SNR use the walk TUI rating thresholds. Benchmark deltas require a
location benchmark and a captured value on both sides.

<section class="reference-block generated-reference" aria-labelledby="evaluation-metrics">
  <h2 id="evaluation-metrics">Ranking metrics</h2>
  <div class="table-scroll" tabindex="0" role="region" aria-label="Evaluation ranking metrics">
    <table>
      <thead>
        <tr>
          <th scope="col">Metric</th>
          <th scope="col">Unit</th>
          <th scope="col">Preferred direction</th>
        </tr>
      </thead>
      <tbody>
      {% for metric in capabilities.evaluation.metrics %}
        <tr>
          <th scope="row">{{ metric.label }}</th>
          <td>{{ metric.unit or "—" }}</td>
          <td>{% if metric.higher_is_better %}Higher{% else %}Lower{% endif %}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</section>

## Inspect a result

`Enter` opens a spot's newest-first history or the selected reading. Details
include:

- location, room, floor, and spot;
- SSID, BSSID, and the access point's local name when one exists;
- channel, PHY, TX rate, RSSI, noise, and SNR;
- benchmark deltas and throughput results;
- speed-test server, path-quality aggregates, and notes.

Blank measurements remain blank. Move through history with `↑`/`↓`, `j`/`k`,
or `Home`/`End`.

## Compare named walks

`c` selects a before walk and an after walk from the same location. Comparison
uses one median per walk and spot. **Matched** mode contains spots present in
both walks. `f` includes all spots and marks them `NEW` or `NOT REVISITED`.

`Tab` changes the metric, `r` reverses the order, and `x` swaps the walks.
`Enter` opens both reading histories.

In detail view, `←`/`→` switches walks and `Tab` switches radio, throughput,
and path pages.

<section class="reference-block generated-reference" aria-labelledby="comparison-metrics">
  <h2 id="comparison-metrics">Walk comparison metrics</h2>
  <div class="table-scroll" tabindex="0" role="region" aria-label="Walk comparison metrics">
    <table>
      <thead>
        <tr>
          <th scope="col">Metric</th>
          <th scope="col">Unit</th>
          <th scope="col">Improves when</th>
        </tr>
      </thead>
      <tbody>
      {% for metric in capabilities.evaluation.comparison_metrics %}
        <tr>
          <th scope="row">{{ metric.label }}</th>
          <td>{{ metric.unit or "—" }}</td>
          <td>{% if metric.higher_is_better %}the value increases{% else %}the value decreases{% endif %}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</section>

## Portable CSV evaluation

Blank CSV cells are valid. Missing required columns and malformed numbers
produce a nonzero exit. Walk comparison requires the optional walk columns.
The exact column order is listed in [Data and troubleshooting](/data-troubleshooting/).
