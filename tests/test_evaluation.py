"""Evaluation data-source and analysis tests."""
from __future__ import annotations

import csv

import pytest

from wifimap import evaluation
from wifimap import store


def _csv_row(**overrides):
    row = {field: "" for field in evaluation.REQUIRED_FIELDS}
    row.update({
        "id": "1",
        "ts": "2026-09-21T10:00:00+00:00",
        "spot_id": "3",
        "room_id": "2",
        "location_id": "1",
        "location_name": "home",
        "room_name": "kitchen",
        "spot_name": "window",
        "floor": "0",
        "outdoors": "0",
        "ssid": "home-net",
        "rssi": "-61",
        "noise": "-92",
        "snr": "31",
        "ping_ms": "18.5",
        "down_mbps": "220.4",
        "up_mbps": "35.2",
        "delta_rssi": "-11",
        "delta_snr": "-9",
        "delta_down_mbps": "-59.6",
        "delta_up_mbps": "0.2",
    })
    row.update({key: str(value) for key, value in overrides.items()})
    return row


def _write_csv(path, rows, fields=None):
    fieldnames = list(fields or evaluation.REQUIRED_FIELDS)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_load_csv_parses_current_delta_schema(tmp_path):
    path = tmp_path / "report.csv"
    _write_csv(path, [_csv_row()])

    report = evaluation.load_csv(path)

    assert report.source == str(path)
    assert len(report.readings) == 1
    reading = report.readings[0]
    assert reading.location_name == "home"
    assert reading.outdoors is False
    assert reading.rssi == -61.0
    assert reading.delta_down_mbps == -59.6


def test_load_csv_requires_all_delta_columns(tmp_path):
    path = tmp_path / "old.csv"
    fields = [f for f in evaluation.REQUIRED_FIELDS if f != "delta_rssi"]
    _write_csv(path, [], fields=fields)

    with pytest.raises(evaluation.ReportError, match="delta_rssi"):
        evaluation.load_csv(path)


def test_load_csv_reports_invalid_utf8_as_source_error(tmp_path):
    path = tmp_path / "broken.csv"
    path.write_bytes(b"id,ts\n\xff")

    with pytest.raises(evaluation.ReportError, match="cannot read CSV"):
        evaluation.load_csv(path)


def test_load_csv_reports_bad_number_with_row(tmp_path):
    path = tmp_path / "bad.csv"
    _write_csv(path, [_csv_row(rssi="loud")])

    with pytest.raises(evaluation.ReportError, match=r"row 2.*rssi"):
        evaluation.load_csv(path)


@pytest.mark.parametrize("bad_value", ["nan", "inf", "-inf"])
def test_load_csv_rejects_non_finite_numbers(tmp_path, bad_value):
    path = tmp_path / "bad.csv"
    _write_csv(path, [_csv_row(down_mbps=bad_value)])

    with pytest.raises(evaluation.ReportError, match=r"row 2.*down_mbps"):
        evaluation.load_csv(path)


def test_load_db_reads_joined_rows_and_benchmark_deltas(tmp_path):
    path = tmp_path / "wifi.db"
    conn = store.get_db(path)
    location_id = store.create_location(conn, "home")
    room_id = store.create_room(conn, location_id, "kitchen")
    spot_id = store.create_spot(conn, room_id, "window")
    store.set_benchmark(
        conn, location_id, rssi=-50, snr=40,
        down_mbps=280.0, up_mbps=35.0,
    )
    store.add_reading(
        conn, spot_id, ssid="home-net", rssi=-61, noise=-92, snr=31,
        ping_ms=18.5, down_mbps=220.4, up_mbps=35.2,
    )
    conn.close()

    report = evaluation.load_db(path)

    assert len(report.readings) == 1
    reading = report.readings[0]
    assert reading.room_name == "kitchen"
    assert reading.delta_rssi == -11.0
    assert reading.delta_down_mbps == -59.6


def test_load_db_does_not_create_a_missing_database(tmp_path):
    path = tmp_path / "missing.db"

    with pytest.raises(evaluation.ReportError, match="does not exist"):
        evaluation.load_db(path)

    assert not path.exists()


def test_scope_options_offer_locations_and_ssids_with_counts():
    readings = [
        evaluation.Reading.from_mapping(_csv_row(id=1)),
        evaluation.Reading.from_mapping(
            _csv_row(id=2, room_name="office", ssid="work-net")),
        evaluation.Reading.from_mapping(
            _csv_row(id=3, location_name="away", ssid="")),
    ]

    locations = evaluation.scope_options(readings, "location")
    ssids = evaluation.scope_options(readings, "ssid")

    assert [(o.label, o.count) for o in locations] == [
        ("home", 2), ("away", 1),
    ]
    assert [(o.label, o.count) for o in ssids] == [
        ("home-net", 1), ("work-net", 1), ("Unknown", 1),
    ]
    assert len(evaluation.filter_scope(readings, ssids[-1])) == 1


def test_analysis_rows_group_repeated_spots_by_median():
    readings = [
        evaluation.Reading.from_mapping(
            _csv_row(id=1, rssi=-70, down_mbps=100, delta_rssi=-20)),
        evaluation.Reading.from_mapping(
            _csv_row(id=2, rssi=-50, down_mbps=200, delta_rssi=0)),
        evaluation.Reading.from_mapping(
            _csv_row(id=3, spot_name="table", rssi=-80,
                     down_mbps="", delta_rssi=-30)),
    ]

    rows = evaluation.analysis_rows(readings, grouped=True)

    assert len(rows) == 2
    window = next(row for row in rows if row.spot_name == "window")
    assert window.count == 2
    assert window.rssi == -60.0
    assert window.down_mbps == 150.0
    assert window.delta_rssi == -10.0


def test_analysis_rows_preserve_radio_details_and_mark_mixed_values():
    readings = [
        evaluation.Reading.from_mapping(_csv_row(
            id=1, bssid="aa:aa", channel="36 (160MHz)", phy="802.11ac",
            tx_rate=100, server="fast (1)", note="first")),
        evaluation.Reading.from_mapping(_csv_row(
            id=2, bssid="bb:bb", channel="1 (20MHz)", phy="802.11n",
            tx_rate=200, server="near (2)", note="second")),
    ]

    row = evaluation.analysis_rows(readings, grouped=True)[0]

    assert row.bssid == "Mixed"
    assert row.channel == "Mixed"
    assert row.phy == "Mixed"
    assert row.tx_rate == 150.0
    assert row.server == "Mixed"
    assert row.note == "Mixed"


def test_analysis_rows_raw_keeps_individual_readings():
    readings = [
        evaluation.Reading.from_mapping(_csv_row(id=1, rssi=-70)),
        evaluation.Reading.from_mapping(_csv_row(id=2, rssi=-50)),
    ]

    rows = evaluation.analysis_rows(readings, grouped=False)

    assert len(rows) == 2
    assert [row.count for row in rows] == [1, 1]
    assert [row.rssi for row in rows] == [-70.0, -50.0]


def test_rank_rows_defaults_to_worst_first_and_keeps_missing_last():
    readings = [
        evaluation.Reading.from_mapping(
            _csv_row(id=1, spot_name="weak", rssi=-80, ping_ms=50)),
        evaluation.Reading.from_mapping(
            _csv_row(id=2, spot_name="strong", rssi=-50, ping_ms=10)),
        evaluation.Reading.from_mapping(
            _csv_row(id=3, spot_name="missing", rssi="", ping_ms="")),
    ]
    rows = evaluation.analysis_rows(readings, grouped=False)

    by_rssi = evaluation.rank_rows(rows, "rssi")
    by_ping = evaluation.rank_rows(rows, "ping_ms")
    reversed_rssi = evaluation.rank_rows(rows, "rssi", reverse=True)

    assert [row.spot_name for row in by_rssi] == [
        "weak", "strong", "missing"]
    assert [row.spot_name for row in by_ping] == [
        "weak", "strong", "missing"]
    assert [row.spot_name for row in reversed_rssi] == [
        "strong", "weak", "missing"]


def test_metric_summary_counts_missing_and_uses_median():
    readings = [
        evaluation.Reading.from_mapping(_csv_row(id=1, rssi=-80)),
        evaluation.Reading.from_mapping(_csv_row(id=2, rssi=-60)),
        evaluation.Reading.from_mapping(_csv_row(id=3, rssi="")),
    ]
    rows = evaluation.analysis_rows(readings, grouped=False)

    summary = evaluation.metric_summary(rows, "rssi")

    assert summary.available == 2
    assert summary.missing == 1
    assert summary.minimum == -80.0
    assert summary.median == -70.0
    assert summary.maximum == -60.0


def test_unknown_metric_is_rejected():
    with pytest.raises(ValueError, match="unknown metric"):
        evaluation.rank_rows([], "quality_score")
