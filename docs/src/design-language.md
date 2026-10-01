---
layout: layouts/base.njk
title: Design language
description: Dashboard layout, color meanings, selection styles, and wording shared by the terminal and documentation.
navKey: design-language
permalink: /design-language/index.html
---

# Design language

wifimap follows the framed, monospace dashboard conventions shared with
[usmaybe](https://github.com/inframanufaktur/usmaybe) and the
[TUI toolkit template](https://github.com/inframanufaktur/tui-template).
The documentation uses the same dark surfaces, turquoise accents, bordered
panels, and keyboard labels.

## Layout and focus

- A framed header identifies the application, current view, and data mode.
- A muted context line gives the active place, survey, counts, or ranking scope.
- A left sidebar selects survey spots or explains the current evaluation.
- The main area shows live readings, charts, rankings, or walk comparisons.
- A lower panel explains the selected reading or comparison.
- The footer shows status and the current keyboard shortcuts.
- Selection uses a `▸` marker, accent foreground, and raised background.
- Help and pickers use centered bordered overlays. Small terminals show a resize hint.

The minimum terminal size is 76 × 24. A 110 × 32 terminal leaves more room for
charts and details. Web navigation moves above the document on narrow screens;
reference tables scroll horizontally.

## Palette

Terminal colors and rendering helpers live in
[`src/ui.rs`]({{ site.repository }}/blob/main/src/ui.rs). Web tokens live in
[`site.css`]({{ site.repository }}/blob/main/docs/src/assets/site.css), with the
framed layout in
[`layout.css`]({{ site.repository }}/blob/main/docs/src/assets/layout.css).
Keep these definitions aligned when changing the design.

| Role | Terminal constant | Color | Use |
| --- | --- | --- | --- |
| Background | `BG` | `#0f151e` | Main surface and framed panels |
| Raised surface | `PANEL` | `#17202c` | Selected rows and alternating table rows |
| Accent | `ACCENT` | `#4adecb` | Identity, selections, and key hints |
| Muted | `DIM` | `#8b99aa` | Context, missing values, and terminal borders |
| Good | `GOOD` | `#4ade80` | Strong signal and improvements |
| Warning | `WARN` | `#fac460` | Fair signal, loss, and limited support |
| Bad | `BAD` | `#f87171` | Weak signal and regressions |

The terminal uses white primary text; the documentation uses `#edf2f7` text and
`#586779` borders. The web focus outline uses warning amber.

## Status and wording

`DEMO / SIMULATED` identifies isolated in-memory data. `LIVE` means current
measurements; `SAVED / REPORT` identifies evaluation of stored readings.
`WIFI UNAVAILABLE` means the latest radio sample failed. It does not by itself
establish that the connection disconnected.

Show missing values as `—`. Pair colors with labels or numeric values. RSSI and
SNR use strong, fair, and weak thresholds; comparison deltas follow the metric's
preferred direction. Missing values and changes displayed as zero stay muted.
Amber sample counts indicate limited measurement support. See
[Walk TUI](/walk-tui/) and [Evaluate](/evaluate/) for the measurement rules.

Use short verbs in key hints: choose, save, compare, cancel, help, quit.
Describe completed actions and errors in the status line. Keep radio link rate,
passive traffic, and measured Internet throughput clearly labeled.
