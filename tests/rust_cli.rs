use rusqlite::Connection;
use std::{
    path::Path,
    process::{Command, Output},
};
use tempfile::TempDir;

fn cli(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_wifimap"))
        .args(args)
        .output()
        .unwrap()
}
fn database(temp: &TempDir) -> std::path::PathBuf {
    let path = temp.path().join("survey.db");
    let conn = Connection::open(&path).unwrap();
    // Python's dump interleaves inserts and schema in alphabetical table order.
    conn.pragma_update(None, "foreign_keys", false).unwrap();
    conn.execute_batch(include_str!("fixtures/python-v4.sql"))
        .unwrap();
    conn.pragma_update(None, "foreign_keys", true).unwrap();
    assert!(
        conn.prepare("PRAGMA foreign_key_check")
            .unwrap()
            .query([])
            .unwrap()
            .next()
            .unwrap()
            .is_none()
    );
    path
}
fn successful(args: &[&str]) -> String {
    let result = cli(args);
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    String::from_utf8(result.stdout).unwrap()
}

#[test]
fn opens_python_database_and_exports_compatible_csv() {
    let temp = TempDir::new().unwrap();
    let path = database(&temp);
    let db = path.to_str().unwrap();
    let csv = temp.path().join("out.csv");
    let history = successful(&["--db", db, "list", "--ssid", "HOME-WIFI"]);
    assert!(history.contains("KITCHEN") && history.contains("-67") && history.contains("café"));
    successful(&["--db", db, "export", "--csv", csv.to_str().unwrap()]);
    let mut actual = csv::Reader::from_path(&csv).unwrap();
    let mut expected =
        csv::Reader::from_reader(include_bytes!("fixtures/python-v4.csv").as_slice());
    assert_eq!(actual.headers().unwrap(), expected.headers().unwrap());
    let actual = actual.records().next().unwrap().unwrap();
    let expected = expected.records().next().unwrap().unwrap();
    for (index, (a, b)) in actual.iter().zip(expected.iter()).enumerate() {
        match (a.parse::<f64>(), b.parse::<f64>()) {
            (Ok(a), Ok(b)) => assert!((a - b).abs() < 1e-8, "column {index}: {a} != {b}"),
            _ => assert_eq!(a, b, "column {index}"),
        }
    }
    let conn = Connection::open(path).unwrap();
    assert_eq!(
        conn.query_row("SELECT COUNT(*) FROM readings", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        1
    );
    assert_eq!(
        conn.query_row("SELECT name FROM walks WHERE id=1", [], |r| r
            .get::<_, String>(0))
            .unwrap(),
        "before"
    );
}

#[test]
fn place_management_scopes_ids_and_rejects_unknown_readonly_lookups() {
    let temp = TempDir::new().unwrap();
    let path = temp.path().join("new/survey.db");
    let db = path.to_str().unwrap();
    successful(&["--db", db, "locations", "add", "--name", "HOME"]);
    successful(&[
        "--db",
        db,
        "rooms",
        "add",
        "--location",
        "HOME",
        "--name",
        "CELLAR",
        "--floor",
        "-1",
    ]);
    successful(&[
        "--db",
        db,
        "spots",
        "add",
        "--location",
        "HOME",
        "--room",
        "CELLAR",
        "--name",
        "DESK",
    ]);
    let rooms = successful(&["--db", db, "rooms", "list", "--location", "HOME"]);
    assert!(rooms.contains("CELLAR") && rooms.contains("-1"));
    let missing = cli(&["--db", db, "rooms", "list", "--location", "UNKNOWN"]);
    assert_eq!(missing.status.code(), Some(3));
    assert!(!successful(&["--db", db, "locations"]).contains("UNKNOWN"));
}

#[test]
fn access_point_names_are_normalized_and_persist() {
    let temp = TempDir::new().unwrap();
    let path = temp.path().join("survey.db");
    let db = path.to_str().unwrap();
    let output = successful(&[
        "--db",
        db,
        "aps",
        "name",
        "--bssid",
        "AA-BB-CC-DD-EE-FF",
        "--name",
        "Kitchen",
    ]);
    assert!(output.contains("aa:bb:cc:dd:ee:ff"));
    assert!(successful(&["--db", db, "aps", "list"]).contains("Kitchen"));
    assert_eq!(
        cli(&[
            "--db", db, "aps", "name", "--bssid", "invalid", "--name", "AP"
        ])
        .status
        .code(),
        Some(3)
    );
}

#[test]
fn benchmark_show_and_force_clear_preserve_readings() {
    let temp = TempDir::new().unwrap();
    let path = database(&temp);
    let db = path.to_str().unwrap();
    assert!(successful(&["--db", db, "benchmark", "show", "--location", "HOME"]).contains("-40"));
    let result = cli(&["--db", db, "benchmark", "clear", "--location", "HOME"]);
    assert_eq!(result.status.code(), Some(3));
    successful(&[
        "--db",
        db,
        "benchmark",
        "clear",
        "--location",
        "HOME",
        "--force",
    ]);
    assert_eq!(
        cli(&["--db", db, "benchmark", "show", "--location", "HOME"])
            .status
            .code(),
        Some(3)
    );
    assert!(successful(&["--db", db, "list"]).contains("-67"));
}

#[test]
fn demo_scan_is_explicit_and_never_opens_requested_database() {
    let temp = TempDir::new().unwrap();
    let path = temp.path().join("must-not-exist.db");
    let output = successful(&[
        "--demo",
        "--db",
        path.to_str().unwrap(),
        "scan",
        "--location",
        "HOME",
        "--room",
        "KITCHEN",
        "--spot",
        "WINDOW",
        "--ssid",
        "DEMO-WIFI",
        "--no-speedtest",
    ]);
    assert!(output.contains("DEMO / SIMULATED") && output.contains("#1"));
    assert!(!path.exists());
}

#[test]
fn noninteractive_eval_missing_database_does_not_create_it() {
    let temp = TempDir::new().unwrap();
    let path = temp.path().join("missing.db");
    let result = cli(&["--db", path.to_str().unwrap(), "eval"]);
    assert_eq!(result.status.code(), Some(3));
    assert!(!Path::new(&path).exists());
}

#[test]
fn malformed_csv_fails_cleanly_and_help_is_available_without_wifi() {
    assert!(successful(&["--help"]).contains("scan"));
    let temp = TempDir::new().unwrap();
    let csv = temp.path().join("bad.csv");
    std::fs::write(&csv, "id,ts\n1,now\n").unwrap();
    assert_eq!(
        cli(&["eval", "--csv", csv.to_str().unwrap()]).status.code(),
        Some(3)
    );
}

#[test]
fn history_ssid_filters_are_trimmed_and_blank_values_fail() {
    let temp = TempDir::new().unwrap();
    let path = database(&temp);
    let db = path.to_str().unwrap();
    assert!(successful(&["--db", db, "list", "--ssid", "  HOME-WIFI  "]).contains("-67"));
    assert_eq!(
        cli(&["--db", db, "list", "--ssid", "   "]).status.code(),
        Some(3)
    );
    let out = temp.path().join("out.csv");
    assert_eq!(
        cli(&[
            "--db",
            db,
            "export",
            "--ssid",
            "   ",
            "--csv",
            out.to_str().unwrap()
        ])
        .status
        .code(),
        Some(3)
    );
    assert!(!out.exists());
}

#[test]
fn walk_rejects_blank_names_before_opening_database() {
    let temp = TempDir::new().unwrap();
    let path = temp.path().join("missing.db");
    for flag in ["--ssid", "--name", "--compare-to"] {
        assert_eq!(
            cli(&["--db", path.to_str().unwrap(), "walk", flag, "  "])
                .status
                .code(),
            Some(3)
        );
    }
    assert!(!path.exists());
}
