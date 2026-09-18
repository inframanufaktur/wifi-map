# Probe A — PyObjC CoreWLAN polls

Date: 2026-09-18. Host: macOS ARM64 (Tahoe 26.6.2), /usr/bin/python3 3.9.6, venv /tmp/wifimap-probe-a.

## Install

- Command: `/tmp/wifimap-probe-a/bin/pip install pyobjc-framework-CoreWLAN`
- Stock venv pip 21.2.4: **FAIL**. Tried to build pyobjc 12.0 from source (`pyobjc-core` wheel build, PEP 517). clang errors (16x, `-Werror,-Wdefault-const-init-var-unsafe` in `Modules/objc/objc_support.m`, `VECTOR_TO_PYTHON`), `Failed to build pyobjc-core`. Wall: **22.02s** (16.49s user, 94% cpu).
- Fix: `/tmp/wifimap-probe-a/bin/pip install --upgrade pip` → pip 26.0.1, then reinstall: **SUCCESS**. Correctly resolved `pyobjc-core 11.1 + pyobjc-framework-Cocoa 11.1 + pyobjc-framework-CoreWLAN 11.1` prebuilt wheels (`macosx_10_9_universal2`). Wall: **0.53s**.
- Root cause: pip 21.2.4 ignored `requires-python` and grabbed 12.0 (needs newer Python); 11.1 is last cp39-compatible line and ships wheels. pyobjc 12.0 likely needs Python ≥3.10 (brew has 3.14.7, untested here).
- Import cost: `from CoreWLAN import CWWiFiClient` ≈ **1237ms** cold, one-time.

## Poll loop (10 consecutive, same `CWWiFiClient.sharedWiFiClient()`)

Code per poll: `c.interface()` + `ssid() bssid() rssiValue() noiseMeasurement() wlanChannel() transmitRate() activePHYMode()`.

| Poll | Latency | rssi | noise | rate | phy | channel |
|---|---|---|---|---|---|---|
| 0 | 9.65ms | -57 | -92 | 780.0 | 5 | 36 (5GHz, 160MHz) |
| 1 | 5.41ms | -57 | -92 | 780.0 | 5 | 36 (5GHz, 160MHz) |
| 2 | 5.42ms | -57 | -92 | 780.0 | 5 | 36 (5GHz, 160MHz) |
| 3 | 5.56ms | -57 | -92 | 780.0 | 5 | 36 (5GHz, 160MHz) |
| 4 | 5.39ms | -57 | -92 | 780.0 | 5 | 36 (5GHz, 160MHz) |
| 5 | 5.52ms | -57 | -92 | 780.0 | 5 | 36 (5GHz, 160MHz) |
| 6 | 5.26ms | -57 | -92 | 780.0 | 5 | 36 (5GHz, 160MHz) |
| 7 | 5.34ms | -57 | -92 | 780.0 | 5 | 36 (5GHz, 160MHz) |
| 8 | 5.01ms | -57 | -92 | 780.0 | 5 | 36 (5GHz, 160MHz) |
| 9 | 5.29ms | -57 | -92 | 780.0 | 5 | 36 (5GHz, 160MHz) |

Summary: min 5.01ms, **median 5.40ms**, mean 5.78ms, max 9.65ms (first poll warm).

## Fields obtained (exact method names on `CWInterface`)

- `ssid()` → `None` (masked, see blocker)
- `bssid()` → `None` (masked, see blocker)
- `rssiValue()` → `-57` (dBm, int) — yes
- `noiseMeasurement()` → `-92` (dBm, int) — yes
- `wlanChannel()` → `<CWChannel> [channelNumber=36(5GHz), channelWidth={160MHz}]` — yes (note: method is `wlanChannel`, not `channel`)
- `transmitRate()` → `780.0` (Mbps, float) — yes
- `activePHYMode()` → `5` (enum; 5 ≈ 802.11ac per probe-C cross-check showing `PHY Mode: 802.11ac`) — yes
- `interface()` → `<CWInterface> [interfaceName=en0]` — never None here.

## Sample output (raw, poll 0)

```
poll 0: 9.65ms ssid=None bssid=None rssi=-57 noise=-92 channel=<CWChannel: 0x104efe0b0> [channelNumber=36(5GHz), channelWidth={160MHz}] rate=780.0 phy=5
```

## Blockers

1. Stock-pip install fails (pyobjc-core 12.0 source build on cp39/Tahoe). Workaround: upgrade pip first → 11.1 wheels in ~0.5s. Pin `pyobjc-framework-CoreWLAN==11.1` for cp39.
2. `ssid()`/`bssid()` return `None` from bare Terminal python — macOS location-privacy gate (SSID/BSSID need Location Services authorization for the parent app). Same redaction seen in probe-C (`<redacted>`). **Kills AP-identity mapping** unless daemon host gets Location permission; RSSI/noise/channel/rate unaffected.
3. Cold import ~1.2s — fine for long-lived daemon, not per-sample subprocess.

## Verdict (viable for 1s polling?)

- Speed: **yes** — ~5.4ms median, 2 orders of magnitude under 1s budget.
- Identity: **no (as-is)** — without Location Services grant, no SSID/BSSID, so mapper cannot attribute signal to AP. If Location permission granted to host terminal/app bundle, expected yes; retest `ssid()`/`bssid()` non-None after granting.
