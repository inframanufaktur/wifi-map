# Probe B — Swift CoreWLAN helper (wifisig)

Date: 2026-09-18. Machine: macOS 26.6.2 (25G83), ARM64, swiftc Apple Swift 6.4 (swift-driver 1.168.6).

## Source
`/tmp/wifisig/main.swift` — `CWWiFiClient.shared().interface()`, prints one-line JSON:
`ssid, bssid, rssi (rssiValue), noise (noiseMeasurement), channel (wlanChannel.channelNumber), txRate (transmitRate)`.

## Build
- Cmd: `swiftc -O /tmp/wifisig/main.swift -o /tmp/wifisig/wifisig`
- Result: success, exit 0, binary 52.8K.
- Wall time: 1.498s total (1.01s user, 0.40s sys).

## Per-run latency (Python subprocess, perf_counter, ms)
| run | ms | rc | output |
|-----|------|----|--------|
| 1 (cold) | 340.2 | 0 | `{"ssid":"nil","bssid":"nil","rssi":-55,"noise":-92,"channel":36,"txRate":780.0}` |
| 2 | 18.7 | 0 | same values |
| 3 | 15.7 | 0 | same |
| 4 | 13.2 | 0 | same |
| 5 | 12.0 | 0 | same |
| 6 | 11.2 | 0 | same |
| 7 | 10.5 | 0 | same |
| 8 | 9.6 | 0 | same |
| 9 | 10.2 | 0 | same |
| 10 | 8.9 | 0 | same |

- Median (10 runs): 11.6ms. Median warm (runs 2–10): ~11.2ms. Min 8.9, max 340.2 (cold start), mean 45.0.
- Sample output: `{"ssid":"nil","bssid":"nil","rssi":-55,"noise":-92,"channel":36,"txRate":780.0}`

## Permission / sandbox
- No permission prompt observed, no stderr, no sandbox denial across 10 runs + 1 extra.
- Caveat: `ssid`/`bssid` returned nil on macOS 26.6.2 (expected — SSID/BSSID need Location Services entitlement/permission; rssi/noise/channel/txRate unaffected).

## Verdict
**Yes — viable for 1s polling from Python subprocess.** Warm latency ~9–19ms = ~1% of 1s budget, two orders margin. Cold first-run ~340ms still fits. Blocker only if SSID/BSSID strings required — then needs location permission handling or alternate API; signal metrics (rssi/noise/channel/txRate) work permission-free.
