"""Standard-library checks for the retained one-off legacy database migration."""
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ssid_migration", ROOT / "scripts/migrate_ssid_entities.py"
)
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)

LEGACY = """
CREATE TABLE locations(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE rooms(id INTEGER PRIMARY KEY, location_id INTEGER NOT NULL
  REFERENCES locations(id), name TEXT NOT NULL, floor INTEGER NOT NULL DEFAULT 0,
  outdoors INTEGER NOT NULL DEFAULT 0, UNIQUE(location_id, name, floor));
CREATE TABLE spots(id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL
  REFERENCES rooms(id), name TEXT NOT NULL, UNIQUE(room_id, name));
CREATE TABLE readings(id INTEGER PRIMARY KEY, ts TEXT NOT NULL,
  spot_id INTEGER NOT NULL REFERENCES spots(id), ssid TEXT, bssid TEXT,
  rssi INTEGER, noise INTEGER, snr INTEGER, channel TEXT, phy TEXT, tx_rate TEXT,
  ping_ms REAL, down_mbps REAL, up_mbps REAL, server TEXT, note TEXT);
INSERT INTO locations VALUES (1, 'HOME');
INSERT INTO rooms(id, location_id, name) VALUES (1, 1, 'KITCHEN');
INSERT INTO spots VALUES (1, 1, 'WINDOW');
PRAGMA user_version=1;
"""


class MigrationTests(unittest.TestCase):
    def create_database(self, path, spot=1):
        with sqlite3.connect(path) as conn:
            conn.executescript(LEGACY)
            conn.execute(
                "INSERT INTO readings(id,ts,spot_id,ssid,rssi) VALUES(1,?,?,?,-70)",
                ("2026-01-01T00:00:00+00:00", spot, "HOME-WIFI"),
            )

    def test_migration_preserves_data_makes_backup_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.db"
            self.create_database(path)
            backup = MIGRATION.migrate_database(path)
            self.assertTrue(backup.is_file())
            self.assertIsNone(MIGRATION.migrate_database(path))
            with sqlite3.connect(path) as conn:
                reading = conn.execute(
                    "SELECT r.id,r.rssi,s.name FROM readings r "
                    "JOIN ssids s ON s.id=r.ssid_id"
                ).fetchone()
                self.assertEqual(reading, (1, -70, "HOME-WIFI"))
                # The application applies additive path/walk columns on next open.
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            with sqlite3.connect(backup) as conn:
                self.assertEqual(conn.execute("SELECT ssid FROM readings").fetchone()[0], "HOME-WIFI")

    def test_foreign_key_violation_leaves_original_database_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "orphan.db"
            self.create_database(path, spot=999)
            before = path.read_bytes()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "foreign-key violations"):
                MIGRATION.migrate_database(path)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(temp).glob("*.bak")), [])


if __name__ == "__main__":
    unittest.main()
