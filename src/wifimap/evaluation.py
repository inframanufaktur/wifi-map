"""Shared evaluation model for SQLite readings and exported CSV reports."""
from __future__ import annotations

import csv
import math
import sqlite3
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple, Union


REQUIRED_FIELDS: Tuple[str, ...] = (
    "id", "ts", "spot_id", "room_id", "location_id", "location_name",
    "room_name", "spot_name", "floor", "outdoors", "ssid", "bssid",
    "rssi", "noise", "snr", "channel", "phy", "tx_rate", "ping_ms",
    "down_mbps", "up_mbps", "server", "note", "delta_rssi",
    "delta_snr", "delta_down_mbps", "delta_up_mbps",
)

OPTIONAL_WALK_FIELDS: Tuple[str, ...] = (
    "walk_id", "walk_name", "walk_started_at", "walk_ended_at",
)


class ReportError(ValueError):
    """Raised when an evaluation source cannot be opened or parsed."""


def _optional_text(value: object) -> Optional[str]:
    if value is None or value == "":
        return None
    return str(value)


def _number(value: object, field: str, row_number: int) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ReportError("row %d field %s is not numeric" % (
            row_number, field))
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ReportError("row %d field %s is not numeric: %r" % (
            row_number, field, value)) from exc
    if not math.isfinite(number):
        raise ReportError("row %d field %s must be finite: %r" % (
            row_number, field, value))
    return number


def _integer(value: object, field: str, row_number: int) -> Optional[int]:
    number = _number(value, field, row_number)
    if number is None:
        return None
    if not number.is_integer():
        raise ReportError("row %d field %s is not an integer: %r" % (
            row_number, field, value))
    return int(number)


@dataclass(frozen=True)
class Reading:
    id: Optional[int]
    ts: str
    spot_id: Optional[int]
    room_id: Optional[int]
    location_id: Optional[int]
    location_name: str
    room_name: str
    spot_name: str
    floor: Optional[int]
    outdoors: bool
    ssid: Optional[str]
    bssid: Optional[str]
    rssi: Optional[float]
    noise: Optional[float]
    snr: Optional[float]
    channel: Optional[str]
    phy: Optional[str]
    tx_rate: Optional[float]
    ping_ms: Optional[float]
    down_mbps: Optional[float]
    up_mbps: Optional[float]
    server: Optional[str]
    note: Optional[str]
    delta_rssi: Optional[float]
    delta_snr: Optional[float]
    delta_down_mbps: Optional[float]
    delta_up_mbps: Optional[float]
    walk_id: Optional[int] = None
    walk_name: Optional[str] = None
    walk_started_at: Optional[str] = None
    walk_ended_at: Optional[str] = None

    @classmethod
    def from_mapping(cls, row: Mapping[str, object],
                     row_number: int = 1) -> "Reading":
        outdoors = _integer(row.get("outdoors"), "outdoors", row_number)
        if outdoors not in (None, 0, 1):
            raise ReportError("row %d field outdoors must be 0 or 1" % (
                row_number,))
        return cls(
            id=_integer(row.get("id"), "id", row_number),
            ts=str(row.get("ts") or ""),
            spot_id=_integer(row.get("spot_id"), "spot_id", row_number),
            room_id=_integer(row.get("room_id"), "room_id", row_number),
            location_id=_integer(
                row.get("location_id"), "location_id", row_number),
            location_name=str(row.get("location_name") or ""),
            room_name=str(row.get("room_name") or ""),
            spot_name=str(row.get("spot_name") or ""),
            floor=_integer(row.get("floor"), "floor", row_number),
            outdoors=bool(outdoors),
            ssid=_optional_text(row.get("ssid")),
            bssid=_optional_text(row.get("bssid")),
            rssi=_number(row.get("rssi"), "rssi", row_number),
            noise=_number(row.get("noise"), "noise", row_number),
            snr=_number(row.get("snr"), "snr", row_number),
            channel=_optional_text(row.get("channel")),
            phy=_optional_text(row.get("phy")),
            tx_rate=_number(row.get("tx_rate"), "tx_rate", row_number),
            ping_ms=_number(row.get("ping_ms"), "ping_ms", row_number),
            down_mbps=_number(
                row.get("down_mbps"), "down_mbps", row_number),
            up_mbps=_number(row.get("up_mbps"), "up_mbps", row_number),
            server=_optional_text(row.get("server")),
            note=_optional_text(row.get("note")),
            delta_rssi=_number(
                row.get("delta_rssi"), "delta_rssi", row_number),
            delta_snr=_number(
                row.get("delta_snr"), "delta_snr", row_number),
            delta_down_mbps=_number(
                row.get("delta_down_mbps"), "delta_down_mbps", row_number),
            delta_up_mbps=_number(
                row.get("delta_up_mbps"), "delta_up_mbps", row_number),
            walk_id=_integer(row.get("walk_id"), "walk_id", row_number),
            walk_name=_optional_text(row.get("walk_name")),
            walk_started_at=_optional_text(row.get("walk_started_at")),
            walk_ended_at=_optional_text(row.get("walk_ended_at")),
        )


@dataclass(frozen=True)
class Report:
    source: str
    readings: Tuple[Reading, ...]


@dataclass(frozen=True)
class ScopeOption:
    kind: str
    value: Optional[str]
    label: str
    count: int


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    unit: str
    higher_is_better: bool
    decimals: int = 1


METRICS: Tuple[MetricSpec, ...] = (
    MetricSpec("rssi", "RSSI", "dBm", True, 0),
    MetricSpec("noise", "noise", "dBm", False, 0),
    MetricSpec("snr", "SNR", "dB", True, 0),
    MetricSpec("tx_rate", "TX rate", "Mbps", True, 0),
    MetricSpec("ping_ms", "ping", "ms", False, 1),
    MetricSpec("down_mbps", "down", "Mbps", True, 1),
    MetricSpec("up_mbps", "up", "Mbps", True, 1),
    MetricSpec("delta_rssi", "ΔRSSI", "dB", True, 0),
    MetricSpec("delta_snr", "ΔSNR", "dB", True, 0),
    MetricSpec("delta_down_mbps", "Δdown", "Mbps", True, 1),
    MetricSpec("delta_up_mbps", "Δup", "Mbps", True, 1),
)

_METRIC_BY_KEY = {metric.key: metric for metric in METRICS}

_COMPARISON_METRIC_KEYS = (
    "rssi", "noise", "snr", "tx_rate", "ping_ms", "down_mbps", "up_mbps",
)
COMPARISON_METRICS: Tuple[MetricSpec, ...] = tuple(
    _METRIC_BY_KEY[key] for key in _COMPARISON_METRIC_KEYS
)
_COMPARISON_METRIC_BY_KEY = {
    metric.key: metric for metric in COMPARISON_METRICS
}


@dataclass(frozen=True)
class AnalysisRow:
    location_name: str
    room_name: str
    spot_name: str
    floor: Optional[int]
    outdoors: bool
    count: int
    latest_ts: str
    ssid: Optional[str]
    bssid: Optional[str]
    channel: Optional[str]
    phy: Optional[str]
    tx_rate: Optional[float]
    server: Optional[str]
    note: Optional[str]
    noise: Optional[float]
    rssi: Optional[float]
    snr: Optional[float]
    ping_ms: Optional[float]
    down_mbps: Optional[float]
    up_mbps: Optional[float]
    delta_rssi: Optional[float]
    delta_snr: Optional[float]
    delta_down_mbps: Optional[float]
    delta_up_mbps: Optional[float]
    readings: Tuple[Reading, ...]


@dataclass(frozen=True)
class MetricSummary:
    available: int
    missing: int
    minimum: Optional[float]
    median: Optional[float]
    maximum: Optional[float]


@dataclass(frozen=True)
class WalkOption:
    walk_id: int
    name: str
    location_id: Optional[int]
    location_name: str
    started_at: Optional[str]
    ended_at: Optional[str]
    reading_count: int
    spot_count: int


@dataclass(frozen=True)
class PlaceKey:
    spot_id: Optional[int]
    location_name: str
    room_name: str
    floor: Optional[int]
    spot_name: str


@dataclass(frozen=True)
class ComparisonRow:
    place: PlaceKey
    status: str
    before: Optional[AnalysisRow]
    after: Optional[AnalysisRow]


@dataclass(frozen=True)
class ComparisonSummary:
    before_spots: int
    after_spots: int
    matched_spots: int
    before_only: int
    after_only: int
    comparable: int
    improved: int
    unchanged: int
    worse: int
    metric_missing: int
    before_median: Optional[float]
    after_median: Optional[float]
    median_delta: Optional[float]


def load_csv(path: Union[str, Path]) -> Report:
    source = Path(path).expanduser()
    try:
        with source.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            fields = set(reader.fieldnames or ())
            missing = [field for field in REQUIRED_FIELDS if field not in fields]
            if missing:
                raise ReportError("missing required columns: %s" % (
                    ", ".join(missing),))
            readings = tuple(
                Reading.from_mapping(row, row_number=index)
                for index, row in enumerate(reader, start=2)
            )
    except ReportError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ReportError("cannot read CSV %s: %s" % (source, exc)) from exc
    return Report(source=str(source), readings=readings)


_DB_QUERY_TEMPLATE = """
SELECT r.id, r.ts, r.spot_id, s.room_id, l.id AS location_id,
       l.name AS location_name, m.name AS room_name, s.name AS spot_name,
       m.floor, m.outdoors, {ssid_field}, r.bssid, r.rssi, r.noise, r.snr,
       r.channel, r.phy, r.tx_rate, r.ping_ms, r.down_mbps, r.up_mbps,
       r.server, r.note, {walk_fields},
       CASE WHEN r.rssi IS NOT NULL AND b.rssi IS NOT NULL
            THEN r.rssi - b.rssi END AS delta_rssi,
       CASE WHEN r.snr IS NOT NULL AND b.snr IS NOT NULL
            THEN r.snr - b.snr END AS delta_snr,
       CASE WHEN r.down_mbps IS NOT NULL AND b.down_mbps IS NOT NULL
            THEN r.down_mbps - b.down_mbps END AS delta_down_mbps,
       CASE WHEN r.up_mbps IS NOT NULL AND b.up_mbps IS NOT NULL
            THEN r.up_mbps - b.up_mbps END AS delta_up_mbps
FROM readings r
JOIN spots s ON s.id = r.spot_id
JOIN rooms m ON m.id = s.room_id
JOIN locations l ON l.id = m.location_id
LEFT JOIN benchmarks b ON b.location_id = l.id
{ssid_join}
{walk_join}
ORDER BY r.id DESC
"""

_DB_QUERY = _DB_QUERY_TEMPLATE.format(
    ssid_field="n.name AS ssid",
    ssid_join="LEFT JOIN ssids n ON n.id = r.ssid_id",
    walk_fields=(
        "r.walk_id, w.name AS walk_name, "
        "w.started_at AS walk_started_at, w.ended_at AS walk_ended_at"
    ),
    walk_join="LEFT JOIN walks w ON w.id = r.walk_id",
)

_LEGACY_DB_QUERY = _DB_QUERY_TEMPLATE.format(
    ssid_field="n.name AS ssid",
    ssid_join="LEFT JOIN ssids n ON n.id = r.ssid_id",
    walk_fields=(
        "NULL AS walk_id, NULL AS walk_name, "
        "NULL AS walk_started_at, NULL AS walk_ended_at"
    ),
    walk_join="",
)

_STRING_DB_QUERY = _DB_QUERY_TEMPLATE.format(
    ssid_field="r.ssid",
    ssid_join="",
    walk_fields=(
        "r.walk_id, w.name AS walk_name, "
        "w.started_at AS walk_started_at, w.ended_at AS walk_ended_at"
    ),
    walk_join="LEFT JOIN walks w ON w.id = r.walk_id",
)

_STRING_LEGACY_DB_QUERY = _DB_QUERY_TEMPLATE.format(
    ssid_field="r.ssid",
    ssid_join="",
    walk_fields=(
        "NULL AS walk_id, NULL AS walk_name, "
        "NULL AS walk_started_at, NULL AS walk_ended_at"
    ),
    walk_join="",
)


def _has_walk_schema(conn: sqlite3.Connection) -> bool:
    reading_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(readings)")
    }
    walk_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(walks)")
    }
    return (
        "walk_id" in reading_columns
        and {"id", "name", "started_at", "ended_at"} <= walk_columns
    )


def _has_normalized_ssid_schema(conn: sqlite3.Connection) -> bool:
    return "ssid_id" in {
        row[1] for row in conn.execute("PRAGMA table_info(readings)")
    }


def load_db(path: Union[str, Path]) -> Report:
    source = Path(path).expanduser()
    if not source.is_file():
        raise ReportError("database does not exist: %s" % (source,))
    uri = source.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            has_walks = _has_walk_schema(conn)
            normalized = _has_normalized_ssid_schema(conn)
            if normalized:
                query = _DB_QUERY if has_walks else _LEGACY_DB_QUERY
            else:
                query = (_STRING_DB_QUERY if has_walks
                         else _STRING_LEGACY_DB_QUERY)
            rows = conn.execute(query).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ReportError("cannot read database %s: %s" % (
            source, exc)) from exc
    parsed = []
    for index, row in enumerate(rows, start=1):
        values = dict(row)
        for key in ("delta_down_mbps", "delta_up_mbps"):
            if values[key] is not None:
                values[key] = round(values[key], 1)
        parsed.append(Reading.from_mapping(values, row_number=index))
    readings = tuple(parsed)
    return Report(source=str(source), readings=readings)


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
