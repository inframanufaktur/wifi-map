"""Public signal readings and backend errors."""
from __future__ import annotations

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
    mcs: Optional[int] = None
    band: Optional[str] = None
    security: Optional[str] = None
