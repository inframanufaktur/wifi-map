"""Pure formatting and input helpers shared by walk interfaces."""
from __future__ import annotations

import math
import os
import textwrap
from collections import deque
from typing import Callable, List, Optional, Tuple

from wifimap import signal as signal_mod
from wifimap.path_monitor import PathSnapshot, ProbeSummary


def format_walk_header(ssid: Optional[str], location: Optional[str],
                       pending: int) -> str:
    """Summarize the active network, capture target, and save queue."""
    network = ssid or "unknown network"
    target = location or "no spot selected"
    queue = "%d queued" % pending if pending else "ready"
    return "WIFI-MAP · %s · %s · %s" % (network, target, queue)


def format_walk_footer() -> str:
    """Compact, stable command legend for the walk dashboard."""
    return ("[s] save  [t] throughput  [c] compare  [b] benchmark  "
            "[l] switch  [n] new  [q] quit")


def format_walk_link_status(ch: Optional[str], phy: Optional[str],
                            tx: Optional[str], mcs: object,
                            band: Optional[str], security: Optional[str]) -> str:
    """Put connection metadata in a compact, scan-friendly status rail."""
    return "LINK  ch %s · %s · tx %s · mcs %s · %s · %s" % (
        ch or "-", phy or "-", tx or "-", "-" if mcs is None else mcs,
        band or "-", security or "-",
    )


def format_walk_signal_summary(rssi: str, rssi_rating: str, snr: str,
                               snr_rating: str, noise: str) -> str:
    """One-line instrument readout for compact and comparison layouts."""
    return "NOW · RSSI %s [%s] · SNR %s [%s] · noise %s" % (
        rssi, rssi_rating, snr, snr_rating, noise)


def format_walk_traffic_summary(down: str, up: str) -> str:
    """One-line passive-traffic readout for compact layouts."""
    return "TRAFFIC · ↓ %s · ↑ %s" % (down, up)


def _path_current_pair(summary: ProbeSummary) -> int:
    if not summary.count:
        return 0
    return 3 if summary.current_ms is None else 1


def _path_loss_pair(summary: ProbeSummary) -> int:
    return 2 if summary.loss_pct else 0


def _path_spark_segments(summary: ProbeSummary,
                         width: int) -> List[Tuple[str, int]]:
    """Colour only confirmed loss marks; latency history stays neutral."""
    segments: List[Tuple[str, int]] = []
    for char in summary.sparkline(width):
        pair = 3 if char == "×" else 0
        if segments and segments[-1][1] == pair:
            text, _old_pair = segments[-1]
            segments[-1] = (text + char, pair)
        else:
            segments.append((char, pair))
    return segments


def _path_row(label: str, summary: ProbeSummary,
              width: int) -> List[Tuple[str, int]]:
    if not summary.count:
        current = p95 = loss = "-"
    else:
        current = ("timeout" if summary.current_ms is None
                   else "%.0f ms" % summary.current_ms)
        p95 = "-" if summary.p95_ms is None else "%.0f" % summary.p95_ms
        loss = "%.0f%%" % (summary.loss_pct or 0.0)
    segments = [
        ("%-14s " % label, 0),
        ("%7s" % current, _path_current_pair(summary)),
        ("  p95 %3s  loss " % p95, 0),
        (loss, _path_loss_pair(summary)),
        ("  ", 0),
    ]
    graph_width = max(0, width - sum(len(text) for text, _pair in segments))
    return segments + _path_spark_segments(summary, graph_width)


def _compact_path_value(label: str, summary: ProbeSummary,
                        spark_width: int = 2) -> List[Tuple[str, int]]:
    if not summary.count:
        return [("%s -" % label, 0)]
    current = ("timeout" if summary.current_ms is None
               else "%.0fms" % summary.current_ms)
    p95 = "-" if summary.p95_ms is None else "%.0f" % summary.p95_ms
    loss = "%.0f%%" % (summary.loss_pct or 0.0)
    return [
        ("%s " % label, 0),
        (current, _path_current_pair(summary)),
        (" p95 %s loss " % p95, 0),
        (loss, _path_loss_pair(summary)),
        (" ", 0),
    ] + _path_spark_segments(summary, spark_width)


def path_panel_rows(snapshot: PathSnapshot,
                    width: int,
                    compact: bool = False) -> List[List[Tuple[str, int]]]:
    if not snapshot.gateway.count and not snapshot.internet.count:
        return []
    if compact:
        router = _compact_path_value("router", snapshot.gateway)
        internet = _compact_path_value("internet", snapshot.internet)
        return [[("PATH  ", 0)] + router + [("  ·  ", 0)] + internet]
    return [
        _path_row("PATH  router", snapshot.gateway, width),
        _path_row("      internet", snapshot.internet, width),
    ]


def walk_display_mode(height: int, comparing: bool) -> str:
    """Use the full instrument panel whenever the terminal can fit it."""
    # A comparison adds information; it must not make live signal history
    # disappear. ``comparing`` remains an explicit argument so callers state
    # their context and future truly constrained layouts can use it.
    _ = comparing
    if height >= 10:
        return "detailed"
    return "compact"


def walk_layout_mode(width: int, comparing: bool) -> str:
    """Use the unused width for a live/compare side-by-side dashboard."""
    if comparing and width >= 140:
        return "side_by_side"
    return "stacked"


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

METER_VAL_W = 10
METER_LABEL_W = 6
_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def rating_style(rating: str) -> Tuple[int, str]:
    """Map GREAT/OK/WEAK/UNKNOWN to (curses_pair, ansi_code)."""
    return _RATING_STYLE.get(rating, (0, "37"))


def format_meter_left(label: str, value: str, rating: Optional[str]) -> str:
    """Format the aligned label, value, and optional rating of a meter."""
    label_cell = (label if label is not None else "").ljust(
        METER_LABEL_W)[:METER_LABEL_W]
    value_cell = (value if value is not None else "UNKNOWN").ljust(
        METER_VAL_W)
    if rating:
        return "%s%s[%s]" % (label_cell, value_cell, rating)
    return "%s%s" % (label_cell, value_cell)


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
    """Fixed-length sample window; resizing resamples, never drops history."""

    def __init__(self, maxlen: int = 60) -> None:
        self._buf: deque = deque(maxlen=max(1, maxlen))

    def append(self, v: Optional[float]) -> None:
        self._buf.append(v)

    def maximum(self, minimum: float = 1.0) -> float:
        return max([minimum] + [v for v in self._buf if v is not None])

    def chart(self, lo: float, hi: float, width: int, height: int = 3,
              rating: Optional[Callable] = None,
              align: str = "right",
              allow_full_block: bool = True) -> List[List[Tuple[str, int]]]:
        """Block chart with per-column colours and eight substeps per row.

        The full ring capacity defines the horizontal window, including
        blank time during warmup. Narrow columns average their samples;
        wider columns repeat them. Missing-only buckets remain gaps.
        """
        height = max(1, height)
        rows: List[List[Tuple[str, int]]] = [[] for _ in range(height)]
        values = list(self._buf)
        if align == "right":
            values = [None] * (self._buf.maxlen - len(values)) + values
        for column in range(max(0, width)):
            start = column * len(values) // width
            end = max(start + 1, (column + 1) * len(values) // width)
            samples = [v for v in values[start:end] if v is not None]
            if not samples:
                for row in rows:
                    row.append((" ", 0))
                continue
            value = sum(samples) / len(samples)
            fraction = (value - lo) / (hi - lo) if hi > lo else 1.0
            fraction = max(0.0, min(1.0, fraction))
            # A real sample at the lower bound still has a visible baseline.
            level = 1 + round(fraction * (height * 8 - 1))
            pair = rating_style(rating(value))[0] if rating else 0
            for index, row in enumerate(rows):
                fill = max(0, min(8, level - (height - index - 1) * 8))
                glyph = _SPARK_CHARS[fill - 1] if fill else " "
                if not allow_full_block and glyph == "█":
                    glyph = "▇"
                row.append((glyph, pair))
        return rows

def history_cap(interval: float) -> int:
    """Return the number of samples covering about sixty seconds."""
    return max(1, int(math.ceil(60.0 / interval)))


def signal_panel_rows(state: "WalkState", width: int,
                      chart_height: int = 1,
                      compact_path: bool = False) -> List[List[Tuple[str, int]]]:
    """Shared curses/ANSI instruments; style numbers are semantic pairs."""
    sig = state.sig
    ap = sig.bssid or state.net_bssid or "unknown"
    rows = [[("SIGNAL", 4)]]
    left_width = 24
    graph_cols = max(1, width - left_width - 2)
    for label, value, unit, history, lo, hi, rating in (
        ("RSSI", sig.rssi, "dBm", state.hist_rssi, -90, -30, rate_rssi),
        ("SNR", sig.snr, "dB", state.hist_snr, 0, 60, rate_snr),
        ("noise", sig.noise, "dBm", state.hist_noise, -100, -60, None),
    ):
        value_text = "UNKNOWN" if value is None else "%d %s" % (value, unit)
        quality = rating(value) if rating else None
        pair = rating_style(quality)[0] if quality else 0
        graph = history.chart(
            lo, hi, graph_cols, chart_height, rating,
            allow_full_block=label != "noise")
        for index, cells in enumerate(graph):
            if index == 0:
                prefix = format_meter_left(label, value_text, quality)
            elif index == chart_height - 1:
                prefix = "      %s…%s %s" % (lo, hi, unit)
            else:
                prefix = ""
            rows.append([(prefix.ljust(left_width) + "  ",
                          pair if index == 0 else 0)] + cells)
    rows.append([])
    ap_name = state.access_point_names.get(ap.lower()) if ap != "unknown" else None
    ap_line = "AP    %s" % ap
    if ap_name:
        ap_line = "AP    %s · %s" % (ap_name, ap)
    rows.append([(ap_line, 0)])
    link = format_walk_link_status(
        sig.channel, sig.phy, sig.tx_rate, sig.mcs, sig.band, sig.security)
    rows.extend([[(line, 0)] for line in textwrap.wrap(link, max(1, width))])
    path_rows = path_panel_rows(
        state.path_snapshot(), width, compact=compact_path)
    if path_rows:
        rows.append([])
        rows.extend(path_rows)
    rows.append([])
    traffic_hi = max(state.hist_down.maximum(), state.hist_up.maximum())
    rows.append([("TRAFFIC · scale 0–%.1f Mbps" % traffic_hi, 4)])
    for index, (label, history) in enumerate((
        ("traffic down", state.hist_down), ("traffic up", state.hist_up),
    )):
        value = state.last_rates[index] if state.last_rates else None
        prefix = (label.ljust(13) + fmt_rate_val(value)).ljust(left_width)
        rows.append([(prefix + "  ", 0)]
                    + history.chart(0, traffic_hi, graph_cols, 1)[0])
    return rows


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
