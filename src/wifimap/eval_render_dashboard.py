"""Dashboard and reading-detail renderers for evaluation UI."""
from __future__ import annotations

from typing import List, Optional, Tuple

from wifimap import evaluation
from wifimap.eval_state import EvalState, KIND_OPTIONS
from wifimap.eval_render_common import (
    RenderLine,
    _fmt,
    _focus_value,
    _key_help_lines,
    _line,
    _place,
    _rating,
)


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
    summary = evaluation.comparison_summary(
        state.all_comparison_rows, metric.key)
    scope = state.scope.label if state.scope is not None else "-"
    kind = KIND_OPTIONS[state.kind_index][1]
    before = state.before_walk.name if state.before_walk else "-"
    after = state.after_walk.name if state.after_walk else "-"
    order = "best change first" if state.comparison_reverse else (
        "worst change first")
    spot_filter = "all spots" if state.comparison_show_all else (
        "compared spots only")
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
        _line("filter: %s" % spot_filter, width, "muted"),
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
        "r order", "f all spots" if not state.comparison_show_all
        else "f compared only", "x swap", "c reselect", "Esc evaluation",
        "q quit",
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
