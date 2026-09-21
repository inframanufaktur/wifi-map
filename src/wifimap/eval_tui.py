"""Interactive terminal evaluation of saved WiFi readings."""
from __future__ import annotations

import os
import sys
from typing import List, Optional, Tuple

from wifimap import evaluation
from wifimap.eval_state import EvalState, KIND_OPTIONS
from wifimap.eval_render_common import (
    RenderLine,
    _clip,
    _fmt,
    _focus_value,
    _key_help_lines,
    _line,
    _place,
    _rating,
    _render_kind,
    _render_scope,
    _walk_selector_row,
)
from wifimap.eval_render_dashboard import (
    _comparison_place,
    _comparison_result,
    _comparison_style,
    _fmt_change,
    _history_row,
    _reading_detail_lines,
    _render_comparison_dashboard,
    _render_dashboard,
    _render_detail,
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
