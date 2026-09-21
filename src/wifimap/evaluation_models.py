"""Data contracts and validation for WiFi evaluation reports."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple


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
OPTIONAL_AP_FIELDS: Tuple[str, ...] = ("ap_name",)
OPTIONAL_PATH_FIELDS: Tuple[str, ...] = (
    "path_probe_count",
    "gateway_rtt_ms", "gateway_p95_ms", "gateway_loss_pct",
    "gateway_max_outage_ms",
    "internet_rtt_ms", "internet_p95_ms", "internet_loss_pct",
    "internet_max_outage_ms",
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
    ap_name: Optional[str] = None
    path_probe_count: Optional[int] = None
    gateway_rtt_ms: Optional[float] = None
    gateway_p95_ms: Optional[float] = None
    gateway_loss_pct: Optional[float] = None
    gateway_max_outage_ms: Optional[int] = None
    internet_rtt_ms: Optional[float] = None
    internet_p95_ms: Optional[float] = None
    internet_loss_pct: Optional[float] = None
    internet_max_outage_ms: Optional[int] = None

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
            ap_name=_optional_text(row.get("ap_name")),
            path_probe_count=_integer(
                row.get("path_probe_count"), "path_probe_count", row_number),
            gateway_rtt_ms=_number(
                row.get("gateway_rtt_ms"), "gateway_rtt_ms", row_number),
            gateway_p95_ms=_number(
                row.get("gateway_p95_ms"), "gateway_p95_ms", row_number),
            gateway_loss_pct=_number(
                row.get("gateway_loss_pct"), "gateway_loss_pct", row_number),
            gateway_max_outage_ms=_integer(
                row.get("gateway_max_outage_ms"),
                "gateway_max_outage_ms", row_number),
            internet_rtt_ms=_number(
                row.get("internet_rtt_ms"), "internet_rtt_ms", row_number),
            internet_p95_ms=_number(
                row.get("internet_p95_ms"), "internet_p95_ms", row_number),
            internet_loss_pct=_number(
                row.get("internet_loss_pct"), "internet_loss_pct", row_number),
            internet_max_outage_ms=_integer(
                row.get("internet_max_outage_ms"),
                "internet_max_outage_ms", row_number),
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
