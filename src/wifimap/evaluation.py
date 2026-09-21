"""Shared evaluation model for SQLite readings and exported CSV reports."""
from __future__ import annotations

import statistics
from typing import Iterable, List, Optional, Sequence, Tuple

from wifimap.evaluation_models import (
    AnalysisRow,
    COMPARISON_METRICS,
    ComparisonRow,
    ComparisonSummary,
    METRICS,
    MetricSpec,
    MetricSummary,
    OPTIONAL_WALK_FIELDS,
    PlaceKey,
    REQUIRED_FIELDS,
    Reading,
    Report,
    ReportError,
    ScopeOption,
    WalkOption,
    _COMPARISON_METRIC_BY_KEY,
    _METRIC_BY_KEY,
)
from wifimap.evaluation_io import load_csv, load_db




def scope_options(readings: Sequence[Reading], kind: str) -> List[ScopeOption]:
    if kind not in ("location", "ssid"):
        raise ValueError("unknown scope kind: %r" % (kind,))
    counts = {}
    for reading in readings:
        value = reading.location_name if kind == "location" else reading.ssid
        counts[value] = counts.get(value, 0) + 1
    return [
        ScopeOption(kind=kind, value=value,
                    label=("Unknown" if value is None else value),
                    count=count)
        for value, count in counts.items()
    ]


def filter_scope(readings: Iterable[Reading],
                 option: ScopeOption) -> List[Reading]:
    if option.kind == "location":
        return [r for r in readings if r.location_name == option.value]
    if option.kind == "ssid":
        return [r for r in readings if r.ssid == option.value]
    raise ValueError("unknown scope kind: %r" % (option.kind,))


_ANALYSIS_METRIC_KEYS = tuple(metric.key for metric in METRICS)


def _median_reading_value(readings: Sequence[Reading],
                          key: str) -> Optional[float]:
    values = [getattr(reading, key) for reading in readings
              if getattr(reading, key) is not None]
    return float(statistics.median(values)) if values else None


def _common_reading_value(readings: Sequence[Reading],
                          key: str) -> Optional[str]:
    values = {getattr(reading, key) for reading in readings}
    if len(values) == 1:
        return next(iter(values))
    return "Mixed"


def _analysis_row(readings: Sequence[Reading]) -> AnalysisRow:
    first = readings[0]
    return AnalysisRow(
        location_name=first.location_name,
        room_name=first.room_name,
        spot_name=first.spot_name,
        floor=first.floor,
        outdoors=first.outdoors,
        count=len(readings),
        latest_ts=max(reading.ts for reading in readings),
        ssid=_common_reading_value(readings, "ssid"),
        bssid=_common_reading_value(readings, "bssid"),
        channel=_common_reading_value(readings, "channel"),
        phy=_common_reading_value(readings, "phy"),
        server=_common_reading_value(readings, "server"),
        note=_common_reading_value(readings, "note"),
        **{
            key: _median_reading_value(readings, key)
            for key in _ANALYSIS_METRIC_KEYS
        },
        readings=tuple(readings),
    )


def analysis_rows(readings: Sequence[Reading],
                  grouped: bool = True) -> List[AnalysisRow]:
    """Return per-spot median rows, or one analysis row per reading."""
    if not grouped:
        return [_analysis_row([reading]) for reading in readings]
    groups = {}
    for reading in readings:
        key = (
            reading.location_name, reading.room_name, reading.spot_name,
            reading.floor, reading.outdoors,
        )
        groups.setdefault(key, []).append(reading)
    return [_analysis_row(group) for group in groups.values()]


def _metric(metric: str) -> MetricSpec:
    try:
        return _METRIC_BY_KEY[metric]
    except KeyError as exc:
        raise ValueError("unknown metric: %r" % (metric,)) from exc


def rank_rows(rows: Sequence[AnalysisRow], metric: str,
              reverse: bool = False) -> List[AnalysisRow]:
    """Rank rows worst-first by default, always placing missing values last."""
    spec = _metric(metric)
    present = [row for row in rows if getattr(row, metric) is not None]
    missing = [row for row in rows if getattr(row, metric) is None]
    worst_descending = not spec.higher_is_better
    descending = worst_descending if not reverse else not worst_descending
    present.sort(
        key=lambda row: (
            getattr(row, metric), row.location_name, row.room_name,
            row.spot_name, row.latest_ts,
        ),
        reverse=descending,
    )
    missing.sort(key=lambda row: (
        row.location_name, row.room_name, row.spot_name, row.latest_ts))
    return present + missing


def metric_summary(rows: Sequence[AnalysisRow], metric: str) -> MetricSummary:
    _metric(metric)
    values = [getattr(row, metric) for row in rows
              if getattr(row, metric) is not None]
    if not values:
        return MetricSummary(available=0, missing=len(rows), minimum=None,
                             median=None, maximum=None)
    return MetricSummary(
        available=len(values),
        missing=len(rows) - len(values),
        minimum=float(min(values)),
        median=float(statistics.median(values)),
        maximum=float(max(values)),
    )


def _spot_identity(reading: Reading) -> tuple:
    if reading.spot_id is not None:
        return ("spot_id", reading.spot_id)
    return (
        "place", reading.location_name, reading.room_name,
        reading.floor, reading.spot_name,
    )


def _place_key(reading: Reading) -> PlaceKey:
    return PlaceKey(
        spot_id=reading.spot_id,
        location_name=reading.location_name,
        room_name=reading.room_name,
        floor=reading.floor,
        spot_name=reading.spot_name,
    )


def _walk_location(readings: Sequence[Reading],
                   walk_id: int) -> Tuple[Optional[int], str]:
    location_ids = {
        reading.location_id for reading in readings
        if reading.location_id is not None
    }
    location_names = {reading.location_name for reading in readings}
    if len(location_ids) > 1 or len(location_names) > 1:
        raise ValueError("walk %r spans multiple locations" % walk_id)
    location_id = next(iter(location_ids)) if location_ids else None
    location_name = next(iter(location_names)) if location_names else ""
    return location_id, location_name


def _same_location(before: Tuple[Optional[int], str],
                   after: Tuple[Optional[int], str]) -> bool:
    before_id, before_name = before
    after_id, after_name = after
    if before_id is not None and after_id is not None:
        return before_id == after_id
    return before_name == after_name


def walk_options(readings: Sequence[Reading]) -> List[WalkOption]:
    """Return named walks represented by the already scope-filtered readings."""
    groups = {}
    for reading in readings:
        if reading.walk_id is None or reading.walk_name is None:
            continue
        groups.setdefault(reading.walk_id, []).append(reading)

    options = []
    for walk_id, group in groups.items():
        location_id, location_name = _walk_location(group, walk_id)
        starts = [reading.walk_started_at for reading in group
                  if reading.walk_started_at is not None]
        ends = [reading.walk_ended_at for reading in group
                if reading.walk_ended_at is not None]
        options.append(WalkOption(
            walk_id=walk_id,
            name=group[0].walk_name or "Walk #%d" % walk_id,
            location_id=location_id,
            location_name=location_name,
            started_at=min(starts) if starts else None,
            ended_at=max(ends) if ends else None,
            reading_count=len(group),
            spot_count=len({_spot_identity(reading) for reading in group}),
        ))
    options.sort(
        key=lambda option: (
            option.started_at or "", option.walk_id, option.name,
        ),
        reverse=True,
    )
    return options


def _walk_rows(readings: Sequence[Reading], walk_id: int):
    groups = {}
    for reading in readings:
        if reading.walk_id == walk_id:
            groups.setdefault(_spot_identity(reading), []).append(reading)
    return {
        identity: (_place_key(group[0]), _analysis_row(group))
        for identity, group in groups.items()
    }


def compare_walks(readings: Sequence[Reading], before_walk_id: int,
                  after_walk_id: int) -> List[ComparisonRow]:
    """Compare two walks using per-spot medians and their spot union."""
    if before_walk_id == after_walk_id:
        raise ValueError("before and after walks must be different")
    before_readings = [
        reading for reading in readings
        if reading.walk_id == before_walk_id
    ]
    after_readings = [
        reading for reading in readings
        if reading.walk_id == after_walk_id
    ]
    if not before_readings:
        raise ValueError("unknown or empty before walk: %r" % before_walk_id)
    if not after_readings:
        raise ValueError("unknown or empty after walk: %r" % after_walk_id)
    before_location = _walk_location(before_readings, before_walk_id)
    after_location = _walk_location(after_readings, after_walk_id)
    if not _same_location(before_location, after_location):
        raise ValueError("cannot compare walks from different locations")

    before = _walk_rows(readings, before_walk_id)
    after = _walk_rows(readings, after_walk_id)

    rows = []
    for identity in before.keys() | after.keys():
        before_item = before.get(identity)
        after_item = after.get(identity)
        before_row = before_item[1] if before_item is not None else None
        after_row = after_item[1] if after_item is not None else None
        if before_row is not None and after_row is not None:
            status = "matched"
        elif before_row is not None:
            status = "before_only"
        else:
            status = "after_only"
        place = (before_item or after_item)[0]
        rows.append(ComparisonRow(
            place=place, status=status,
            before=before_row, after=after_row,
        ))
    rows.sort(key=_comparison_place_sort_key)
    return rows


def _comparison_metric(metric: str) -> MetricSpec:
    try:
        return _COMPARISON_METRIC_BY_KEY[metric]
    except KeyError as exc:
        raise ValueError("unknown comparison metric: %r" % metric) from exc


def comparison_delta(row: ComparisonRow, metric: str) -> Optional[float]:
    """Return the raw after-minus-before change for a comparison row."""
    _comparison_metric(metric)
    if row.before is None or row.after is None:
        return None
    before = getattr(row.before, metric)
    after = getattr(row.after, metric)
    if before is None or after is None:
        return None
    return float(after - before)


def comparison_direction(row: ComparisonRow, metric: str) -> str:
    """Return ``improved``, ``unchanged``, ``worse``, or ``missing``."""
    spec = _comparison_metric(metric)
    delta = comparison_delta(row, metric)
    if delta is None:
        return "missing"
    improvement = delta if spec.higher_is_better else -delta
    if improvement > 0:
        return "improved"
    if improvement < 0:
        return "worse"
    return "unchanged"


def _comparison_place_sort_key(row: ComparisonRow) -> tuple:
    floor = row.place.floor
    return (
        row.place.location_name, floor is None, floor or 0,
        row.place.room_name, row.place.spot_name,
        row.place.spot_id if row.place.spot_id is not None else -1,
    )


def _comparison_improvement(row: ComparisonRow, metric: str) -> float:
    spec = _comparison_metric(metric)
    delta = comparison_delta(row, metric)
    if delta is None:
        raise ValueError("comparison row has no paired metric")
    return delta if spec.higher_is_better else -delta


def rank_comparison_rows(rows: Sequence[ComparisonRow], metric: str,
                         reverse: bool = False) -> List[ComparisonRow]:
    """Rank paired changes, keeping coverage exceptions visible."""
    _comparison_metric(metric)
    before_only = [row for row in rows if row.status == "before_only"]
    after_only = [row for row in rows if row.status == "after_only"]
    comparable = [
        row for row in rows
        if row.status == "matched" and comparison_delta(row, metric) is not None
    ]
    metric_missing = [
        row for row in rows
        if row.status == "matched" and comparison_delta(row, metric) is None
    ]
    for group in (before_only, after_only, comparable, metric_missing):
        group.sort(key=_comparison_place_sort_key)
    comparable.sort(
        key=lambda row: _comparison_improvement(row, metric),
        reverse=reverse,
    )
    return before_only + comparable + after_only + metric_missing


def comparison_summary(rows: Sequence[ComparisonRow],
                       metric: str) -> ComparisonSummary:
    """Summarize coverage and paired changes for one raw metric."""
    _comparison_metric(metric)
    paired = [
        row for row in rows
        if row.status == "matched" and comparison_delta(row, metric) is not None
    ]
    directions = [comparison_direction(row, metric) for row in paired]
    before_values = [getattr(row.before, metric) for row in paired]
    after_values = [getattr(row.after, metric) for row in paired]
    deltas = [comparison_delta(row, metric) for row in paired]

    def _median(values) -> Optional[float]:
        return float(statistics.median(values)) if values else None

    return ComparisonSummary(
        before_spots=sum(row.before is not None for row in rows),
        after_spots=sum(row.after is not None for row in rows),
        matched_spots=sum(row.status == "matched" for row in rows),
        before_only=sum(row.status == "before_only" for row in rows),
        after_only=sum(row.status == "after_only" for row in rows),
        comparable=len(paired),
        improved=directions.count("improved"),
        unchanged=directions.count("unchanged"),
        worse=directions.count("worse"),
        metric_missing=sum(
            row.status == "matched" and comparison_delta(row, metric) is None
            for row in rows
        ),
        before_median=_median(before_values),
        after_median=_median(after_values),
        median_delta=_median(deltas),
    )


__all__ = [
    "REQUIRED_FIELDS", "OPTIONAL_WALK_FIELDS", "ReportError", "Reading",
    "Report", "ScopeOption", "MetricSpec", "METRICS", "COMPARISON_METRICS",
    "AnalysisRow", "MetricSummary", "WalkOption", "PlaceKey",
    "ComparisonRow", "ComparisonSummary", "load_csv", "load_db",
    "scope_options", "filter_scope", "analysis_rows", "rank_rows",
    "metric_summary", "walk_options", "compare_walks", "comparison_delta",
    "comparison_direction", "rank_comparison_rows", "comparison_summary",
]
