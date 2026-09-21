"""Load evaluation reports from CSV exports and SQLite databases."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Union

from wifimap.evaluation_models import (
    REQUIRED_FIELDS,
    Reading,
    Report,
    ReportError,
)


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
