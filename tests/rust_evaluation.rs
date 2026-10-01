use wifimap::evaluation::{
    analysis_rows, compare_walks, comparison_direction, comparison_summary, export_csv, load_csv,
    metric_summary, rank_rows,
};
use wifimap::model::{Reading, Signal, Speed};

fn row(spot: &str, rssi: Option<f64>, down: Option<f64>) -> Reading {
    Reading {
        location: "home".into(),
        location_id: 1,
        room: "office".into(),
        spot: spot.into(),
        ts: "2026-09-21T10:00:00Z".into(),
        signal: Signal {
            rssi,
            ..Default::default()
        },
        speed: Speed {
            down_mbps: down,
            ..Default::default()
        },
        ..Default::default()
    }
}

#[test]
fn csv_roundtrip_keeps_optional_path_metrics_and_nulls() {
    let temp = tempfile::NamedTempFile::new().unwrap();
    let mut r = row("desk", Some(-61.0), None);
    r.delta_rssi = Some(-11.0);
    r.path.gateway_loss_pct = Some(0.0);
    r.path.internet_max_outage_ms = Some(1000);
    r.note = Some("comma, quoted \"note\"\nnext line".into());
    r.walk_id = Some(7);
    r.walk_name = Some("before".into());
    export_csv(temp.path(), &[r.clone()]).unwrap();
    assert_eq!(load_csv(temp.path()).unwrap(), vec![r]);
}

#[test]
fn fractional_radio_values_roundtrip_and_aggregate_without_rounding() {
    let mut first = row("desk", Some(-61.25), None);
    first.signal.noise = Some(-92.75);
    first.signal.snr = Some(31.5);
    let mut second = first.clone();
    second.signal.rssi = Some(-62.0);
    let file = tempfile::NamedTempFile::new().unwrap();
    export_csv(file.path(), &[first.clone(), second.clone()]).unwrap();
    assert_eq!(
        load_csv(file.path()).unwrap(),
        vec![first.clone(), second.clone()]
    );
    let grouped = analysis_rows(&[first, second], true);
    assert_eq!(grouped[0].metric("rssi"), Some(-61.625));
    assert_eq!(grouped[0].reading.signal.rssi, Some(-61.625));
    assert_eq!(grouped[0].reading.signal.noise, Some(-92.75));
}

#[test]
fn grouped_metrics_include_mixed_measurement_types_and_missing_counts() {
    let readings = vec![
        row("desk", Some(-70.0), None),
        row("desk", Some(-50.0), Some(200.0)),
        row("table", Some(-80.0), None),
    ];
    let rows = analysis_rows(&readings, true);
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0].metric("rssi"), Some(-60.0));
    assert_eq!(rows[0].metric("down_mbps"), Some(200.0));
    assert_eq!(rows[0].available("down_mbps"), 1);
    assert_eq!(rows[0].confidence("down_mbps"), "limited");
    let ranked = rank_rows(&rows, "down_mbps", true).unwrap();
    assert_eq!(ranked[0].reading.spot, "desk");
    assert_eq!(ranked[1].reading.spot, "table");
    let summary = metric_summary(&rows, "down_mbps").unwrap();
    assert_eq!(summary.available, 1);
    assert_eq!(summary.missing, 1);
    assert_eq!(summary.median, Some(200.0));
}

#[test]
fn comparison_pairs_spots_by_identity_and_counts_missing_coverage() {
    let mut a = row("desk", Some(-80.0), None);
    a.spot_id = 3;
    a.walk_id = Some(1);
    a.walk_name = Some("before".into());
    let mut b = row("renamed desk", Some(-50.0), None);
    b.spot_id = 3;
    b.walk_id = Some(2);
    b.walk_name = Some("after".into());
    let mut missing = row("missing", Some(-90.0), None);
    missing.spot_id = 4;
    missing.walk_id = Some(1);
    let rows = compare_walks(&[a, b, missing], 1, 2).unwrap();
    let matched = rows.iter().find(|r| r.status == "matched").unwrap();
    assert_eq!(comparison_direction(matched, "rssi").unwrap(), "improved");
    assert_eq!(matched.confidence("rssi"), "limited");
    let summary = comparison_summary(&rows, "rssi").unwrap();
    assert_eq!(summary.before_only, 1);
    assert_eq!(summary.comparable, 1);
    assert_eq!(summary.median_delta, Some(30.0));
}

#[test]
fn csv_rejects_nonfinite_numbers_and_missing_required_headers() {
    let temp = tempfile::NamedTempFile::new().unwrap();
    export_csv(temp.path(), &[row("desk", Some(-61.0), Some(4.0))]).unwrap();
    let text = std::fs::read_to_string(temp.path()).unwrap();
    // Parse/write via csv to target a single metric field.
    let mut reader = csv::Reader::from_reader(text.as_bytes());
    let headers = reader.headers().unwrap().clone();
    let idx = headers.iter().position(|h| h == "down_mbps").unwrap();
    let mut values = reader
        .records()
        .next()
        .unwrap()
        .unwrap()
        .iter()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    values[idx] = "NaN".into();
    let mut writer = csv::Writer::from_path(temp.path()).unwrap();
    writer.write_record(&headers).unwrap();
    writer.write_record(values).unwrap();
    writer.flush().unwrap();
    let error = load_csv(temp.path()).unwrap_err().to_string();
    assert!(error.contains("row 2") && error.contains("down_mbps"));
    std::fs::write(temp.path(), "id,ts\n").unwrap();
    assert!(
        load_csv(temp.path())
            .unwrap_err()
            .to_string()
            .contains("delta_rssi")
    );
}

#[test]
fn ranking_respects_metric_direction_and_keeps_missing_last_both_ways() {
    let mut weak = row("weak", Some(-80.0), None);
    weak.signal.noise = Some(-80.0);
    weak.signal.tx_rate = Some("100.5".into());
    weak.speed.ping_ms = Some(50.0);
    let mut strong = row("strong", Some(-50.0), None);
    strong.signal.noise = Some(-95.0);
    strong.signal.tx_rate = Some("500.5".into());
    strong.speed.ping_ms = Some(10.0);
    let missing = row("missing", None, None);
    let rows = analysis_rows(&[weak, strong, missing], false);
    for key in ["rssi", "noise", "tx_rate", "ping_ms"] {
        let ranked = rank_rows(&rows, key, false).unwrap();
        assert_eq!(
            ranked
                .iter()
                .map(|r| r.reading.spot.as_str())
                .collect::<Vec<_>>(),
            ["weak", "strong", "missing"]
        );
        let ranked = rank_rows(&rows, key, true).unwrap();
        assert_eq!(
            ranked
                .iter()
                .map(|r| r.reading.spot.as_str())
                .collect::<Vec<_>>(),
            ["strong", "weak", "missing"]
        );
    }
    assert!(rank_rows(&rows, "quality_score", false).is_err());
    let summary = metric_summary(&rows, "rssi").unwrap();
    assert_eq!(summary.available, 2);
    assert_eq!(summary.missing, 1);
    assert_eq!(summary.minimum, Some(-80.0));
    assert_eq!(summary.median, Some(-65.0));
    assert_eq!(summary.maximum, Some(-50.0));
}

#[test]
fn groups_mark_mixed_text_and_scope_options_preserve_encounter_order() {
    let mut first = row("desk", Some(-70.0), None);
    first.signal.ssid = Some("net".into());
    first.signal.bssid = Some("aa".into());
    first.note = Some("first".into());
    first.signal.tx_rate = Some("100".into());
    let mut second = row("desk", Some(-50.0), None);
    second.signal.ssid = Some("other-net".into());
    second.signal.bssid = Some("bb".into());
    second.note = Some("second".into());
    second.signal.tx_rate = Some("200".into());
    let mut away = row("chair", None, None);
    away.location = "away".into();
    let readings = vec![first, second, away];
    let rows = analysis_rows(&readings, true);
    assert_eq!(rows[0].reading.signal.ssid.as_deref(), Some("Mixed"));
    assert_eq!(rows[0].reading.signal.bssid.as_deref(), Some("Mixed"));
    assert_eq!(rows[0].reading.note.as_deref(), Some("Mixed"));
    assert_eq!(rows[0].metric("tx_rate"), Some(150.0));
    let options = wifimap::evaluation::scope_options(&readings, "location").unwrap();
    assert_eq!(
        options
            .iter()
            .map(|o| (o.label.as_str(), o.count))
            .collect::<Vec<_>>(),
        [("home", 2), ("away", 1)]
    );
    let options = wifimap::evaluation::scope_options(&readings, "ssid").unwrap();
    assert_eq!(options[2].label, "Unknown");
    assert_eq!(
        wifimap::evaluation::filter_scope(&readings, &options[2])
            .unwrap()
            .len(),
        1
    );
}

#[test]
fn comparisons_preserve_coverage_exceptions_and_pair_only_available_metrics() {
    use wifimap::evaluation::{comparison_delta, rank_comparison_rows, walk_options};
    let make = |walk: i64, spot: &str, noise: Option<f64>| {
        let mut r = row(spot, None, None);
        r.walk_id = Some(walk);
        r.walk_name = Some(format!("walk {walk}"));
        r.walk_started_at = Some(format!("2026-09-21T0{walk}:00:00Z"));
        r.signal.noise = noise;
        r
    };
    let readings = vec![
        make(1, "improved", Some(-90.0)),
        make(2, "improved", Some(-95.0)),
        make(1, "worse", Some(-95.0)),
        make(2, "worse", Some(-85.0)),
        make(1, "old-only", Some(-90.0)),
        make(2, "new-only", Some(-90.0)),
        make(1, "partial", Some(-90.0)),
        make(2, "partial", None),
    ];
    let walks = walk_options(&readings).unwrap();
    assert_eq!(walks[0].walk_id, 2);
    assert_eq!(walks[1].spot_count, 4);
    let rows = compare_walks(&readings, 1, 2).unwrap();
    let summary = comparison_summary(&rows, "noise").unwrap();
    assert_eq!(
        (
            summary.before_spots,
            summary.after_spots,
            summary.matched_spots,
            summary.before_only,
            summary.after_only,
            summary.comparable,
            summary.improved,
            summary.worse,
            summary.metric_missing
        ),
        (4, 4, 3, 1, 1, 2, 1, 1, 1)
    );
    let improved = rows
        .iter()
        .find(|r| r.place.spot_name == "improved")
        .unwrap();
    assert_eq!(comparison_delta(improved, "noise").unwrap(), Some(-5.0));
    let ranked = rank_comparison_rows(&rows, "noise", false).unwrap();
    assert_eq!(
        ranked
            .iter()
            .map(|r| r.place.spot_name.as_str())
            .collect::<Vec<_>>(),
        ["old-only", "worse", "improved", "new-only", "partial"]
    );
    assert!(compare_walks(&readings, 1, 1).is_err());
    assert!(compare_walks(&readings, 1, 3).is_err());
    let mut foreign = readings.clone();
    foreign[1].location_id = 2;
    foreign[1].location = "away".into();
    assert!(walk_options(&foreign).is_err());
    assert!(compare_walks(&foreign, 1, 2).is_err());
}

#[test]
fn path_zero_loss_is_rankable_and_partial_samples_stay_visible() {
    let mut first = row("desk", None, None);
    first.path.gateway_loss_pct = Some(0.0);
    let mut second = row("hall", None, None);
    second.path.gateway_loss_pct = Some(20.0);
    let third = row("unknown", None, None);
    let rows = analysis_rows(&[first, second, third], true);
    let ranked = rank_rows(&rows, "gateway_loss_pct", false).unwrap();
    assert_eq!(ranked[0].reading.spot, "hall");
    assert_eq!(ranked[1].metric("gateway_loss_pct"), Some(0.0));
    assert_eq!(ranked[2].metric("gateway_loss_pct"), None);
}

#[test]
fn database_evaluation_rejects_nonfinite_metrics_with_row_and_field() {
    let file = tempfile::NamedTempFile::new().unwrap();
    let store = wifimap::store::Store::open(file.path()).unwrap();
    let location = store.create_location("home").unwrap();
    let room = store.create_room(location, "office", 0, false).unwrap();
    let spot = store.create_spot(room, "desk").unwrap();
    store
        .add_reading(&Reading {
            spot_id: spot,
            ..Default::default()
        })
        .unwrap();
    let connection = rusqlite::Connection::open(file.path()).unwrap();
    connection
        .execute("UPDATE readings SET down_mbps=?", [f64::INFINITY])
        .unwrap();
    let error = wifimap::evaluation::load_db(file.path())
        .unwrap_err()
        .to_string();
    assert!(error.contains("row 1") && error.contains("down_mbps"));
}
