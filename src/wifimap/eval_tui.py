"""Interactive terminal evaluation of saved WiFi readings."""
from __future__ import annotations

import os
import sys
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


def _render_walk_selector(state: EvalState, width: int,
                          height: int) -> List[RenderLine]:
    scope = state.scope.label if state.scope is not None else "-"
    kind = KIND_OPTIONS[state.kind_index][1]
    choosing_after = state.screen == "compare_after"
    show_location = state.scope is not None and state.scope.kind == "ssid"
    lines = [
        _line("Compare walks — %s: %s" % (kind, scope), width, "title"),
        _line("source: %s" % state.report.source, width, "muted"),
        _line("", width),
    ]
    if choosing_after:
        before = state.before_walk.name if state.before_walk else "-"
        lines.append(_line("Before: %s" % before, width, "muted"))
        if state.before_walk is not None and show_location:
            lines.append(_line(
                "location: %s" % state.before_walk.location_name,
                width, "muted"))
        lines.append(_line("Choose after walk", width, "title"))
        walks = state.walk_candidates
        cursor = state.candidate_index
    else:
        lines.append(_line("Choose before walk", width, "title"))
        walks = state.walks
        cursor = state.walk_index

    if choosing_after and not walks:
        location = (state.before_walk.location_name
                    if state.before_walk is not None else "this location")
        lines.extend([
            _line("No other named walk for location %s." % location,
                  width, "weak"),
            _line("Capture another named walk there, then compare again.",
                  width, "muted"),
        ])
    elif len(state.walks) < 2:
        lines.extend([
            _line("Need at least two named walks in this selection.",
                  width, "weak"),
            _line("Run two named walks, then return here to compare them.",
                  width, "muted"),
        ])
    else:
        capacity = max(1, height - len(lines) - 2)
        cursor = min(cursor, max(0, len(walks) - 1))
        start = max(0, cursor - capacity + 1)
        end = min(len(walks), start + capacity)
        for index in range(start, end):
            walk = walks[index]
            lines.append(_line(
                _walk_selector_row(
                    index, walk, index == cursor, show_location),
                width, "selected" if index == cursor else "normal"))
    lines.extend([
        _line("", width),
        _line("keys: ↑/↓ or j/k move | Enter select | Esc back | q quit",
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


_WIDE_CONTEXT_COLUMNS: Tuple[Tuple[str, int], ...] = (
    ("rssi", 6),
    ("snr", 6),
    ("ping_ms", 6),
    ("down_mbps", 7),
    ("up_mbps", 7),
    ("delta_rssi", 7),
    ("delta_down_mbps", 7),
)
_METRICS_BY_KEY = {metric.key: metric for metric in evaluation.METRICS}


def _wide_place_width(width: int) -> int:
    return 29 + min(6, max(0, width - 100))


def _wide_context_columns(
        metric: evaluation.MetricSpec,
        width: int) -> Tuple[Tuple[evaluation.MetricSpec, int], ...]:
    budget = max(0, width - _wide_place_width(width) - 33)
    columns = []
    used = 0
    for key, column_width in _WIDE_CONTEXT_COLUMNS:
        if key == metric.key:
            continue
        required = column_width + (1 if columns else 0)
        if used + required > budget:
            break
        columns.append((_METRICS_BY_KEY[key], column_width))
        used += required
    return tuple(columns)


def _wide_header(metric: evaluation.MetricSpec, width: int) -> str:
    place_width = _wide_place_width(width)
    context = " ".join(
        "%*s" % (column_width, spec.label)
        for spec, column_width in _wide_context_columns(metric, width)
    )
    return " # %-*s %3s │ %-20s │ %s" % (
        place_width, "room / floor / spot", "n",
        "FOCUS: %s" % metric.label, context)


def _wide_row(index: int, row: evaluation.AnalysisRow,
              metric: evaluation.MetricSpec, width: int) -> str:
    place_width = _wide_place_width(width)
    place = _place(row, place_width)
    context = " ".join(
        "%*s" % (
            column_width,
            _fmt(getattr(row, spec.key), spec.decimals),
        )
        for spec, column_width in _wide_context_columns(metric, width)
    )
    return "%2d %-*s %3d │ %-20s │ %s" % (
        index + 1, place_width, place, row.count,
        _focus_value(row, metric), context)


def _narrow_header(metric: evaluation.MetricSpec) -> str:
    return " # room / floor / spot          n  %s" % metric.label


def _narrow_row(index: int, row: evaluation.AnalysisRow,
                metric: evaluation.MetricSpec) -> str:
    place = _place(row, 27)
    return "%2d %-27s %2d  %s" % (
        index + 1, place, row.count, _focus_value(row, metric))


def _selected_line(row: evaluation.AnalysisRow, width: int) -> RenderLine:
    area = "outdoor" if row.outdoors else "indoor"
    count_label = "reading" if row.count == 1 else "readings"
    context = "%s / %s" % (row.location_name, _place(row))
    return _line("selected: %s | %d %s | %s | latest %s" % (
        context, row.count, count_label, area, row.latest_ts),
        width, "muted")


def _reading_detail_lines(reading: evaluation.Reading,
                          width: int) -> List[RenderLine]:
    return [
        _line("reading: %s | id %s" % (
            reading.ts, reading.id if reading.id is not None else "-"),
            width, "muted"),
        _line("network: SSID %s | BSSID %s" % (
            reading.ssid or "Unknown", reading.bssid or "-"),
            width, "muted"),
        _line("radio: channel %s | PHY %s | tx %s Mbps" % (
            reading.channel or "-", reading.phy or "-",
            _fmt(reading.tx_rate, 0)),
            width, "muted"),
        _line("signal: RSSI %s dBm | noise %s dBm | SNR %s dB | "
              "ΔRSSI %s dB | ΔSNR %s dB" % (
                  _fmt(reading.rssi, 0), _fmt(reading.noise, 0),
                  _fmt(reading.snr, 0), _fmt(reading.delta_rssi, 0),
                  _fmt(reading.delta_snr, 0)),
              width, "muted"),
        _line("speed: ping %s ms | down %s Mbps (Δ %s) | "
              "up %s Mbps (Δ %s) | server %s" % (
                  _fmt(reading.ping_ms, 1), _fmt(reading.down_mbps, 1),
                  _fmt(reading.delta_down_mbps, 1),
                  _fmt(reading.up_mbps, 1),
                  _fmt(reading.delta_up_mbps, 1), reading.server or "-"),
              width, "muted"),
        _line("note: %s" % (reading.note or "-"), width, "muted"),
    ]


def _comparison_place(place: evaluation.PlaceKey,
                      max_length: Optional[int] = None) -> str:
    floor = "-" if place.floor is None else str(place.floor)
    if max_length is None:
        return "%s / %s / %s" % (
            place.room_name, floor, place.spot_name)
    label_budget = max(2, max_length - len(floor) - len(" /  / "))
    room_width = max(1, label_budget // 2)
    spot_width = max(1, label_budget - room_width)
    return "%s / %s / %s" % (
        place.room_name[:room_width], floor,
        place.spot_name[:spot_width])


def _comparison_result(row: evaluation.ComparisonRow, metric: str) -> str:
    if row.status == "before_only":
        return "NOT REVISITED"
    if row.status == "after_only":
        return "NEW"
    direction = evaluation.comparison_direction(row, metric)
    return {
        "improved": "BETTER",
        "unchanged": "UNCHANGED",
        "worse": "WORSE",
        "missing": "NO DATA",
    }[direction]


def _fmt_change(value: Optional[float], decimals: int) -> str:
    if value is None:
        return "-"
    if decimals == 0:
        return "%+d" % round(value)
    return ("%+.*f" % (decimals, value))


def _comparison_row_lines(index: int, row: evaluation.ComparisonRow,
                          metric: evaluation.MetricSpec, width: int,
                          selected: bool) -> List[str]:
    before = getattr(row.before, metric.key) if row.before else None
    after = getattr(row.after, metric.key) if row.after else None
    delta = evaluation.comparison_delta(row, metric.key)
    result = _comparison_result(row, metric.key)
    marker = ">" if selected else " "
    if width >= 76:
        place_width = width - 44
        return ["%s%3d %-*s %7s %7s %7s  %s" % (
            marker, index + 1, place_width,
            _comparison_place(row.place, place_width),
            _fmt(before, metric.decimals), _fmt(after, metric.decimals),
            _fmt_change(delta, metric.decimals), result,
        )]
    place = "%s%2d  %s" % (
        marker, index + 1,
        _comparison_place(row.place, max(2, width - 5)))
    if width >= 70:
        values = "    before %s  →  after %s  │  change %s  │  %s" % (
            _fmt(before, metric.decimals), _fmt(after, metric.decimals),
            _fmt_change(delta, metric.decimals), result,
        )
    else:
        values = "    %s → %s  │  Δ %s  │  %s" % (
            _fmt(before, metric.decimals), _fmt(after, metric.decimals),
            _fmt_change(delta, metric.decimals), result,
        )
    return [place, values]


def _comparison_style(row: evaluation.ComparisonRow, metric: str) -> str:
    result = _comparison_result(row, metric)
    if result in ("NOT REVISITED", "WORSE"):
        return "weak"
    if result in ("NEW", "BETTER"):
        return "great"
    if result == "UNCHANGED":
        return "ok"
    return "normal"


def _render_comparison_dashboard(state: EvalState, width: int,
                                 height: int) -> List[RenderLine]:
    rows = state.comparison_rows
    metric = state.comparison_metric
    summary = evaluation.comparison_summary(rows, metric.key)
    scope = state.scope.label if state.scope is not None else "-"
    kind = KIND_OPTIONS[state.kind_index][1]
    before = state.before_walk.name if state.before_walk else "-"
    after = state.after_walk.name if state.after_walk else "-"
    order = "best change first" if state.comparison_reverse else (
        "worst change first")
    lines = [
        _line("WiFi comparison — %s: %s" % (kind, scope), width, "title"),
        _line("%sbefore: %s → after: %s" % (
            (("location: %s | " % state.before_walk.location_name)
             if kind == "SSID" and state.before_walk is not None else ""),
            before, after), width),
        _line("coverage: %d matched | %d not revisited | %d new" % (
            summary.matched_spots, summary.before_only, summary.after_only),
            width),
        _line("focus: %s (%s is better) | order: %s" % (
            metric.label, "higher" if metric.higher_is_better else "lower",
            order), width),
    ]
    lines.append(_line(
        "paired %d | median %s → %s %s | change %s" % (
            summary.comparable,
            _fmt(summary.before_median, metric.decimals),
            _fmt(summary.after_median, metric.decimals), metric.unit,
            _fmt_change(summary.median_delta, metric.decimals)), width))
    lines.append(_line(
        "results: better %d | same %d | worse %d | missing %d" % (
            summary.improved, summary.unchanged, summary.worse,
            summary.metric_missing), width))
    lines.append(_line("", width))
    tabular = width >= 76
    header = (("  # %-*s %7s %7s %7s  %s" % (
        width - 44, "room / floor / spot",
        "before", "after", "change", "result"))
        if tabular else "  #  room / floor / spot")
    lines.append(_line(header, width, "title"))
    help_lines = _key_help_lines((
        "↑/↓ j/k move", "Enter details", "Tab/Shift+Tab metric",
        "r order", "x swap", "c reselect", "Esc evaluation", "q quit",
    ), width)
    row_height = 1 if tabular else 2
    reserved = len(lines) + len(help_lines)
    capacity = max(1, (height - reserved) // row_height)
    cursor = min(state.comparison_cursor, max(0, len(rows) - 1))
    start = max(0, cursor - capacity + 1)
    end = min(len(rows), start + capacity)
    for index in range(start, end):
        row = rows[index]
        style = _comparison_style(row, metric.key)
        for text in _comparison_row_lines(
                index, row, metric, width, index == cursor):
            lines.append(_line(text, width, style))
    if not rows:
        lines.append(_line("No comparable readings.", width, "weak"))
    lines.extend(help_lines)
    return lines


def _render_dashboard(state: EvalState, width: int,
                      height: int) -> List[RenderLine]:
    rows = state.rows
    scope = state.scope.label if state.scope is not None else "-"
    kind = KIND_OPTIONS[state.kind_index][1]
    mode = "spot summary" if state.grouped else "individual readings"
    order = "best first" if state.reverse else "worst first"
    lines = [
        _line("WiFi evaluation — %s: %s" % (kind, scope), width, "title"),
        _line("source: %s" % state.report.source, width, "muted"),
        _line("mode: %s | focus: %s | order: %s | %d rows" % (
            mode, state.metric.label, order, len(rows)), width),
        _line(_summary_line(state, rows), width),
        _line("", width),
    ]
    wide = width >= 100
    lines.append(_line(
        _wide_header(state.metric, width) if wide
        else _narrow_header(state.metric),
        width, "title"))
    help_lines = _key_help_lines((
        "↑/↓ j/k move", "Enter details", "c compare",
        "Tab/Shift+Tab metric", "m mode", "r order", "Esc back", "q quit",
    ), width)

    cursor = min(state.cursor, max(0, len(rows) - 1))
    selected = _selected_line(rows[cursor], width) if rows else None
    reserved = len(lines) + (1 if selected else 0) + len(help_lines)
    capacity = max(1, height - reserved)
    start = max(0, cursor - capacity + 1)
    end = min(len(rows), start + capacity)
    for index in range(start, end):
        row = rows[index]
        text = (_wide_row(index, row, state.metric, width) if wide
                else _narrow_row(index, row, state.metric))
        style = "selected" if index == cursor else _rating(
            state.metric.key, getattr(row, state.metric.key)).lower()
        lines.append(_line(text, width, style))

    if rows:
        lines.append(selected)
    else:
        lines.append(_line("No readings in this selection.", width, "weak"))
    lines.extend(help_lines)
    return lines


def _history_row(reading: evaluation.Reading) -> str:
    return ("%s | channel %s | BSSID %s | RSSI %s | SNR %s | down %s" % (
        reading.ts, reading.channel or "-", reading.bssid or "-",
        _fmt(reading.rssi, 0), _fmt(reading.snr, 0),
        _fmt(reading.down_mbps, 1),
    ))


def _render_detail(state: EvalState, width: int,
                   height: int) -> List[RenderLine]:
    readings = state.detail_readings
    if not readings:
        return [
            _line("Reading history", width, "title"),
            _line("No readings available.", width, "weak"),
            _line("keys: Esc back | q quit", width, "muted"),
        ]

    cursor = min(state.detail_index, len(readings) - 1)
    reading = readings[cursor]
    floor = "-" if reading.floor is None else str(reading.floor)
    place = "%s / %s / %s / %s" % (
        reading.location_name, reading.room_name, floor, reading.spot_name)
    count_label = "reading" if len(readings) == 1 else "readings"
    area = "outdoor" if reading.outdoors else "indoor"
    lines = [
        _line("Reading history — %s" % place, width, "title"),
        _line("source: %s" % state.report.source, width, "muted"),
        _line("%d %s | %s | newest first" % (
            len(readings), count_label, area), width),
        _line("", width),
        _line("History", width, "title"),
    ]
    details = _reading_detail_lines(reading, width)
    reserved = len(lines) + len(details) + 2  # gap + keys
    capacity = max(1, height - reserved)
    start = max(0, cursor - capacity + 1)
    end = min(len(readings), start + capacity)
    for index in range(start, end):
        prefix = "> " if index == cursor else "  "
        lines.append(_line(
            prefix + _history_row(readings[index]), width,
            "selected" if index == cursor else "normal"))
    lines.append(_line("", width))
    lines.extend(details)
    lines.append(_line(
        "keys: ↑/↓ or j/k reading | Home/End | Esc back | q quit",
        width, "muted"))
    return lines


def _comparison_history_lines(
        title: str, readings: Tuple[evaluation.Reading, ...], index: int,
        active: bool, width: int, capacity: int,
        empty_message: str) -> List[RenderLine]:
    lines = [_line(("> " if active else "  ") + title,
                   width, "title" if active else "normal")]
    if not readings:
        lines.append(_line("  " + empty_message, width, "weak"))
        return lines
    cursor = min(index, len(readings) - 1)
    start = max(0, cursor - capacity + 1)
    end = min(len(readings), start + capacity)
    for reading_index in range(start, end):
        selected = reading_index == cursor
        prefix = "> " if active and selected else ("* " if selected else "  ")
        lines.append(_line(
            prefix + _history_row(readings[reading_index]), width,
            "selected" if active and selected else "normal"))
    return lines


def _comparison_metric_lines(row: evaluation.ComparisonRow,
                             width: int) -> List[RenderLine]:
    lines = []
    for metric in evaluation.COMPARISON_METRICS:
        before = getattr(row.before, metric.key) if row.before else None
        after = getattr(row.after, metric.key) if row.after else None
        delta = evaluation.comparison_delta(row, metric.key)
        result = _comparison_result(row, metric.key)
        lines.append(_line(
            "%-8s %9s → %-9s | change %8s %-5s | %s" % (
                metric.label,
                "%s %s" % (_fmt(before, metric.decimals), metric.unit),
                "%s %s" % (_fmt(after, metric.decimals), metric.unit),
                _fmt_change(delta, metric.decimals), metric.unit, result,
            ), width))
    return lines


def _selected_comparison_reading(state: EvalState):
    if state.comparison_detail_side == "before":
        readings = state.comparison_before_readings
        index = state.comparison_before_index
        side_label = "before"
    else:
        readings = state.comparison_after_readings
        index = state.comparison_after_index
        side_label = "after"
    reading = readings[min(index, len(readings) - 1)] if readings else None
    return side_label, reading


def _render_compact_comparison_detail(
        state: EvalState, row: evaluation.ComparisonRow,
        before_name: str, after_name: str,
        header: List[RenderLine], width: int,
        height: int) -> List[RenderLine]:
    page = state.comparison_detail_page
    lines = list(header)
    lines.append(_line("section %d/3" % (page + 1), width, "muted"))
    lines.append(_line("", width))
    if page == 0:
        lines.append(_line("All metrics", width, "title"))
        lines.extend(_comparison_metric_lines(row, width))
        controls = "Tab/Shift+Tab section | Esc back | q quit"
    elif page == 1:
        available = max(2, height - len(lines) - 1)
        history_capacity = max(1, (available - 2) // 2)
        lines.extend(_comparison_history_lines(
            "Before history — %s" % before_name,
            state.comparison_before_readings,
            state.comparison_before_index,
            state.comparison_detail_side == "before",
            width, history_capacity, "No before readings (NEW)",
        ))
        lines.extend(_comparison_history_lines(
            "After history — %s" % after_name,
            state.comparison_after_readings,
            state.comparison_after_index,
            state.comparison_detail_side == "after",
            width, history_capacity,
            "No after readings (NOT REVISITED)",
        ))
        controls = ("←/→ walk | ↑/↓ j/k reading | Home/End | "
                    "Tab/Shift+Tab section")
    else:
        side_label, reading = _selected_comparison_reading(state)
        if reading is None:
            lines.append(_line("No selected reading.", width, "weak"))
        else:
            lines.append(_line("Selected %s reading" % side_label,
                               width, "title"))
            lines.extend(_reading_detail_lines(reading, width))
        controls = ("←/→ walk | ↑/↓ j/k reading | "
                    "Tab/Shift+Tab section | Esc back")
    lines.append(_line("keys: %s" % controls, width, "muted"))
    return lines


def _render_comparison_detail(state: EvalState, width: int,
                              height: int) -> List[RenderLine]:
    row = state.comparison_detail_row
    if row is None:
        return [
            _line("Walk comparison detail", width, "title"),
            _line("No comparison row selected.", width, "weak"),
            _line("keys: Esc back | q quit", width, "muted"),
        ]
    before_name = state.before_walk.name if state.before_walk else "before"
    after_name = state.after_walk.name if state.after_walk else "after"
    status = _comparison_result(row, state.comparison_metric.key)
    place = "%s / %s" % (
        row.place.location_name, _comparison_place(row.place))
    header = [
        _line("Walk comparison detail — %s" % place, width, "title"),
        _line("before: %s → after: %s" % (
            before_name, after_name), width, "muted"),
        _line("status: %s" % status, width,
              _comparison_style(row, state.comparison_metric.key)),
    ]
    if height < 30 or state.comparison_detail_page != 0:
        return _render_compact_comparison_detail(
            state, row, before_name, after_name,
            header, width, height)

    lines = header + [
        _line("", width),
        _line("All metrics", width, "title"),
    ]
    lines.extend(_comparison_metric_lines(row, width))
    lines.append(_line("", width))

    history_capacity = max(1, (height - 25) // 2)
    lines.extend(_comparison_history_lines(
        "Before history — %s" % before_name,
        state.comparison_before_readings, state.comparison_before_index,
        state.comparison_detail_side == "before", width, history_capacity,
        "No before readings (NEW)",
    ))
    lines.extend(_comparison_history_lines(
        "After history — %s" % after_name,
        state.comparison_after_readings, state.comparison_after_index,
        state.comparison_detail_side == "after", width, history_capacity,
        "No after readings (NOT REVISITED)",
    ))
    lines.append(_line("", width))

    side_label, reading = _selected_comparison_reading(state)
    if reading is not None:
        lines.append(_line("Selected %s reading" % side_label,
                           width, "title"))
        lines.extend(_reading_detail_lines(reading, width))
    lines.append(_line(
        "keys: ←/→ walk | ↑/↓ or j/k reading | Home/End | Esc back | q quit",
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
    elif state.screen == "detail":
        lines = _render_detail(state, width, height)
    elif state.screen in ("compare_before", "compare_after"):
        lines = _render_walk_selector(state, width, height)
    elif state.screen == "compare_dashboard":
        lines = _render_comparison_dashboard(state, width, height)
    elif state.screen == "compare_detail":
        lines = _render_comparison_detail(state, width, height)
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
    key_left = getattr(curses_mod, "KEY_LEFT", None)
    if key_left is not None:
        mapping[key_left] = "left"
    key_right = getattr(curses_mod, "KEY_RIGHT", None)
    if key_right is not None:
        mapping[key_right] = "right"
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
