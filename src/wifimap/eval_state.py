"""Navigation and analysis state for the evaluation terminal UI."""
from __future__ import annotations

from typing import List, Optional, Tuple

from wifimap import evaluation


KIND_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("location", "Location"),
    ("ssid", "SSID"),
)


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
        self.detail_readings: Tuple[evaluation.Reading, ...] = ()
        self.detail_index = 0
        self._rows: List[evaluation.AnalysisRow] = []
        self.walks: List[evaluation.WalkOption] = []
        self.walk_index = 0
        self.walk_candidates: List[evaluation.WalkOption] = []
        self.candidate_index = 0
        self.before_walk: Optional[evaluation.WalkOption] = None
        self.after_walk: Optional[evaluation.WalkOption] = None
        self.comparison_metric_index = 0
        self.comparison_reverse = False
        self.comparison_show_all = False
        self.comparison_cursor = 0
        self._all_comparison_rows: List[evaluation.ComparisonRow] = []
        self._comparison_rows: List[evaluation.ComparisonRow] = []
        self.comparison_detail_row: Optional[evaluation.ComparisonRow] = None
        self.comparison_detail_side = "before"
        self.comparison_before_readings: Tuple[evaluation.Reading, ...] = ()
        self.comparison_after_readings: Tuple[evaluation.Reading, ...] = ()
        self.comparison_before_index = 0
        self.comparison_after_index = 0
        self.comparison_detail_page = 0

    @property
    def metric(self) -> evaluation.MetricSpec:
        return evaluation.METRICS[self.metric_index]

    @property
    def rows(self) -> List[evaluation.AnalysisRow]:
        return self._rows

    @property
    def comparison_metric(self) -> evaluation.MetricSpec:
        return evaluation.COMPARISON_METRICS[self.comparison_metric_index]

    @property
    def comparison_rows(self) -> List[evaluation.ComparisonRow]:
        return self._comparison_rows

    @property
    def all_comparison_rows(self) -> List[evaluation.ComparisonRow]:
        return self._all_comparison_rows

    def _scoped_readings(self) -> List[evaluation.Reading]:
        if self.scope is None:
            return []
        return evaluation.filter_scope(self.report.readings, self.scope)

    def _refresh_rows(self) -> None:
        if self.scope is None:
            self._rows = []
            return
        readings = self._scoped_readings()
        rows = evaluation.analysis_rows(readings, grouped=self.grouped)
        self._rows = evaluation.rank_rows(
            rows, self.metric.key, reverse=self.reverse)
        self.cursor = min(self.cursor, max(0, len(self._rows) - 1))

    def _start_comparison(self) -> None:
        self.walks = evaluation.walk_options(self._scoped_readings())
        self.walk_index = max(0, len(self.walks) - 1)
        self.walk_candidates = []
        self.before_walk = None
        self.after_walk = None
        self.screen = "compare_before"

    def _refresh_comparison_rows(self) -> None:
        if self.before_walk is None or self.after_walk is None:
            self._all_comparison_rows = []
            self._comparison_rows = []
            return
        rows = evaluation.compare_walks(
            self._scoped_readings(), self.before_walk.walk_id,
            self.after_walk.walk_id,
        )
        self._all_comparison_rows = evaluation.rank_comparison_rows(
            rows, self.comparison_metric.key,
            reverse=self.comparison_reverse,
        )
        self._comparison_rows = [
            row for row in self._all_comparison_rows
            if self.comparison_show_all or row.status == "matched"
        ]
        self.comparison_cursor = min(
            self.comparison_cursor,
            max(0, len(self._comparison_rows) - 1),
        )

    @staticmethod
    def _newest_first(readings) -> Tuple[evaluation.Reading, ...]:
        return tuple(sorted(
            readings,
            key=lambda reading: (
                reading.ts,
                reading.id if reading.id is not None else -1,
            ),
            reverse=True,
        ))

    def _open_comparison_detail(self) -> None:
        row = self.comparison_rows[self.comparison_cursor]
        self.comparison_detail_row = row
        self.comparison_before_readings = self._newest_first(
            row.before.readings if row.before is not None else ())
        self.comparison_after_readings = self._newest_first(
            row.after.readings if row.after is not None else ())
        self.comparison_before_index = 0
        self.comparison_after_index = 0
        self.comparison_detail_page = 0
        self.comparison_detail_side = (
            "before" if self.comparison_before_readings else "after")
        self.screen = "compare_detail"

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

        if self.screen == "detail":
            if key == "esc":
                self.screen = "dashboard"
            elif key in ("up", "k"):
                self.detail_index = max(0, self.detail_index - 1)
            elif key in ("down", "j"):
                self.detail_index = min(
                    max(0, len(self.detail_readings) - 1),
                    self.detail_index + 1,
                )
            elif key == "home":
                self.detail_index = 0
            elif key == "end":
                self.detail_index = max(0, len(self.detail_readings) - 1)
            return "continue"

        if self.screen == "compare_before":
            if key == "esc":
                self.screen = "dashboard"
            elif key in ("up", "k"):
                self.walk_index = self._move(
                    self.walk_index, -1, len(self.walks))
            elif key in ("down", "j"):
                self.walk_index = self._move(
                    self.walk_index, 1, len(self.walks))
            elif key == "home":
                self.walk_index = 0
            elif key == "end" and self.walks:
                self.walk_index = len(self.walks) - 1
            elif key == "enter" and len(self.walks) >= 2:
                self.before_walk = self.walks[self.walk_index]
                self.walk_candidates = [
                    walk for walk in self.walks
                    if walk.walk_id != self.before_walk.walk_id
                    and self._same_walk_location(walk, self.before_walk)
                ]
                self.candidate_index = 0
                self.screen = "compare_after"
            return "continue"

        if self.screen == "compare_after":
            if key == "esc":
                self.screen = "compare_before"
            elif key in ("up", "k"):
                self.candidate_index = self._move(
                    self.candidate_index, -1, len(self.walk_candidates))
            elif key in ("down", "j"):
                self.candidate_index = self._move(
                    self.candidate_index, 1, len(self.walk_candidates))
            elif key == "home":
                self.candidate_index = 0
            elif key == "end" and self.walk_candidates:
                self.candidate_index = len(self.walk_candidates) - 1
            elif key == "enter" and self.walk_candidates:
                self.after_walk = self.walk_candidates[self.candidate_index]
                self.comparison_metric_index = 0
                self.comparison_reverse = False
                self.comparison_show_all = False
                self.comparison_cursor = 0
                self._refresh_comparison_rows()
                self.screen = "compare_dashboard"
            return "continue"

        if self.screen == "compare_detail":
            if key == "esc":
                self.screen = "compare_dashboard"
            elif key in ("tab", "shift-tab"):
                step = -1 if key == "shift-tab" else 1
                self.comparison_detail_page = (
                    self.comparison_detail_page + step) % 3
            elif key == "left" and self.comparison_before_readings:
                self.comparison_detail_side = "before"
            elif key == "right" and self.comparison_after_readings:
                self.comparison_detail_side = "after"
            else:
                readings = (self.comparison_before_readings
                            if self.comparison_detail_side == "before"
                            else self.comparison_after_readings)
                attr = ("comparison_before_index"
                        if self.comparison_detail_side == "before"
                        else "comparison_after_index")
                index = getattr(self, attr)
                if key in ("up", "k"):
                    setattr(self, attr, max(0, index - 1))
                elif key in ("down", "j"):
                    setattr(self, attr, min(max(0, len(readings) - 1),
                                           index + 1))
                elif key == "home":
                    setattr(self, attr, 0)
                elif key == "end":
                    setattr(self, attr, max(0, len(readings) - 1))
            return "continue"

        if self.screen == "compare_dashboard":
            if key == "esc":
                self.screen = "dashboard"
            elif key == "c":
                self._start_comparison()
            elif key in ("tab", "shift-tab"):
                step = -1 if key == "shift-tab" else 1
                self.comparison_metric_index = (
                    self.comparison_metric_index + step
                ) % len(evaluation.COMPARISON_METRICS)
                self.comparison_cursor = 0
                self._refresh_comparison_rows()
            elif key == "r":
                self.comparison_reverse = not self.comparison_reverse
                self.comparison_cursor = 0
                self._refresh_comparison_rows()
            elif key == "f":
                self.comparison_show_all = not self.comparison_show_all
                self.comparison_cursor = 0
                self._refresh_comparison_rows()
            elif key == "x" and self.before_walk and self.after_walk:
                self.before_walk, self.after_walk = (
                    self.after_walk, self.before_walk)
                self.comparison_cursor = 0
                self._refresh_comparison_rows()
            elif key == "enter" and self.comparison_rows:
                self._open_comparison_detail()
            elif key in ("up", "k"):
                self.comparison_cursor = max(0, self.comparison_cursor - 1)
            elif key in ("down", "j"):
                self.comparison_cursor = min(
                    max(0, len(self.comparison_rows) - 1),
                    self.comparison_cursor + 1,
                )
            elif key == "home":
                self.comparison_cursor = 0
            elif key == "end":
                self.comparison_cursor = max(
                    0, len(self.comparison_rows) - 1)
            return "continue"

        if key == "esc":
            self.screen = "scope"
            return "continue"
        if key in ("tab", "shift-tab"):
            step = -1 if key == "shift-tab" else 1
            self.metric_index = (self.metric_index + step) % len(
                evaluation.METRICS)
            self.cursor = 0
            self._refresh_rows()
        elif key == "r":
            self.reverse = not self.reverse
            self.cursor = 0
            self._refresh_rows()
        elif key == "m":
            self.grouped = not self.grouped
            self.cursor = 0
            self._refresh_rows()
        elif key == "c":
            self._start_comparison()
        elif key == "enter" and self.rows:
            row = self.rows[self.cursor]
            self.detail_readings = self._newest_first(row.readings)
            self.detail_index = 0
            self.screen = "detail"
        elif key in ("up", "k"):
            self.cursor = max(0, self.cursor - 1)
        elif key in ("down", "j"):
            self.cursor = min(max(0, len(self.rows) - 1), self.cursor + 1)
        elif key == "home":
            self.cursor = 0
        elif key == "end":
            self.cursor = max(0, len(self.rows) - 1)
        return "continue"

    @staticmethod
    def _same_walk_location(first: evaluation.WalkOption,
                            second: evaluation.WalkOption) -> bool:
        if first.location_id is not None and second.location_id is not None:
            return first.location_id == second.location_id
        return first.location_name == second.location_name
