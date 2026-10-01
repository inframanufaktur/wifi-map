# Live connection diagnostics

## Objective and acceptance criteria

Keep diagnostics inside wifimap and observe the active Wi-Fi connection. Add
gateway and Internet loss/jitter, idle/loaded/recovery latency during the existing
optional speed probe, and a correlated signal/link-rate/AP timeline with observed
roaming events. Do not switch channels or put the connected interface into RF
monitor mode.

- Gateway probes and traffic counters refer to the Wi-Fi interface, including
  when Ethernet or a VPN supplies the global default route.
- Jitter is the mean absolute RTT change between adjacent successful probes;
  timeouts do not bridge successful pairs. Unknown targets are unavailable,
  rather than lost packets.
- Loaded latency has separate idle, during-test, and recovery summaries. Failed
  speed tests cannot produce a successful load measurement. Cancellation remains
  responsive.
- Timestamped timeline samples correlate raw signal, PHY rate, AP/channel, traffic,
  and the latest individual latency probes. Known same-network BSSID transitions
  are observed roams; other transitions are described conservatively.
- Missing/redacted BSSIDs do not become disconnects. Sampling gaps and identity
  gaps remain visible; a sampling interval is not a measured handoff duration.
- Timeline history is bounded and lasts for the current session, including
  across room and spot changes. A new walk, location, or selected survey network
  resets it. Late worker results cannot pollute a new session.
- The unsaved speed probe works without a selected survey location or spot.
  Completed load reports are cleared on later known AP/network transitions;
  a same-SSID roam during the test can remain part of the complete-run result.
- The dashboard has a keyboard-accessible diagnostics view, renders at 76x24,
  and includes simulated data in demo mode.

## Commands

Build: `scripts/cargo build --locked --release`

Test: `scripts/cargo test --locked`

Format: `scripts/cargo fmt --check`

Lint: `scripts/cargo clippy --locked --all-targets -- -D warnings`

Terminal verification: `python3 scripts/smoke_tui.py`

Documentation: `npm run docs:build` and `npm run docs:test`

## Source organization

- `src/measure.rs` exposes the measurement API. `src/measure/radio.rs` and
  `corewlan.rs` read radio/identity data; `path.rs`, `throughput.rs`, and
  `traffic.rs` own Wi-Fi-bound probes, latency phases, and passive counters.
  `process.rs` owns cancellable subprocesses.
- `src/path_diagnostics.rs` defines session-only jitter and load reports.
- `src/timeline.rs` retains bounded connection samples/events and detects
  observed roams, other transitions, and sampling gaps.
- `src/app.rs` owns shared state, controls, and terminal entry points.
  `src/app/survey.rs` manages survey sessions and places; `measurement.rs`
  schedules captures and applies results; `live.rs` owns current-connection
  sampling and probe freshness; `eval.rs` owns evaluation state and controls.
- `src/ui.rs` shares the terminal palette and drawing helpers.
  `src/ui/walk.rs`, `evaluation.rs`, `reading.rs`, `diagnostics.rs`, and
  `overlays.rs` render the dashboard, reports, details, diagnostics, and dialogs.
- `src/measure/tests/`, `src/app/tests.rs`, `src/ui/tests.rs`, and inline
  timeline tests cover measurement behavior, session ownership, and rendering.
  `docs/src/walk-tui.md` describes controls and measurement limits.

## Code style and testing

Use the existing Rust types, standard library, `Option` for unavailable metrics,
and `scripts/cargo fmt`. Example: `pub gateway_jitter_ms: Option<f64>`.
Place behavior tests alongside their modules and use Ratatui's test backend for
rendering. Verify generation filtering, session resets, identity gaps, timeout
statistics, and failures as well as normal transitions. Do not require real
roaming or Internet access for automated tests.

## Boundaries

Always preserve real connection identity separately from a survey's chosen name,
bound retained data, preserve cancellation, and report unavailable measurements.
Keep the SQLite schema and existing 41-column CSV format compatible. Add no new
dependencies or required external applications. Use the speed probe's existing
optional Ookla backend. Do not scan other channels or transmit Wi-Fi management
frames. Timeline and the new diagnostic details remain session-local.
