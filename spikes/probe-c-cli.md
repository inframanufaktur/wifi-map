# Probe C — Built-in CLI readers (wdutil, system_profiler)

Date: 2026-09-18. Host: macOS ARM64.

## Timings

| Command | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| `wdutil info` (no sudo) | 0.02s, rc=0, 288B usage text only | 0.01s, same | 0.01s, same |
| `system_profiler SPAirPortDataType` | 4.49s, rc=0, ~3.2KB | 4.48s, rc=0, ~3.4KB | — |

## Fields found

### wdutil info (no sudo)
- Output: usage string only (`usage: sudo wdutil info ...`). No RSSI, no noise, no BSSID, no channel.
- Current-network data: none without sudo.

### sudo need check
- `timeout 10 sudo -n wdutil info` → `sudo: a password is required`.
- Sudo needed? **yes**. Cannot run passwordless here; would block/hang daemon polling.

### system_profiler SPAirPortDataType
Current Network Information block under `en0`:
- `PHY Mode: 802.11ac` — yes
- `Channel: 36 (5GHz, 160MHz)` — yes
- `Signal / Noise: -57 dBm / -92 dBm` (varied -54 to -57 across runs) — yes
- `Transmit Rate: 780`, `MCS Index: 8`, `Security: WPA2/WPA3 Personal`, `Network Type: Infrastructure` — yes
- SSID / BSSID: **redacted** — network name lines shown as `<redacted>:`; no BSSID line exposed.
- MAC, PHY modes, supported channels also present.

## Sudo needed?
- wdutil: **yes**.
- system_profiler: **no**.

## Verdict (viable for 1s polling?)
- wdutil: **no** — needs sudo, no data without it.
- system_profiler: **no** — ~4.5s wall time, SSID/BSSID redacted, far too slow/heavy for 1s poll.
