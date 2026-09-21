"""Interactive terminal evaluation of saved WiFi readings."""
from __future__ import annotations

import os
import sys
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple

from wifimap import evaluation


KIND_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("location", "Location"),
    ("ssid", "SSID"),
)


@dataclass(frozen=True)
class RenderLine:
    text: str
    style: str = "normal"


class EvalState:
    """Pure navigation and analysis state shared by curses and fallback UIs."""

    def __init__(self, report: evaluation.Report) -> None:
        self.report = report
        self.screen = "kind"
        self.kind_index = 0
        self.scopes: List[evaluation.ScopeOption] = []
        self.scope_index = 0
        self.scope: Optional[evaluation.ScopeOption] = None
        self.grouped = True
        self.metric_index = 0
        self.reverse = False
        self.cursor = 0
        self._rows: List[evaluation.AnalysisRow] = []

    @property
    def metric(self) -> evaluation.MetricSpec:
        return evaluation.METRICS[self.metric_index]

    @property
    def rows(self) -> List[evaluation.AnalysisRow]:
        return self._rows

    def _refresh_rows(self) -> None:
        if self.scope is None:
            self._rows = []
            return
        readings = evaluation.filter_scope(self.report.readings, self.scope)
        rows = evaluation.analysis_rows(readings, grouped=self.grouped)
        self._rows = evaluation.rank_rows(
            rows, self.metric.key, reverse=self.reverse)
        self.cursor = min(self.cursor, max(0, len(self._rows) - 1))

    def _move(self, current: int, direction: int, count: int) -> int:
        if count <= 0:
            return 0
        return (current + direction) % count

    def press(self, key: str) -> str:
        """Apply a normalized key and return ``continue`` or ``quit``."""
        if key in ("q", "Q", "ctrl-c"):
            return "quit"
        if self.screen == "kind":
            if key in ("up", "k"):
                self.kind_index = self._move(
                    self.kind_index, -1, len(KIND_OPTIONS))
            elif key in ("down", "j"):
                self.kind_index = self._move(
                    self.kind_index, 1, len(KIND_OPTIONS))
            elif key == "home":
                self.kind_index = 0
            elif key == "end":
                self.kind_index = len(KIND_OPTIONS) - 1
            elif key == "enter" and self.report.readings:
                kind = KIND_OPTIONS[self.kind_index][0]
                self.scopes = evaluation.scope_options(
                    self.report.readings, kind)
                self.scope_index = 0
                self.screen = "scope"
            return "continue"

        if self.screen == "scope":
            if key == "esc":
                self.screen = "kind"
            elif key in ("up", "k"):
                self.scope_index = self._move(
                    self.scope_index, -1, len(self.scopes))
            elif key in ("down", "j"):
                self.scope_index = self._move(
                    self.scope_index, 1, len(self.scopes))
            elif key == "home":
                self.scope_index = 0
            elif key == "end" and self.scopes:
                self.scope_index = len(self.scopes) - 1
            elif key == "enter" and self.scopes:
                self.scope = self.scopes[self.scope_index]
                self.cursor = 0
                self._refresh_rows()
                self.screen = "dashboard"
            return "continue"

        if key == "esc":
            self.screen = "scope"
            return "continue"
        if key in ("tab", "shift-tab", "m"):
            step = -1 if key == "shift-tab" else 1
            self.metric_index = (self.metric_index + step) % len(
                evaluation.METRICS)
            self.cursor = 0
            self._refresh_rows()
        elif key == "r":
            self.reverse = not self.reverse
            self.cursor = 0
            self._refresh_rows()
        elif key == "v":
            self.grouped = not self.grouped
            self.cursor = 0
            self._refresh_rows()
        elif key in ("up", "k"):
            self.cursor = max(0, self.cursor - 1)
        elif key in ("down", "j"):
            self.cursor = min(max(0, len(self.rows) - 1), self.cursor + 1)
        elif key == "home":
            self.cursor = 0
        elif key == "end":
            self.cursor = max(0, len(self.rows) - 1)
        return "continue"


def _clip(text: str, width: int) -> str:
    safe = "".join(
        " " if unicodedata.category(char) in ("Cc", "Cf") else char
        for char in text
    )
    return safe[:max(0, width)]


def _line(text: str, width: int, style: str = "normal") -> RenderLine:
    return RenderLine(_clip(text, width), style)


def _fmt(value: Optional[float], decimals: int = 1) -> str:
    if value is None:
        return "-"
    if decimals == 0:
        return str(int(round(value)))
    return ("%%.%df" % decimals) % value


def _rating(metric: str, value: Optional[float]) -> str:
    if value is None:
        return "UNKNOWN"
    if metric == "rssi":
        return "GREAT" if value >= -60 else ("OK" if value >= -70 else "WEAK")
    if metric == "snr":
        return "GREAT" if value >= 25 else ("OK" if value >= 15 else "WEAK")
    return ""


def _focus_value(row: evaluation.AnalysisRow,
                 metric: evaluation.MetricSpec) -> str:
    value = getattr(row, metric.key)
    text = "%s %s" % (_fmt(value, metric.decimals), metric.unit)
    rating = _rating(metric.key, value)
    return "%s [%s]" % (text, rating) if rating else text


def _place(row: evaluation.AnalysisRow,
           max_length: Optional[int] = None) -> str:
    floor = "-" if row.floor is None else str(row.floor)
    if max_length is None:
        return "%s / %s / %s" % (row.room_name, floor, row.spot_name)
    label_budget = max(2, max_length - len(floor) - len(" /  / "))
    room_width = max(1, label_budget // 2)
    spot_width = max(1, label_budget - room_width)
    return "%s / %s / %s" % (
        row.room_name[:room_width], floor, row.spot_name[:spot_width])


def _render_kind(state: EvalState, width: int) -> List[RenderLine]:
    lines = [
        _line("WiFi evaluation", width, "title"),
        _line("source: %s" % state.report.source, width, "muted"),
        _line("", width),
    ]
    if not state.report.readings:
        lines.extend([
            _line("No readings found in this source.", width, "weak"),
            _line("Capture readings or choose another database/CSV.",
                  width, "muted"),
            _line("", width),
            _line("keys: q quit", width, "muted"),
        ])
        return lines
    lines.append(_line("Evaluate by", width, "title"))
    for index, (_, label) in enumerate(KIND_OPTIONS):
        lines.append(_line("  %s %s" % (
            ">" if index == state.kind_index else " ", label),
            width, "selected" if index == state.kind_index else "normal"))
    lines.extend([
        _line("", width),
        _line("keys: ↑/↓ or j/k move | Enter select | q quit",
              width, "muted"),
    ])
    return lines


def _render_scope(state: EvalState, width: int) -> List[RenderLine]:
    label = KIND_OPTIONS[state.kind_index][1]
    lines = [
        _line("WiFi evaluation", width, "title"),
        _line("source: %s" % state.report.source, width, "muted"),
        _line("", width),
        _line("Choose %s" % label, width, "title"),
    ]
    if not state.scopes:
        lines.append(_line("No %s values found." % label, width, "weak"))
    for index, option in enumerate(state.scopes):
        suffix = "reading" if option.count == 1 else "readings"
        lines.append(_line("  %s %-28s %d %s" % (
            ">" if index == state.scope_index else " ",
            option.label, option.count, suffix), width,
            "selected" if index == state.scope_index else "normal"))
    lines.extend([
        _line("", width),
        _line("keys: ↑/↓ or j/k move | Enter evaluate | Esc back | q quit",
              width, "muted"),
    ])
    return lines


def _summary_line(state: EvalState,
                  rows: List[evaluation.AnalysisRow]) -> str:
    summary = evaluation.metric_summary(rows, state.metric.key)
    return ("%s: min %s | median %s | max %s | missing %d" % (
        state.metric.label,
        _fmt(summary.minimum, state.metric.decimals),
        _fmt(summary.median, state.metric.decimals),
        _fmt(summary.maximum, state.metric.decimals),
        summary.missing,
    ))


def _wide_header() -> str:
    return (" # room / floor / spot             n   RSSI    SNR   ping"
            "    down     up    ΔRSSI   Δdown  focus rating")


def _wide_row(index: int, row: evaluation.AnalysisRow,
              metric: evaluation.MetricSpec) -> str:
    place = _place(row, 29)
    rating = _rating(metric.key, getattr(row, metric.key))
    return ("%2d %-29s %3d %6s %6s %6s %7s %7s %7s %7s %s" % (
        index + 1, place, row.count,
        _fmt(row.rssi, 0), _fmt(row.snr, 0), _fmt(row.ping_ms, 1),
        _fmt(row.down_mbps, 1), _fmt(row.up_mbps, 1),
        _fmt(row.delta_rssi, 0), _fmt(row.delta_down_mbps, 1), rating))


def _narrow_header(metric: evaluation.MetricSpec) -> str:
    return " # room / floor / spot          n  %s" % metric.label


def _narrow_row(index: int, row: evaluation.AnalysisRow,
                metric: evaluation.MetricSpec) -> str:
    place = _place(row, 27)
    return "%2d %-27s %2d  %s" % (
        index + 1, place, row.count, _focus_value(row, metric))


def _detail_lines(row: evaluation.AnalysisRow,
                  width: int) -> List[RenderLine]:
    area = "outdoor" if row.outdoors else "indoor"
    count_label = "reading" if row.count == 1 else "readings"
    context = "%s / %s" % (row.location_name, _place(row))
    return [
        _line("selected: %s | %d %s | %s | latest %s" % (
            context, row.count, count_label, area, row.latest_ts),
            width, "muted"),
        _line("network: SSID %s | BSSID %s" % (
            row.ssid or "Unknown", row.bssid or "-"), width, "muted"),
        _line("radio: channel %s | PHY %s | tx %s Mbps" % (
            row.channel or "-", row.phy or "-", _fmt(row.tx_rate, 0)),
            width, "muted"),
        _line("signal: RSSI %s dBm | noise %s dBm | SNR %s dB | "
              "ΔRSSI %s dB | ΔSNR %s dB" % (
                  _fmt(row.rssi, 0), _fmt(row.noise, 0), _fmt(row.snr, 0),
                  _fmt(row.delta_rssi, 0), _fmt(row.delta_snr, 0)),
              width, "muted"),
        _line("speed: ping %s ms | down %s Mbps (Δ %s) | "
              "up %s Mbps (Δ %s) | server %s" % (
                  _fmt(row.ping_ms, 1), _fmt(row.down_mbps, 1),
                  _fmt(row.delta_down_mbps, 1), _fmt(row.up_mbps, 1),
                  _fmt(row.delta_up_mbps, 1), row.server or "-"),
              width, "muted"),
        _line("note: %s" % (row.note or "-"), width, "muted"),
    ]


def _render_dashboard(state: EvalState, width: int,
                      height: int) -> List[RenderLine]:
    rows = state.rows
    scope = state.scope.label if state.scope is not None else "-"
    kind = KIND_OPTIONS[state.kind_index][1]
    view = "spots" if state.grouped else "readings"
    order = "best first" if state.reverse else "worst first"
    lines = [
        _line("WiFi evaluation — %s: %s" % (kind, scope), width, "title"),
        _line("source: %s" % state.report.source, width, "muted"),
        _line("view: %s | focus: %s | order: %s | %d rows" % (
            view, state.metric.label, order, len(rows)), width),
        _line(_summary_line(state, rows), width),
        _line("", width),
    ]
    wide = width >= 100
    lines.append(_line(
        _wide_header() if wide else _narrow_header(state.metric),
        width, "title"))

    cursor = min(state.cursor, max(0, len(rows) - 1))
    details = _detail_lines(rows[cursor], width) if rows else []
    reserved = len(lines) + len(details) + 1  # details + keys
    capacity = max(1, height - reserved)
    start = max(0, cursor - capacity + 1)
    end = min(len(rows), start + capacity)
    for index in range(start, end):
        row = rows[index]
        text = (_wide_row(index, row, state.metric) if wide
                else _narrow_row(index, row, state.metric))
        style = "selected" if index == cursor else _rating(
            state.metric.key, getattr(row, state.metric.key)).lower()
        lines.append(_line(text, width, style))

    if rows:
        lines.extend(details)
    else:
        lines.append(_line("No readings in this selection.", width, "weak"))
    lines.append(_line(
        "keys: ↑/↓ j/k move | Tab/Shift+Tab metric | r order | v view | Esc back | q quit",
        width, "muted"))
    return lines


def render(state: EvalState, width: int, height: int) -> List[RenderLine]:
    """Render the current screen into clipped semantic terminal lines."""
    width = max(1, width)
    height = max(1, height)
    if state.screen == "kind":
        lines = _render_kind(state, width)
    elif state.screen == "scope":
        lines = _render_scope(state, width)
    else:
        lines = _render_dashboard(state, width, height)
    return lines[:height]


def _normalize_key(ch: int, curses_mod) -> str:
    mapping = {
        curses_mod.KEY_UP: "up",
        curses_mod.KEY_DOWN: "down",
        curses_mod.KEY_HOME: "home",
        curses_mod.KEY_END: "end",
        curses_mod.KEY_ENTER: "enter",
        10: "enter", 13: "enter", 27: "esc", 9: "tab", 3: "ctrl-c",
    }
    key_btab = getattr(curses_mod, "KEY_BTAB", None)
    if key_btab is not None:
        mapping[key_btab] = "shift-tab"
    if ch in mapping:
        return mapping[ch]
    try:
        return chr(ch)
    except (ValueError, OverflowError):
        return ""


def _run_curses(stdscr, report: evaluation.Report) -> int:
    import curses

    state = EvalState(report)
    try:
        curses.curs_set(0)
    except Exception:
        pass
    colors = False
    try:
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_GREEN, -1)
            curses.init_pair(2, curses.COLOR_YELLOW, -1)
            curses.init_pair(3, curses.COLOR_RED, -1)
            colors = True
    except Exception:
        colors = False
    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        for row, line in enumerate(render(state, width - 1, height - 1)):
            attr = 0
            if line.style == "title":
                attr = curses.A_BOLD
            elif line.style == "muted":
                attr = curses.A_DIM
            elif line.style == "selected":
                attr = curses.A_REVERSE
            elif colors and line.style in ("great", "ok", "weak"):
                pair = {"great": 1, "ok": 2, "weak": 3}[line.style]
                attr = curses.color_pair(pair)
            try:
                stdscr.addstr(row, 0, line.text, attr)
            except Exception:
                pass
        stdscr.refresh()
        key = _normalize_key(stdscr.getch(), curses)
        if state.press(key) == "quit":
            return 0


def _run_fallback(report: evaluation.Report) -> int:
    state = EvalState(report)
    while True:
        if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
            print("\x1b[2J\x1b[H", end="")
        for line in render(state, 100, 24):
            print(line.text)
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        key = "enter" if raw == "" else raw
        aliases = {"up": "up", "down": "down", "back": "esc"}
        if state.press(aliases.get(key, key)) == "quit":
            return 0


def run_eval(report: evaluation.Report) -> int:
    """Run curses on a TTY, otherwise use the line-oriented terminal UI."""
    try:
        import curses
        use_curses = sys.stdin.isatty() and sys.stdout.isatty()
    except ImportError:
        use_curses = False
    if use_curses:
        try:
            return curses.wrapper(lambda stdscr: _run_curses(stdscr, report))
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print("curses unavailable (%s); using fallback" % exc,
                  file=sys.stderr)
    return _run_fallback(report)


__all__ = ["EvalState", "RenderLine", "render", "run_eval"]
