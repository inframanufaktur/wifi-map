"""Pure formatting and input helpers shared by walk interfaces."""
from __future__ import annotations

import math
import os
from collections import deque
from typing import Callable, Dict, List, Optional, Tuple

from wifimap import signal as signal_mod


def format_signal_line(sig: signal_mod.Signal) -> str:
    """One-line live readout; None fields render as UNKNOWN/-."""

    def _value(value: object) -> str:
        return "-" if value is None else str(value)

    rssi = "UNKNOWN" if sig.rssi is None else "%d dBm" % sig.rssi
    noise = "UNKNOWN" if sig.noise is None else "%d dBm" % sig.noise
    snr = "UNKNOWN" if sig.snr is None else "%d dB" % sig.snr
    return "rssi=%s noise=%s snr=%s ssid=%s bssid=%s ch=%s phy=%s tx=%s" % (
        rssi, noise, snr, _value(sig.ssid), _value(sig.bssid),
        _value(sig.channel), _value(sig.phy), _value(sig.tx_rate),
    )


def format_net_line(ssid: Optional[str]) -> str:
    """Session header: ``Net: <ssid>`` or ``Net: unknown``."""
    return "Net: %s" % (ssid if ssid else "unknown")


def fmt_mbps(v: Optional[float]) -> str:
    """Compact Mbps value; None renders as ``-`` (matches table style)."""
    return "-" if v is None else "%.1f" % v


def fmt_rate_val(v: Optional[float]) -> str:
    """Meter value cell for live down/up: ``267.5 Mbps`` / ``-``."""
    return "-" if v is None else "%.1f Mbps" % v


def rate_rssi(v: Optional[int]) -> str:
    """Rate RSSI: >=-60 GREAT, >=-70 OK, else WEAK, None UNKNOWN."""
    if v is None:
        return "UNKNOWN"
    if v >= -60:
        return "GREAT"
    if v >= -70:
        return "OK"
    return "WEAK"


def rate_snr(v: Optional[int]) -> str:
    """Rate SNR: >=25 GREAT, >=15 OK, else WEAK, None UNKNOWN."""
    if v is None:
        return "UNKNOWN"
    if v >= 25:
        return "GREAT"
    if v >= 15:
        return "OK"
    return "WEAK"


_RATING_STYLE = {
    "GREAT": (1, "32"),
    "OK": (2, "33"),
    "WEAK": (3, "31"),
    "UNKNOWN": (0, "37"),
}

WIDE_MIN_WIDTH = 100
METER_VAL_W = 10
METER_LABEL_W = 6
_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def rating_style(rating: str) -> Tuple[int, str]:
    """Map GREAT/OK/WEAK/UNKNOWN to (curses_pair, ansi_code)."""
    return _RATING_STYLE.get(rating, (0, "37"))


def layout_mode(width: int) -> str:
    """Wide side-by-side at >=100 cols, else stacked narrow."""
    return "wide" if width >= WIDE_MIN_WIDTH else "narrow"


def graph_width(total_w: int, label_len: int = 6, suffix_len: int = 6) -> int:
    """Width for narrow stacked graph line so label+bar+suffix fits w-1."""
    return max(10, total_w - label_len - suffix_len - 1)


def wide_graph_width(total_w: int, left_len: int, label_len: int = 6,
                     sep_len: int = 3, suffix_len: int = 0) -> int:
    """Width for wide side-by-side graph so full line fits w-1."""
    return max(10, total_w - 1 - left_len - sep_len - label_len - suffix_len)


def grouped_graph_width(total_w: int, prefixes) -> int:
    """Shared bar width so grouped metric+graph rows right-align."""
    lengths = []
    for prefix in prefixes:
        try:
            lengths.append(
                len(prefix) if isinstance(prefix, str) else int(prefix))
        except Exception:
            continue
    max_prefix = max(lengths) if lengths else 0
    return max(10, total_w - max_prefix - len(" [60s]") - 1)


def format_meter_left(label: str, value: str, rating: Optional[str]) -> str:
    """Format the aligned label, value, and optional rating of a meter."""
    label_cell = (label if label is not None else "").ljust(
        METER_LABEL_W)[:METER_LABEL_W]
    value_cell = (value if value is not None else "UNKNOWN").ljust(
        METER_VAL_W)
    if rating:
        return "%s%s[%s]" % (label_cell, value_cell, rating)
    return "%s%s" % (label_cell, value_cell)


def format_meter_row(label: str, value: str, rating: Optional[str],
                     bar: str, max_left: int) -> str:
    """Full meter row with ``|`` separator and ``[60s]`` suffix."""
    left = format_meter_left(label, value, rating).ljust(max_left)
    return "%s | %s [60s]" % (left, bar)


def meter_layout(total_w: int, lefts: List[str]) -> Tuple[int, int]:
    """Return ``(maxLeft, graph_width)`` for aligned grouped rows."""
    max_left = max((len(value) for value in lefts), default=0)
    width = max(10, total_w - max_left - len(" | ") - len(" [60s]") - 1)
    return (max_left, width)


def format_extra_line(ch: str, phy: str, tx: str) -> str:
    """Format channel, PHY, and transmit-rate details."""
    return "ch %s phy %s tx %s" % (ch, phy, tx)


def format_radio_line(mcs: object, band: Optional[str],
                      security: Optional[str]) -> str:
    """Format radio metadata, rendering missing values as ``-``."""
    return "mcs %s band %s sec %s" % (
        "-" if mcs is None else str(mcs),
        band if band else "-",
        security if security else "-",
    )


def format_addr_line(ip: Optional[str], router: Optional[str],
                     mac: Optional[str]) -> str:
    """Format the address header, or return empty when all values are unknown."""
    if not ip and not router and not mac:
        return ""
    return "IP %s RTR %s MAC %s" % (ip or "-", router or "-", mac or "-")


def ansi_wrap(s: str, code: str) -> str:
    """Wrap text in ANSI colour unless ``NO_COLOR`` is set."""
    if os.environ.get("NO_COLOR"):
        return s
    return "\x1b[%sm%s\x1b[0m" % (code, s)


class SparkHistory:
    """Fixed-length sample ring with sparkline rendering."""

    def __init__(self, maxlen: int = 60) -> None:
        self._buf: deque = deque(maxlen=max(1, maxlen))

    def append(self, v: Optional[float]) -> None:
        self._buf.append(v)

    def sparkline(self, lo: float, hi: float, width: int,
                  align: str = "right") -> str:
        values = list(self._buf)[-width:] if width > 0 else []
        if not values:
            return ""
        span = hi - lo
        output = []
        for value in values:
            if value is None:
                output.append(" ")
                continue
            if span <= 0:
                level = 6
            else:
                fraction = (value - lo) / span
                fraction = max(0.0, min(1.0, fraction))
                level = int(round(fraction * 6))
            output.append(_SPARK_CHARS[level])
        sparkline = "".join(output)
        if align == "right" and len(sparkline) < width:
            sparkline = " " * (width - len(sparkline)) + sparkline
        return sparkline

    def sparkline_auto(self, width: int, min_hi: float = 1.0) -> str:
        """Render values scaled from zero to their maximum."""
        values = [value for value in self._buf if value is not None]
        hi = max(values) if values else min_hi
        return self.sparkline(0.0, max(hi, min_hi), width)


def history_cap(interval: float) -> int:
    """Return the number of samples covering about sixty seconds."""
    return max(1, int(math.ceil(60.0 / interval)))


def picker_start_cursor(loc_ids: List[int], active: Optional[int]) -> int:
    """Return the active location's picker index, defaulting to zero."""
    if active is not None:
        try:
            return loc_ids.index(active)
        except ValueError:
            pass
    return 0


def picker_move(cursor: int, direction: int, count: int) -> int:
    """Move with wraparound over the locations and create row."""
    return (cursor + direction) % (count + 1)


def picker_press(keypress: str, cursor: int,
                 count: int) -> Tuple[int, str, Optional[int]]:
    """Map a picker keypress to a cursor, action, and selected index."""
    if keypress in ("", "\n", "\r", "enter"):
        return (cursor, "confirm", cursor)
    if keypress in ("q", "Q", "\x1b", "esc"):
        return (cursor, "cancel", None)
    if keypress in ("up", "down"):
        step = -1 if keypress == "up" else 1
        return (picker_move(cursor, step, count), "move", None)
    if keypress == "+":
        return (count, "confirm", count)
    if len(keypress) == 1 and "1" <= keypress <= "9":
        index = int(keypress) - 1
        if 0 <= index < count:
            return (index, "confirm", index)
        return (cursor, "ignore", None)
    return (cursor, "ignore", None)


def parse_floor_input(s: str) -> int:
    """Parse a floor integer, with negative values representing basements."""
    stripped = s.strip()
    if not stripped:
        raise ValueError("floor must be an integer, got empty input")
    try:
        return int(stripped, 10)
    except ValueError as exc:
        raise ValueError("floor must be an integer, got %r" % (s,)) from exc


def snapshot_payload(sig: signal_mod.Signal) -> Dict[str, object]:
    """Freeze signal data into ``store.add_reading`` keyword arguments."""
    return {
        "ssid": sig.ssid,
        "bssid": sig.bssid,
        "rssi": sig.rssi,
        "noise": sig.noise,
        "snr": sig.snr,
        "channel": sig.channel,
        "phy": sig.phy,
        "tx_rate": sig.tx_rate,
    }


def attempt_read(read_fn: Callable[[], signal_mod.Signal],
                 max_retries: int = 3) -> signal_mod.Signal:
    """Retry transient reads, propagate no-WiFi, then return unknown data."""
    for _ in range(max_retries + 1):
        try:
            return read_fn()
        except signal_mod.NoWiFiError:
            raise
        except Exception:
            pass
    return signal_mod.Signal()
