#!/usr/bin/env python3
"""One-off migration from SSID text columns to location-owned entities."""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union


def _columns(conn: sqlite3.Connection, table: str):
    return {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}


def _tables(conn: sqlite3.Connection):
    return {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _backup_database(conn: sqlite3.Connection, source: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = source.with_name(
        "%s.pre-ssid-entities-%s.bak" % (source.name, stamp)
    )
    backup_conn = sqlite3.connect(str(backup))
    try:
        conn.backup(backup_conn)
    finally:
        backup_conn.close()
    return backup


def migrate_database(path: Union[str, Path]) -> Optional[Path]:
    """Migrate one database and return its backup, or None if already done."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("database not found: %s" % source)
    conn = sqlite3.connect(str(source))
    backup: Optional[Path] = None
    try:
        tables = _tables(conn)
        required = {"locations", "rooms", "spots", "readings"}
        missing = sorted(required - tables)
        if missing:
            raise sqlite3.DatabaseError(
                "database is missing required tables: %s" % ", ".join(missing)
            )
        reading_columns = _columns(conn, "readings")
        if "ssid_id" in reading_columns and "ssid" not in reading_columns:
            return None
        if "ssid" not in reading_columns:
            raise sqlite3.DatabaseError("unsupported readings schema")
        if "benchmarks" in tables and "ssid" not in _columns(conn, "benchmarks"):
            raise sqlite3.DatabaseError("unsupported benchmarks schema")
        legacy_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if legacy_violations:
            raise sqlite3.IntegrityError(
                "legacy database has foreign-key violations; refusing "
                "migration: %r" % (legacy_violations,)
            )
        legacy_reading_count = conn.execute(
            "SELECT COUNT(*) FROM readings").fetchone()[0]
        legacy_benchmark_count = (
            conn.execute("SELECT COUNT(*) FROM benchmarks").fetchone()[0]
            if "benchmarks" in tables else 0
        )

        backup = _backup_database(conn, source)
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "CREATE TABLE ssids("
                "id INTEGER PRIMARY KEY, "
                "location_id INTEGER NOT NULL REFERENCES locations(id) "
                "ON DELETE CASCADE, "
                "name TEXT NOT NULL CHECK(trim(name) <> ''), "
                "UNIQUE(location_id, name))"
            )
            conn.execute(
                "INSERT INTO ssids(location_id, name) "
                "SELECT DISTINCT m.location_id, trim(r.ssid) "
                "FROM readings r "
                "JOIN spots s ON s.id = r.spot_id "
                "JOIN rooms m ON m.id = s.room_id "
                "WHERE r.ssid IS NOT NULL AND trim(r.ssid) <> ''"
            )
            if "benchmarks" in tables:
                conn.execute(
                    "INSERT OR IGNORE INTO ssids(location_id, name) "
                    "SELECT location_id, trim(ssid) FROM benchmarks "
                    "WHERE ssid IS NOT NULL AND trim(ssid) <> ''"
                )

            if "walks" not in tables:
                conn.execute(
                    "CREATE TABLE walks("
                    "id INTEGER PRIMARY KEY, "
                    "location_id INTEGER NOT NULL REFERENCES locations(id), "
                    "ssid_id INTEGER REFERENCES ssids(id), "
                    "name TEXT NOT NULL CHECK(trim(name) <> ''), "
                    "started_at TEXT NOT NULL, ended_at TEXT)"
                )
            elif "ssid_id" not in _columns(conn, "walks"):
                conn.execute(
                    "ALTER TABLE walks ADD COLUMN ssid_id INTEGER "
                    "REFERENCES ssids(id)"
                )

            has_walk_id = "walk_id" in reading_columns
            conn.execute("ALTER TABLE readings RENAME TO readings_legacy_ssid")
            conn.execute(
                "CREATE TABLE readings("
                "id INTEGER PRIMARY KEY, ts TEXT NOT NULL, "
                "spot_id INTEGER NOT NULL REFERENCES spots(id), "
                "ssid_id INTEGER REFERENCES ssids(id), bssid TEXT, "
                "rssi INTEGER, noise INTEGER, snr INTEGER, "
                "channel TEXT, phy TEXT, tx_rate TEXT, ping_ms REAL, "
                "down_mbps REAL, up_mbps REAL, server TEXT, note TEXT, "
                "walk_id INTEGER REFERENCES walks(id) ON DELETE SET NULL)"
            )
            walk_expr = "r.walk_id" if has_walk_id else "NULL"
            conn.execute(
                "INSERT INTO readings("
                "id, ts, spot_id, ssid_id, bssid, rssi, noise, snr, "
                "channel, phy, tx_rate, ping_ms, down_mbps, up_mbps, "
                "server, note, walk_id) "
                "SELECT r.id, r.ts, r.spot_id, n.id, r.bssid, r.rssi, "
                "r.noise, r.snr, r.channel, r.phy, r.tx_rate, r.ping_ms, "
                "r.down_mbps, r.up_mbps, r.server, r.note, %s "
                "FROM readings_legacy_ssid r "
                "JOIN spots s ON s.id = r.spot_id "
                "JOIN rooms m ON m.id = s.room_id "
                "LEFT JOIN ssids n ON n.location_id = m.location_id "
                "AND n.name = trim(r.ssid)" % walk_expr
            )
            conn.execute("DROP TABLE readings_legacy_ssid")

            if "benchmarks" in tables:
                conn.execute("ALTER TABLE benchmarks RENAME TO benchmarks_legacy_ssid")
            conn.execute(
                "CREATE TABLE benchmarks("
                "location_id INTEGER PRIMARY KEY REFERENCES locations(id) "
                "ON DELETE CASCADE, ts TEXT NOT NULL, "
                "ssid_id INTEGER REFERENCES ssids(id), bssid TEXT, "
                "rssi INTEGER, noise INTEGER, snr INTEGER, channel TEXT, "
                "phy TEXT, tx_rate TEXT, ping_ms REAL, down_mbps REAL, "
                "up_mbps REAL, server TEXT, note TEXT)"
            )
            if "benchmarks" in tables:
                conn.execute(
                    "INSERT INTO benchmarks("
                    "location_id, ts, ssid_id, bssid, rssi, noise, snr, "
                    "channel, phy, tx_rate, ping_ms, down_mbps, up_mbps, "
                    "server, note) "
                    "SELECT b.location_id, b.ts, n.id, b.bssid, b.rssi, "
                    "b.noise, b.snr, b.channel, b.phy, b.tx_rate, "
                    "b.ping_ms, b.down_mbps, b.up_mbps, b.server, b.note "
                    "FROM benchmarks_legacy_ssid b "
                    "LEFT JOIN ssids n ON n.location_id = b.location_id "
                    "AND n.name = trim(b.ssid)"
                )
                conn.execute("DROP TABLE benchmarks_legacy_ssid")

            migrated_reading_count = conn.execute(
                "SELECT COUNT(*) FROM readings").fetchone()[0]
            migrated_benchmark_count = conn.execute(
                "SELECT COUNT(*) FROM benchmarks").fetchone()[0]
            if migrated_reading_count != legacy_reading_count:
                raise sqlite3.IntegrityError(
                    "reading count changed during migration: %d -> %d"
                    % (legacy_reading_count, migrated_reading_count)
                )
            if migrated_benchmark_count != legacy_benchmark_count:
                raise sqlite3.IntegrityError(
                    "benchmark count changed during migration: %d -> %d"
                    % (legacy_benchmark_count, migrated_benchmark_count)
                )

            conn.execute(
                "UPDATE walks SET ssid_id = ("
                "SELECT CASE WHEN COUNT(DISTINCT r.ssid_id) = 1 "
                "THEN MIN(r.ssid_id) END FROM readings r "
                "WHERE r.walk_id = walks.id AND r.ssid_id IS NOT NULL)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rooms_location "
                "ON rooms(location_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ssids_location_name "
                "ON ssids(location_id, name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_spots_room ON spots(room_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_readings_spot "
                "ON readings(spot_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_readings_ssid "
                "ON readings(ssid_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_readings_walk_spot "
                "ON readings(walk_id, spot_id)"
            )
            conn.execute("PRAGMA user_version = 2")
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError(
                    "foreign-key check failed: %r" % (violations,)
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
        return backup
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate wifimap SSID strings to location-owned entities."
    )
    parser.add_argument("database", help="Path to the SQLite database")
    args = parser.parse_args()
    backup = migrate_database(args.database)
    if backup is None:
        print("already migrated")
    else:
        print("migrated; backup: %s" % backup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
