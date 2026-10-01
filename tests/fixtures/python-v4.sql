BEGIN TRANSACTION;
CREATE TABLE access_points(
  bssid TEXT PRIMARY KEY,
  name TEXT NOT NULL CHECK(trim(name) <> '')
);
INSERT INTO "access_points" VALUES('aa:bb:cc:dd:ee:ff','Kitchen AP');
CREATE TABLE benchmarks(
  location_id INTEGER PRIMARY KEY REFERENCES locations(id) ON DELETE CASCADE,
  ts TEXT NOT NULL,
  ssid_id INTEGER REFERENCES ssids(id),
  bssid TEXT,
  rssi INTEGER, noise INTEGER, snr INTEGER,
  channel TEXT, phy TEXT, tx_rate TEXT,
  ping_ms REAL, down_mbps REAL, up_mbps REAL,
  server TEXT, note TEXT
);
INSERT INTO "benchmarks" VALUES(1,'2026-01-01T00:00:00+00:00',1,NULL,-40,NULL,40,NULL,NULL,NULL,NULL,500.0,100.0,NULL,NULL);
CREATE TABLE locations(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);
INSERT INTO "locations" VALUES(1,'HOME');
CREATE TABLE readings(
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
INSERT INTO "readings" VALUES(1,'2026-01-01T00:01:00+00:00',1,1,'aa:bb:cc:dd:ee:ff',-67,-93,26,'36','802.11ax','866.7',8.5,201.3,51.2,10,1.2,3.5,0.0,0,8.1,11.5,10.0,1000,NULL,'café, "window"
second line',1);
CREATE TABLE rooms(
  id INTEGER PRIMARY KEY,
  location_id INTEGER NOT NULL REFERENCES locations(id),
  name TEXT NOT NULL,
  floor INTEGER NOT NULL DEFAULT 0,
  outdoors INTEGER NOT NULL DEFAULT 0 CHECK(outdoors IN (0, 1)),
  UNIQUE(location_id, name, floor)
);
INSERT INTO "rooms" VALUES(1,1,'KITCHEN',-1,0);
CREATE TABLE spots(
  id INTEGER PRIMARY KEY,
  room_id INTEGER NOT NULL REFERENCES rooms(id),
  name TEXT NOT NULL,
  UNIQUE(room_id, name)
);
INSERT INTO "spots" VALUES(1,1,'WINDOW');
CREATE TABLE ssids(
  id INTEGER PRIMARY KEY,
  location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK(trim(name) <> ''),
  UNIQUE(location_id, name)
);
INSERT INTO "ssids" VALUES(1,1,'HOME-WIFI');
CREATE TABLE walks(
  id INTEGER PRIMARY KEY,
  location_id INTEGER NOT NULL REFERENCES locations(id),
  ssid_id INTEGER REFERENCES ssids(id),
  name TEXT NOT NULL CHECK(trim(name) <> ''),
  started_at TEXT NOT NULL,
  ended_at TEXT
);
INSERT INTO "walks" VALUES(1,1,1,'before','2026-10-01T17:36:04.333102+00:00','2026-10-01T17:36:04.333736+00:00');
CREATE INDEX idx_rooms_location ON rooms(location_id);
CREATE INDEX idx_ssids_location_name ON ssids(location_id, name);
CREATE INDEX idx_spots_room ON spots(room_id);
CREATE INDEX idx_readings_spot ON readings(spot_id);
CREATE INDEX idx_readings_ssid ON readings(ssid_id);
CREATE INDEX idx_readings_walk_spot ON readings(walk_id, spot_id);
COMMIT;
