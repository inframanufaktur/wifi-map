use wifimap::model::{Reading, Signal, Speed};
use wifimap::store::{Filter, Store};

fn setup() -> (Store, i64, i64) {
    let db = Store::memory().unwrap();
    let location = db.resolve_location("home").unwrap();
    let room = db.resolve_room(location, "office", 2, false).unwrap();
    let spot = db.resolve_spot(room, "desk").unwrap();
    (db, location, spot)
}

#[test]
fn readings_join_entities_keep_nulls_and_filter_numeric_names() {
    let (db, location, spot) = setup();
    let reading = Reading {
        spot_id: spot,
        signal: Signal {
            ssid: Some("net".into()),
            rssi: Some(-61.0),
            bssid: Some("aa:bb:cc:dd:ee:ff".into()),
            ..Default::default()
        },
        ..Default::default()
    };
    db.set_access_point_name("AA-BB-CC-DD-EE-FF", "Desk AP")
        .unwrap();
    db.add_reading(&reading).unwrap();
    let rows = db
        .list_readings(&Filter {
            location: Some(location.to_string()),
            ..Default::default()
        })
        .unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].location, "home");
    assert_eq!(rows[0].room, "office");
    assert_eq!(rows[0].floor, 2);
    assert_eq!(rows[0].ap_name.as_deref(), Some("Desk AP"));
    assert_eq!(rows[0].speed.down_mbps, None);
    assert_eq!(db.list_ssids(location).unwrap().len(), 1);
    assert!(db.lookup_location("typo").is_err());
    assert_eq!(db.list_locations().unwrap().len(), 1);
    assert!(
        db.list_readings(&Filter {
            limit: Some(0),
            ..Default::default()
        })
        .unwrap()
        .is_empty()
    );
}

#[test]
fn foreign_location_walks_and_networks_are_rejected() {
    let (db, location, spot) = setup();
    let other = db.resolve_location("away").unwrap();
    let ssid = db.resolve_ssid(other, "net").unwrap();
    assert!(
        db.add_reading(&Reading {
            spot_id: spot,
            ssid_id: Some(ssid),
            ..Default::default()
        })
        .is_err()
    );
    let walk = db.create_walk(other, Some(ssid), "before").unwrap();
    assert!(
        db.add_reading(&Reading {
            spot_id: spot,
            walk_id: Some(walk),
            ..Default::default()
        })
        .is_err()
    );
    assert!(db.create_walk(location, Some(ssid), "foreign").is_err());
    assert!(
        db.set_benchmark(
            location,
            &Reading {
                ssid_id: Some(ssid),
                ..Default::default()
            }
        )
        .is_err()
    );
}

#[test]
fn walk_finish_is_idempotent_and_benchmark_changes_are_joined() {
    let (db, location, spot) = setup();
    let walk = db.create_walk(location, None, "before").unwrap();
    db.finish_walk(walk).unwrap();
    let end = db.get_walk(walk).unwrap().unwrap().ended_at;
    db.finish_walk(walk).unwrap();
    assert_eq!(db.get_walk(walk).unwrap().unwrap().ended_at, end);
    db.set_benchmark(
        location,
        &Reading {
            signal: Signal {
                rssi: Some(-50.0),
                ..Default::default()
            },
            speed: Speed {
                down_mbps: Some(280.0),
                ..Default::default()
            },
            ..Default::default()
        },
    )
    .unwrap();
    db.add_reading(&Reading {
        spot_id: spot,
        walk_id: Some(walk),
        signal: Signal {
            rssi: Some(-61.0),
            ..Default::default()
        },
        speed: Speed {
            down_mbps: Some(220.4),
            ..Default::default()
        },
        ..Default::default()
    })
    .unwrap();
    let rows = db.list_readings(&Filter::default()).unwrap();
    assert_eq!(rows[0].delta_rssi, Some(-11.0));
    assert_eq!(rows[0].delta_down_mbps, Some(-59.6));
    assert_eq!(rows[0].walk_name.as_deref(), Some("before"));
}

#[test]
fn rejects_legacy_database_before_modifying_it() {
    let temp = tempfile::NamedTempFile::new().unwrap();
    let db = rusqlite::Connection::open(temp.path()).unwrap();
    db.execute_batch("CREATE TABLE readings(id INTEGER PRIMARY KEY, ssid TEXT);")
        .unwrap();
    assert!(
        Store::open(temp.path())
            .unwrap_err()
            .to_string()
            .contains("legacy SSID")
    );
    assert_eq!(
        db.query_row(
            "SELECT count(*) FROM sqlite_master WHERE name='locations'",
            [],
            |r| r.get::<_, i64>(0)
        )
        .unwrap(),
        0
    );
}

#[test]
fn normalized_v1_database_reopens_with_additive_migrations_and_keeps_rows() {
    let temp = tempfile::NamedTempFile::new().unwrap();
    let connection = rusqlite::Connection::open(temp.path()).unwrap();
    connection.execute_batch(OLD_SCHEMA).unwrap();
    drop(connection);
    let readonly = Store::open_readonly(temp.path()).unwrap();
    let rows = readonly.list_readings(&Filter::default()).unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].signal.ssid.as_deref(), Some("home-net"));
    assert_eq!(rows[0].path.gateway_rtt_ms, None);
    assert_eq!(rows[0].walk_id, None);
    drop(readonly);
    let connection = rusqlite::Connection::open(temp.path()).unwrap();
    assert_eq!(
        connection
            .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        1
    );
    drop(connection);
    let db = Store::open(temp.path()).unwrap();
    assert_eq!(db.list_readings(&Filter::default()).unwrap(), rows);
    let connection = rusqlite::Connection::open(temp.path()).unwrap();
    assert_eq!(
        connection
            .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        4
    );
    assert_eq!(
        connection
            .query_row("SELECT count(*) FROM readings", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        1
    );
}

#[test]
fn detected_numeric_network_names_remain_literal_and_case_sensitive() {
    let (db, location, spot) = setup();
    let id = db.resolve_ssid(location, "different-name").unwrap();
    db.add_reading(&Reading {
        spot_id: spot,
        signal: Signal {
            ssid: Some(id.to_string()),
            ..Default::default()
        },
        ..Default::default()
    })
    .unwrap();
    assert_eq!(
        db.list_readings(&Filter::default()).unwrap()[0].signal.ssid,
        Some(id.to_string())
    );
    assert_eq!(db.list_ssids(location).unwrap().len(), 2);
    assert_ne!(
        db.resolve_ssid(location, "NET").unwrap(),
        db.resolve_ssid(location, "net").unwrap()
    );
}

#[test]
fn readonly_missing_database_is_not_created() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("missing.sqlite");
    assert!(Store::open_readonly(&path).is_err());
    assert!(!path.exists());
}

#[test]
fn numeric_string_lookup_and_scoped_hierarchy_preserve_name_fallback() {
    let db = Store::memory().unwrap();
    let home = db.create_location("home").unwrap();
    let office = db.create_location("office").unwrap();
    assert_eq!(db.resolve_location(&home.to_string()).unwrap(), home);
    let numbered_location = db.resolve_location("9999").unwrap();
    assert_eq!(
        db.get_location(numbered_location).unwrap().unwrap().name,
        "9999"
    );
    let first = db.resolve_room(home, "shared", 0, false).unwrap();
    let upstairs = db.resolve_room(home, "shared", 1, false).unwrap();
    let other = db.resolve_room(office, "shared", 0, false).unwrap();
    assert_ne!(first, upstairs);
    assert_ne!(first, other);
    assert_eq!(db.resolve_room(home, "shared", 1, true).unwrap(), upstairs);
    assert_eq!(db.lookup_room(home, &first.to_string()).unwrap(), first);
    assert!(db.lookup_room(home, &other.to_string()).is_err());
    let numbered_room = db.resolve_room(home, "8888", -1, true).unwrap();
    assert_eq!(db.get_room(numbered_room).unwrap().unwrap().name, "8888");
    let spot = db.resolve_spot(first, "window").unwrap();
    assert_eq!(db.resolve_spot(first, &spot.to_string()).unwrap(), spot);
    assert!(db.lookup_spot(upstairs, &spot.to_string()).is_err());
    assert_ne!(spot, db.resolve_spot(upstairs, "window").unwrap());
    assert!(db.create_room(home, "shared", 0, false).is_err());
    assert!(db.create_spot(first, "window").is_err());
}

#[test]
fn walk_network_validation_and_filters_keep_partial_measurements() {
    let (db, location, spot) = setup();
    let network = db.resolve_ssid(location, "network").unwrap();
    let other_network = db.resolve_ssid(location, "other").unwrap();
    let walk = db.create_walk(location, Some(network), "baseline").unwrap();
    assert!(
        db.add_reading(&Reading {
            spot_id: spot,
            ssid_id: Some(other_network),
            walk_id: Some(walk),
            ..Default::default()
        })
        .is_err()
    );
    assert!(
        db.add_reading(&Reading {
            spot_id: spot,
            ssid_id: Some(network),
            signal: Signal {
                ssid: Some("mismatch".into()),
                ..Default::default()
            },
            ..Default::default()
        })
        .is_err()
    );
    let id = db
        .add_reading(&Reading {
            spot_id: spot,
            walk_id: Some(walk),
            ..Default::default()
        })
        .unwrap();
    let rows = db
        .list_readings(&Filter {
            walk_id: Some(walk),
            floor: Some(2),
            room: Some("office".into()),
            spot: Some(spot.to_string()),
            ..Default::default()
        })
        .unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].id, id);
    assert_eq!(rows[0].signal.ssid, None);
    assert_eq!(rows[0].speed.ping_ms, None);
    assert!(
        db.list_readings(&Filter {
            ssid: Some("network".into()),
            ..Default::default()
        })
        .unwrap()
        .is_empty()
    );
    assert_eq!(db.resolve_walk(location, "baseline").unwrap(), walk);
    assert_eq!(db.resolve_walk(location, &walk.to_string()).unwrap(), walk);
    assert!(db.resolve_walk(location, "unknown").is_err());
    assert!(db.create_walk(location, None, "   ").is_err());
    assert!(db.finish_walk(99999).is_err());
}

#[test]
fn benchmark_upsert_clear_and_delta_format_keep_optional_values() {
    let (db, location, _) = setup();
    let benchmark = Reading {
        signal: Signal {
            rssi: Some(-45.0),
            snr: Some(32.0),
            ..Default::default()
        },
        speed: Speed {
            down_mbps: Some(310.5),
            up_mbps: Some(48.2),
            ..Default::default()
        },
        ..Default::default()
    };
    db.set_benchmark(location, &benchmark).unwrap();
    let current = Reading {
        signal: Signal {
            rssi: Some(-67.0),
            snr: Some(24.0),
            ..Default::default()
        },
        speed: Speed {
            down_mbps: Some(48.1),
            up_mbps: Some(10.0),
            ..Default::default()
        },
        ..Default::default()
    };
    assert_eq!(
        wifimap::store::format_benchmark_delta(&current, Some(&benchmark)),
        "rssi -22, snr -8, down -262.4, up -38.2"
    );
    assert_eq!(
        wifimap::store::format_benchmark_delta(&Reading::default(), Some(&benchmark)),
        ""
    );
    assert_eq!(wifimap::store::format_benchmark_delta(&current, None), "");
    db.set_benchmark(location, &current).unwrap();
    assert_eq!(
        db.get_benchmark(location).unwrap().unwrap().signal.rssi,
        Some(-67.0)
    );
    db.clear_benchmark(location).unwrap();
    assert_eq!(db.get_benchmark(location).unwrap(), None);
    assert!(db.set_benchmark(99999, &current).is_err());
    assert_eq!(db.get_benchmark(99999).unwrap(), None);
}

#[test]
fn fractional_radio_values_in_existing_sqlite_rows_are_preserved() {
    let file = tempfile::NamedTempFile::new().unwrap();
    let conn = rusqlite::Connection::open(file.path()).unwrap();
    conn.execute_batch(OLD_SCHEMA).unwrap();
    conn.execute(
        "UPDATE readings SET rssi=?,noise=?,snr=? WHERE id=1",
        rusqlite::params![-61.25, -92.75, 31.5],
    )
    .unwrap();
    drop(conn);
    let readonly = Store::open_readonly(file.path()).unwrap();
    let reading = readonly
        .list_readings(&Filter::default())
        .unwrap()
        .remove(0);
    assert_eq!(reading.signal.rssi, Some(-61.25));
    assert_eq!(reading.signal.noise, Some(-92.75));
    assert_eq!(reading.signal.snr, Some(31.5));
    drop(readonly);
    let writable = Store::open(file.path()).unwrap();
    writable.add_reading(&reading).unwrap();
    assert_eq!(
        writable.list_readings(&Filter::default()).unwrap()[0].signal,
        reading.signal
    );
}

#[test]
fn duplicate_walk_names_require_explicit_ids() {
    let (db, location, _) = setup();
    let first = db.create_walk(location, None, "baseline").unwrap();
    let second = db.create_walk(location, None, "baseline").unwrap();
    let error = db
        .resolve_walk(location, " baseline ")
        .unwrap_err()
        .to_string();
    assert!(
        error.contains("ambiguous")
            && error.contains(&first.to_string())
            && error.contains(&second.to_string())
    );
    assert_eq!(
        db.resolve_walk(location, &first.to_string()).unwrap(),
        first
    );
    assert_eq!(
        db.resolve_walk(location, &second.to_string()).unwrap(),
        second
    );
}

#[test]
fn legacy_evaluation_is_readonly_and_preserves_raw_network_names() {
    let file = tempfile::NamedTempFile::new().unwrap();
    let legacy=OLD_SCHEMA.replace("ssid_id INTEGER REFERENCES ssids(id)","ssid TEXT").replace("INSERT INTO readings(id,ts,spot_id,ssid_id,rssi) VALUES (1,'2026-09-21T10:00:00Z',1,1,-61)","INSERT INTO readings(id,ts,spot_id,ssid,rssi) VALUES (1,'2026-09-21T10:00:00Z',1,'legacy-net',-61)");
    let connection = rusqlite::Connection::open(file.path()).unwrap();
    connection.execute_batch(&legacy).unwrap();
    let store = Store::open_readonly(file.path()).unwrap();
    let rows = store
        .list_readings(&Filter {
            ssid: Some("legacy-net".into()),
            ..Default::default()
        })
        .unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].signal.ssid.as_deref(), Some("legacy-net"));
    assert_eq!(rows[0].ssid_id, None);
    assert_eq!(wifimap::evaluation::load_db(file.path()).unwrap(), rows);
    assert_eq!(
        connection
            .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        1
    );
    assert_eq!(
        connection
            .query_row("SELECT ssid FROM readings", [], |r| r.get::<_, String>(0))
            .unwrap(),
        "legacy-net"
    );
    assert!(Store::open(file.path()).is_err());
}

const OLD_SCHEMA: &str = r#"
CREATE TABLE locations(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL);
CREATE TABLE ssids(id INTEGER PRIMARY KEY,location_id INTEGER NOT NULL REFERENCES locations(id),name TEXT NOT NULL,UNIQUE(location_id,name));
CREATE TABLE rooms(id INTEGER PRIMARY KEY,location_id INTEGER NOT NULL REFERENCES locations(id),name TEXT NOT NULL,floor INTEGER NOT NULL DEFAULT 0,outdoors INTEGER NOT NULL DEFAULT 0,UNIQUE(location_id,name,floor));
CREATE TABLE spots(id INTEGER PRIMARY KEY,room_id INTEGER NOT NULL REFERENCES rooms(id),name TEXT NOT NULL,UNIQUE(room_id,name));
CREATE TABLE readings(id INTEGER PRIMARY KEY,ts TEXT NOT NULL,spot_id INTEGER NOT NULL REFERENCES spots(id),ssid_id INTEGER REFERENCES ssids(id),bssid TEXT,rssi INTEGER,noise INTEGER,snr INTEGER,channel TEXT,phy TEXT,tx_rate TEXT,ping_ms REAL,down_mbps REAL,up_mbps REAL,server TEXT,note TEXT);
INSERT INTO locations VALUES (1,'home');
INSERT INTO ssids VALUES (1,1,'home-net');
INSERT INTO rooms VALUES (1,1,'office',0,0);
INSERT INTO spots VALUES (1,1,'desk');
INSERT INTO readings(id,ts,spot_id,ssid_id,rssi) VALUES (1,'2026-09-21T10:00:00Z',1,1,-61);
PRAGMA user_version=1;
"#;
