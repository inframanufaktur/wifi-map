"""WiFi signal collector: CoreWLAN via PyObjC, system_profiler fallback.

Primary path (`read_signal`) uses ``CoreWLAN.CWWiFiClient`` (probe A:
~5.4ms median poll). The ``CoreWLAN`` import lives inside the function so
a missing PyObjC install surfaces as a clean ``SignalUnavailableError``
instead of breaking ``import wifimap.signal``.

Note (macOS 26): ``ssid()``/``bssid()`` return ``None`` without Location
permission for the host app. ``None`` SSID/BSSID is normal — readings are
keyed by manual location tags, not SSID.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Optional


class NoWiFiError(Exception):
    """Raised when WiFi is off or not associated (scan exits 2, walk shows NO-WIFI)."""


class SignalUnavailableError(Exception):
    """Raised when no signal backend works (PyObjC missing, profiler failed)."""


@dataclass
class Signal:
    ssid: Optional[str] = None
    bssid: Optional[str] = None
    rssi: Optional[int] = None
    noise: Optional[int] = None
    snr: Optional[int] = None
    channel: Optional[str] = None
    phy: Optional[str] = None
    tx_rate: Optional[str] = None


# CWPHYMode enum (Apple docs): 0 none, 1 a, 2 b, 3 g, 4 n, 5 ac, 6 ax.
_PHY_MODES = {
    0: "none",
    1: "802.11a",
    2: "802.11b",
    3: "802.11g",
    4: "802.11n",
    5: "802.11ac",
    6: "802.11ax",
}

# CWChannelWidth enum: 0 unknown, 1 20MHz, 2 40MHz, 3 80MHz, 4 160MHz.
_CHANNEL_WIDTHS = {
    0: None,
    1: "20MHz",
    2: "40MHz",
    3: "80MHz",
    4: "160MHz",
}


def _phy_name(mode: object) -> Optional[str]:
    try:
        code = int(mode)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(mode) if mode is not None else None
    return _PHY_MODES.get(code, "unknown(%d)" % code)


def _channel_str(channel: object) -> Optional[str]:
    if channel is None:
        return None
    try:
        number = int(channel.channelNumber())  # type: ignore[union-attr]
    except (AttributeError, TypeError, ValueError):
        return str(channel)
    width: Optional[str] = None
    try:
        width = _CHANNEL_WIDTHS.get(int(channel.channelWidth()))  # type: ignore[union-attr]
    except (AttributeError, TypeError, ValueError):
        width = None
    if width:
        return "%d (%s)" % (number, width)
    return str(number)


def read_signal(timeout: float = 2.0) -> Signal:
    """Poll current association via CoreWLAN. ``timeout`` is reserved for
    API parity with the profiler fallback (CoreWLAN polls take ~5ms)."""
    try:
        from CoreWLAN import CWWiFiClient
    except ImportError as exc:
        raise SignalUnavailableError(
            "PyObjC CoreWLAN not available: pip install "
            "pyobjc-framework-CoreWLAN (on Python 3.9, upgrade pip first: "
            "pip install --upgrade pip)"
        ) from exc
    client = CWWiFiClient.sharedWiFiClient()
    iface = client.interface() if client is not None else None
    if iface is None:
        raise NoWiFiError("no WiFi interface (WiFi off?)")
    rssi = iface.rssiValue()
    noise = iface.noiseMeasurement()
    if rssi is None or rssi == 0:
        raise NoWiFiError("WiFi not associated (RSSI %r)" % (rssi,))
    ssid = iface.ssid()
    bssid = iface.bssid()
    snr = rssi - noise if noise is not None else None
    rate = iface.transmitRate()
    return Signal(
        ssid=str(ssid) if ssid is not None else None,
        bssid=str(bssid) if bssid is not None else None,
        rssi=int(rssi),
        noise=int(noise) if noise is not None else None,
        snr=int(snr) if snr is not None else None,
        channel=_channel_str(iface.wlanChannel()),
        phy=_phy_name(iface.activePHYMode()),
        tx_rate=str(rate) if rate is not None else None,
    )


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
