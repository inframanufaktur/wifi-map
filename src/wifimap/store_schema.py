"""SQLite schema initialization and persistent record types."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


_SCHEMA = """
CREATE TABLE IF NOT EXISTS locations(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS ssids(
  id INTEGER PRIMARY KEY,
  location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK(trim(name) <> ''),
  UNIQUE(location_id, name)
);
CREATE TABLE IF NOT EXISTS access_points(
  bssid TEXT PRIMARY KEY,
  name TEXT NOT NULL CHECK(trim(name) <> '')
);
CREATE TABLE IF NOT EXISTS walks(
  id INTEGER PRIMARY KEY,
  location_id INTEGER NOT NULL REFERENCES locations(id),
  ssid_id INTEGER REFERENCES ssids(id),
  name TEXT NOT NULL CHECK(trim(name) <> ''),
  started_at TEXT NOT NULL,
  ended_at TEXT
);
CREATE TABLE IF NOT EXISTS rooms(
  id INTEGER PRIMARY KEY,
  location_id INTEGER NOT NULL REFERENCES locations(id),
  name TEXT NOT NULL,
  floor INTEGER NOT NULL DEFAULT 0,
  outdoors INTEGER NOT NULL DEFAULT 0 CHECK(outdoors IN (0, 1)),
  UNIQUE(location_id, name, floor)
);
CREATE TABLE IF NOT EXISTS spots(
  id INTEGER PRIMARY KEY,
  room_id INTEGER NOT NULL REFERENCES rooms(id),
  name TEXT NOT NULL,
  UNIQUE(room_id, name)
);
CREATE TABLE IF NOT EXISTS readings(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  spot_id INTEGER NOT NULL REFERENCES spots(id),
  ssid_id INTEGER REFERENCES ssids(id), bssid TEXT,
  rssi INTEGER, noise INTEGER, snr INTEGER,
  channel TEXT, phy TEXT, tx_rate TEXT,
  ping_ms REAL, down_mbps REAL, up_mbps REAL,
  path_probe_count INTEGER,
  gateway_rtt_ms REAL, gateway_p95_ms REAL, gateway_loss_pct REAL,
  gateway_max_outage_ms INTEGER,
  internet_rtt_ms REAL, internet_p95_ms REAL, internet_loss_pct REAL,
  internet_max_outage_ms INTEGER,
  server TEXT, note TEXT,
  walk_id INTEGER REFERENCES walks(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_rooms_location ON rooms(location_id);
CREATE INDEX IF NOT EXISTS idx_ssids_location_name ON ssids(location_id, name);
CREATE INDEX IF NOT EXISTS idx_spots_room ON spots(room_id);
CREATE INDEX IF NOT EXISTS idx_readings_spot ON readings(spot_id);
CREATE INDEX IF NOT EXISTS idx_readings_ssid ON readings(ssid_id);
CREATE TABLE IF NOT EXISTS benchmarks(
  location_id INTEGER PRIMARY KEY REFERENCES locations(id) ON DELETE CASCADE,
  ts TEXT NOT NULL,
  ssid_id INTEGER REFERENCES ssids(id),
  bssid TEXT,
  rssi INTEGER, noise INTEGER, snr INTEGER,
  channel TEXT, phy TEXT, tx_rate TEXT,
  ping_ms REAL, down_mbps REAL, up_mbps REAL,
  server TEXT, note TEXT
);
"""
_SCHEMA_VERSION = 4

_PATH_READING_COLUMNS = {
    "path_probe_count": "INTEGER",
    "gateway_rtt_ms": "REAL",
    "gateway_p95_ms": "REAL",
    "gateway_loss_pct": "REAL",
    "gateway_max_outage_ms": "INTEGER",
    "internet_rtt_ms": "REAL",
    "internet_p95_ms": "REAL",
    "internet_loss_pct": "REAL",
    "internet_max_outage_ms": "INTEGER",
}


class SchemaMigrationRequired(sqlite3.DatabaseError):
    """Raised when a legacy SSID-string database needs the one-off script."""


@dataclass
class Location:
    id: int
    name: str


@dataclass
class SSID:
    id: int
    location_id: int
    name: str


@dataclass
class Room:
    id: int
    location_id: int
    name: str
    floor: int = 0
    outdoors: bool = False


@dataclass
class Spot:
    id: int
    room_id: int
    name: str


@dataclass
class Walk:
    id: int
    location_id: int
    name: str
    started_at: str
    ended_at: Optional[str] = None
    ssid_id: Optional[int] = None


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply additive migrations within the normalized schema."""
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(readings)")
    }
    with conn:
        for name, sql_type in _PATH_READING_COLUMNS.items():
            if name not in columns:
                conn.execute(
                    "ALTER TABLE readings ADD COLUMN %s %s" % (
                        name, sql_type))
        if "walk_id" not in columns:
            conn.execute(
                "ALTER TABLE readings ADD COLUMN walk_id INTEGER "
                "REFERENCES walks(id) ON DELETE SET NULL"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_readings_walk_spot "
            "ON readings(walk_id, spot_id)"
        )
        walk_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(walks)")
        }
        if "ssid_id" not in walk_columns:
            conn.execute(
                "ALTER TABLE walks ADD COLUMN ssid_id INTEGER "
                "REFERENCES ssids(id)"
            )
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < _SCHEMA_VERSION:
            conn.execute("PRAGMA user_version = %d" % _SCHEMA_VERSION)


def get_db(path: Union[str, Path]) -> sqlite3.Connection:
    """Open DB at path with WAL mode, FK enforcement, and schema init."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        has_readings = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'readings'"
        ).fetchone()
        if has_readings is not None:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(readings)")
            }
            if "ssid" in columns and "ssid_id" not in columns:
                raise SchemaMigrationRequired(
                    "legacy SSID schema; run: .venv/bin/python "
                    "scripts/migrate_ssid_entities.py %s" % path
                )
        conn.executescript(_SCHEMA)
        _migrate_schema(conn)
    except Exception:
        conn.close()
        raise
    return conn
