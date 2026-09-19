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
import sys
import time
from dataclasses import dataclass
from typing import Optional, Tuple


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


# CWChannelBand enum (best-effort): 1 = 2.4GHz, 2 = 5GHz.
_CHANNEL_BANDS = {
    1: "2.4 GHz",
    2: "5 GHz",
}

# CWSecurity enum (best-effort; 8=WPA3 Personal verified live against
# macOS WiFi UI on a WPA3 network, others per Apple docs).
_SECURITY_MODES = {
    0: "open",
    1: "WEP",
    2: "WPA Personal",
    3: "WPA/WPA2 Personal",
    4: "WPA2 Personal",
    8: "WPA3 Personal",
}


def _security_name(mode: object) -> Optional[str]:
    try:
        code = int(mode)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(mode) if mode is not None else None
    return _SECURITY_MODES.get(code, "sec(%s)" % code)


def _band_name(channel: object) -> Optional[str]:
    if channel is None:
        return None
    try:
        code = int(channel.channelBand())  # type: ignore[union-attr]
    except (AttributeError, TypeError, ValueError):
        return None
    return _CHANNEL_BANDS.get(code)


def _mcs_value(iface: object) -> Optional[int]:
    try:
        fn = getattr(iface, "mcsIndex", None)
        if fn is None:
            return None
        v = fn() if callable(fn) else fn
    except Exception:
        return None
    if v is None:
        return None
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


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
    wlan_ch = None
    try:
        wlan_ch = iface.wlanChannel()
    except Exception:
        wlan_ch = None
    sec = None
    try:
        sec = _security_name(iface.securityMode())
    except Exception:
        sec = None
    return Signal(
        ssid=str(ssid) if ssid is not None else None,
        bssid=str(bssid) if bssid is not None else None,
        rssi=int(rssi),
        noise=int(noise) if noise is not None else None,
        snr=int(snr) if snr is not None else None,
        channel=_channel_str(wlan_ch),
        phy=_phy_name(iface.activePHYMode()),
        tx_rate=str(rate) if rate is not None else None,
        mcs=_mcs_value(iface),
        band=_band_name(wlan_ch),
        security=sec,
    )


def sample_signal(seconds: float = 5.0, read_fn=None,
                  sleep_fn=time.sleep) -> Signal:
    """Average WiFi signal over ``seconds``: poll now, then every 0.5s.

    rssi + noise averaged (rounded ints), snr recomputed from the
    averages; ssid/bssid/channel/phy/tx_rate/mcs/band/security come
    from the last successful sample. ``NoWiFiError``/
    ``SignalUnavailableError`` propagate immediately (no partial
    averaging). ``read_fn`` defaults to ``read_signal`` resolved at
    call time so tests can monkeypatch the module attribute.
    """
    if read_fn is None:
        read_fn = read_signal
    samples = [read_fn()]
    step = 0.5
    slept = 0.0
    while slept < seconds:
        sleep_fn(step)
        slept += step
        samples.append(read_fn())
    rssi_vals = [s.rssi for s in samples if s.rssi is not None]
    noise_vals = [s.noise for s in samples if s.noise is not None]
    avg_rssi = (int(round(sum(rssi_vals) / len(rssi_vals)))
                if rssi_vals else None)
    avg_noise = (int(round(sum(noise_vals) / len(noise_vals)))
                 if noise_vals else None)
    snr = (avg_rssi - avg_noise
           if avg_rssi is not None and avg_noise is not None else None)
    last = samples[-1]
    return Signal(
        ssid=last.ssid,
        bssid=last.bssid,
        rssi=avg_rssi,
        noise=avg_noise,
        snr=snr,
        channel=last.channel,
        phy=last.phy,
        tx_rate=last.tx_rate,
        mcs=last.mcs,
        band=last.band,
        security=last.security,
    )


_SIGNAL_RE = re.compile(
    r"Signal\s*/\s*Noise:\s*(-?\d+)\s*dBm\s*/\s*(-?\d+)\s*dBm")
_RATE_RE = re.compile(r"Transmit Rate:\s*(\S+)")
_PHY_RE = re.compile(r"PHY Mode:\s*(.+)")
_CHANNEL_RE = re.compile(r"Channel:\s*(\S[^\n]*)")

#: Timeouts (s) for the one-shot session identity lookup. Every
#: subprocess call has a timeout so walk/scan can never hang.
_SUDO_CHECK_TIMEOUT = 10.0
_SUDO_PROMPT_TIMEOUT = 120.0
_WDUTIL_TIMEOUT = 15.0

#: Tolerant `wdutil info` labels (case-insensitive, `:` or `=` sep).
#: verify: compare against real `sudo wdutil info` output on hardware;
#: unit fixtures below cover SSID/BSSID/BSS spellings only.
_SSID_RE = re.compile(
    r"(?im)^\s*(?:SSID|Network\s*Name)\s*[:=]\s*(.+?)\s*$")
_BSSID_RE = re.compile(
    r"(?im)^\s*(?:BSSID|BSS)\s*[:=]\s*([0-9A-Fa-f:\-]{11,})\s*$")


def _parse_wdutil_identity(text: str) -> Optional[Tuple[str, str]]:
    """Parse (ssid, bssid) from `wdutil info` output; None on failure."""
    if not text:
        return None
    ssid_m = _SSID_RE.search(text)
    bssid_m = _BSSID_RE.search(text)
    if not ssid_m or not bssid_m:
        return None
    ssid = ssid_m.group(1).strip().strip("\"'")
    bssid = bssid_m.group(1).strip()
    if not ssid or not bssid:
        return None
    return (ssid, bssid)


def _read_corewlan_identity() -> Optional[Tuple[str, str]]:
    """CoreWLAN ssid/bssid when Location-granted; None if redacted/missing."""
    try:
        from CoreWLAN import CWWiFiClient
    except Exception:
        return None
    try:
        client = CWWiFiClient.sharedWiFiClient()
        iface = client.interface() if client is not None else None
        if iface is None:
            return None
        ssid = iface.ssid()
        bssid = iface.bssid()
    except Exception:
        return None
    if ssid is None or bssid is None:
        return None
    ssid_s, bssid_s = str(ssid).strip(), str(bssid).strip()
    if not ssid_s or not bssid_s:
        return None
    return (ssid_s, bssid_s)


def _run_wdutil_info() -> Optional[Tuple[str, str]]:
    """Run `sudo -n wdutil info` once and parse; None on any failure."""
    try:
        proc = subprocess.run(
            ["sudo", "-n", "wdutil", "info"],
            capture_output=True, text=True, timeout=_WDUTIL_TIMEOUT,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    try:
        return _parse_wdutil_identity(proc.stdout or "")
    except Exception:
        return None


def read_network_identity() -> Optional[Tuple[str, str]]:
    """Session network identity: (ssid, bssid) or None.

    Order: (a) CoreWLAN current ssid/bssid when non-None (no sudo
    needed); (b) else one privileged ``wdutil info`` lookup: if
    ``sudo -n true`` succeeds, read directly; otherwise run ``sudo -v``
    ONCE (user prompted in terminal, cached ~5min) then read. Non-tty
    sessions skip the prompt and return None. Abort/wrong-password,
    timeouts, and parse failures all return None — never raise, never
    hang (every subprocess call has a timeout).

    verify: run ``sudo wdutil info`` on real hardware and confirm
    ``_parse_wdutil_identity`` extracts the SSID/BSSID labels shown.
    """
    found = _read_corewlan_identity()
    if found is not None:
        return found
    try:
        check = subprocess.run(
            ["sudo", "-n", "true"],
            capture_output=True, text=True, timeout=_SUDO_CHECK_TIMEOUT,
        )
    except Exception:
        return None
    if check.returncode == 0:
        return _run_wdutil_info()
    try:
        if not sys.stdin.isatty():
            return None
    except Exception:
        return None
    try:
        prompt = subprocess.run(
            ["sudo", "-v"],
            timeout=_SUDO_PROMPT_TIMEOUT,
        )
    except Exception:
        return None
    if prompt.returncode != 0:
        return None
    return _run_wdutil_info()


#: Timeout (s) for each one-shot local-addr subprocess call.
_ADDR_TIMEOUT = 5.0


def _run_ipconfig(args) -> Optional[str]:
    """Run one ipconfig query; stripped stdout or None on any failure."""
    try:
        proc = subprocess.run(
            ["ipconfig"] + list(args),
            capture_output=True, text=True, timeout=_ADDR_TIMEOUT,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    try:
        out = (proc.stdout or "").strip()
    except Exception:
        return None
    return out or None


def _read_hw_address() -> Optional[str]:
    """CoreWLAN hardwareAddress(); None on any failure (never raise)."""
    try:
        from CoreWLAN import CWWiFiClient
    except Exception:
        return None
    try:
        client = CWWiFiClient.sharedWiFiClient()
        iface = client.interface() if client is not None else None
        if iface is None:
            return None
        mac = iface.hardwareAddress()
    except Exception:
        return None
    if mac is None:
        return None
    try:
        s = str(mac).strip()
    except Exception:
        return None
    return s or None


def read_local_addrs():
    # type: () -> Optional[Tuple[Optional[str], Optional[str], Optional[str]]]
    """Session one-shot: (ip, router, mac) or None when all unknown.

    IP via ``ipconfig getifaddr en0`` (verified returns 192.168.178.50);
    router via ``ipconfig getoption en0 router``
    (verified returns 192.168.178.1); MAC via CoreWLAN
    ``hardwareAddress()`` (verified live). No sudo needed. Never raise;
    each lookup has a timeout; per-field failure yields None for that
    field; None overall only when every field is missing.
    """
    try:
        ip = _run_ipconfig(["getifaddr", "en0"])
    except Exception:
        ip = None
    try:
        router = _run_ipconfig(["getoption", "en0", "router"])
    except Exception:
        router = None
    try:
        mac = _read_hw_address()
    except Exception:
        mac = None
    if not ip and not router and not mac:
        return None
    return (ip, router, mac)


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
