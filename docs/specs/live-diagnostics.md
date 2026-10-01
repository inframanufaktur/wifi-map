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
- Timeline history is bounded and lasts for the current walk, including across
  spot changes. A new walk resets it. Late worker results cannot pollute a new
  session.
- The dashboard has a keyboard-accessible diagnostics view, renders at 76x24,
  and includes simulated data in demo mode.

## Commands

Build: `scripts/cargo build --locked --release`

Test: `scripts/cargo test --locked`

Format: `scripts/cargo fmt --check`

Lint: `scripts/cargo clippy --locked --all-targets -- -D warnings`

Terminal verification: `python3 scripts/smoke_tui.py`

Documentation: `npm run docs:build` and `npm run docs:test`

## Project structure and plan

1. `src/measure.rs` and `src/path_diagnostics.rs`: Wi-Fi-bound probes, jitter,
   and latency phases; deterministic measurement tests.
2. `src/timeline.rs`: bounded connection samples/events and conservative roaming
   detection; deterministic transition and gap tests.
3. `src/app.rs`: cancellable worker integration and session/result ownership.
4. `src/ui.rs`: diagnostics view and compact live summaries; terminal render tests.
5. `docs/src/walk-tui.md`: controls, measurement definitions, and limitations.

Measurement and timeline work proceed in parallel, followed by integrated tests,
terminal verification, and a separate review.

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
