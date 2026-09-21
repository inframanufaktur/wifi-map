"""Shared rendering primitives and selector screens for evaluation UI."""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple

from wifimap import evaluation
from wifimap.eval_state import EvalState, KIND_OPTIONS




@dataclass(frozen=True)
class RenderLine:
    text: str
    style: str = "normal"

def _clip(text: str, width: int) -> str:
    safe = "".join(
        " " if unicodedata.category(char) in ("Cc", "Cf") else char
        for char in text
    )
    return safe[:max(0, width)]


def _line(text: str, width: int, style: str = "normal") -> RenderLine:
    return RenderLine(_clip(text, width), style)


def _key_help_lines(actions: Tuple[str, ...], width: int) -> List[RenderLine]:
    """Wrap key help at action boundaries so labels are never half-visible."""
    lines = []
    current = "keys:"
    for action in actions:
        separator = " " if current == "keys:" else " | "
        candidate = current + separator + action
        if len(candidate) > width and current != "keys:":
            lines.append(_line(current, width, "muted"))
            current = "      " + action
        else:
            current = candidate
    lines.append(_line(current, width, "muted"))
    return lines


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


def _walk_selector_row(index: int, walk: evaluation.WalkOption,
                       selected: bool, show_location: bool) -> str:
    started = walk.started_at or "unknown time"
    name = ("%s [%s]" % (walk.name, walk.location_name)
            if show_location else walk.name)
    return "  %s %-22s %s | %d spots | %d readings" % (
        ">" if selected else " ", name, started,
        walk.spot_count, walk.reading_count,
    )
