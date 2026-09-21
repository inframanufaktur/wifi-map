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
    MetricSpec("snr", "SNR", "dB", True, 0),
    MetricSpec("ping_ms", "ping", "ms", False, 1),
    MetricSpec("down_mbps", "down", "Mbps", True, 1),
    MetricSpec("up_mbps", "up", "Mbps", True, 1),
    MetricSpec("delta_rssi", "ΔRSSI", "dB", True, 0),
    MetricSpec("delta_snr", "ΔSNR", "dB", True, 0),
    MetricSpec("delta_down_mbps", "Δdown", "Mbps", True, 1),
    MetricSpec("delta_up_mbps", "Δup", "Mbps", True, 1),
)

_METRIC_BY_KEY = {metric.key: metric for metric in METRICS}


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


@dataclass(frozen=True)
class MetricSummary:
    available: int
    missing: int
    minimum: Optional[float]
    median: Optional[float]
    maximum: Optional[float]


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


_DB_QUERY = """
SELECT r.id, r.ts, r.spot_id, s.room_id, l.id AS location_id,
       l.name AS location_name, m.name AS room_name, s.name AS spot_name,
       m.floor, m.outdoors, r.ssid, r.bssid, r.rssi, r.noise, r.snr,
       r.channel, r.phy, r.tx_rate, r.ping_ms, r.down_mbps, r.up_mbps,
       r.server, r.note,
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
ORDER BY r.id DESC
"""


def load_db(path: Union[str, Path]) -> Report:
    source = Path(path).expanduser()
    if not source.is_file():
        raise ReportError("database does not exist: %s" % (source,))
    uri = source.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(_DB_QUERY).fetchall()
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
        tx_rate=_median_reading_value(readings, "tx_rate"),
        server=_common_reading_value(readings, "server"),
        note=_common_reading_value(readings, "note"),
        noise=_median_reading_value(readings, "noise"),
        **{
            key: _median_reading_value(readings, key)
            for key in _ANALYSIS_METRIC_KEYS
        }
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


__all__ = [
    "REQUIRED_FIELDS", "ReportError", "Reading", "Report", "ScopeOption",
    "MetricSpec", "METRICS", "AnalysisRow", "MetricSummary", "load_csv",
    "load_db", "scope_options", "filter_scope", "analysis_rows",
    "rank_rows", "metric_summary",
]
