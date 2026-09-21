"""Slow system_profiler fallback for Wi-Fi signal readings."""
from __future__ import annotations

import re
import subprocess

from wifimap.signal_models import NoWiFiError, Signal, SignalUnavailableError


_SIGNAL_RE = re.compile(
    r"Signal\s*/\s*Noise:\s*(-?\d+)\s*dBm\s*/\s*(-?\d+)\s*dBm")
_RATE_RE = re.compile(r"Transmit Rate:\s*(\S+)")
_PHY_RE = re.compile(r"PHY Mode:\s*(.+)")
_CHANNEL_RE = re.compile(r"Channel:\s*(\S[^\n]*)")


def read_signal_profiler(timeout: float = 30.0) -> Signal:
    """Slow fallback via ``system_profiler SPAirPortDataType`` (~4.5s).

    SSID/BSSID are redacted by the OS here, so always ``None``.
    """
    try:
        proc = subprocess.run(
            ["system_profiler", "SPAirPortDataType"],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise SignalUnavailableError("system_profiler not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise SignalUnavailableError(
            "system_profiler timed out after %ss" % (timeout,)) from exc
    if proc.returncode != 0:
        raise SignalUnavailableError(
            "system_profiler failed (rc=%s)" % (proc.returncode,))
    out = proc.stdout or ""
    m = _SIGNAL_RE.search(out)
    if not m:
        raise NoWiFiError("no associated network in system_profiler output")
    rssi, noise = int(m.group(1)), int(m.group(2))
    rate = _RATE_RE.search(out)
    phy = _PHY_RE.search(out)
    channel = _CHANNEL_RE.search(out)
    return Signal(
        ssid=None,
        bssid=None,
        rssi=rssi,
        noise=noise,
        snr=rssi - noise,
        channel=channel.group(1).strip() if channel else None,
        phy=phy.group(1).strip() if phy else None,
        tx_rate=rate.group(1).strip() if rate else None,
    )
