"""CLI tests (M3): scan/locations/list/export. No live network."""
from __future__ import annotations

import csv
import sqlite3

import pytest

from wifimap import signal as signal_mod
from wifimap import speed as speed_mod
from wifimap import store as store_mod
from wifimap import cli as cli_mod
from wifimap.cli import main


def _run(capsys, *argv):
    rc = main(list(argv))
    out = capsys.readouterr()
    return rc, out.out, out.err


def _db_path(monkeypatch, tmp_path):
    return str(tmp_path / "cli.db")


def _seed_3level(conn, loc="home", room="kitchen", spot="window",
                 floor=0, outdoors=False, **reading_kw):
    lid = store_mod.create_location(conn, loc)
    rid = store_mod.create_room(conn, lid, room, floor=floor,
                                outdoors=outdoors)
    sid = store_mod.create_spot(conn, rid, spot)
    store_mod.add_reading(conn, sid, **reading_kw)
    return sid


def test_scan_insert_path(monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    monkeypatch.setattr(
        signal_mod, "read_signal",
        lambda timeout=2.0: signal_mod.Signal(
            ssid="home", bssid="aa:bb", rssi=-55, noise=-95,
            snr=40, channel="36", phy="802.11ax", tx_rate="1200"),
    )
    monkeypatch.setattr(
        speed_mod, "run_speedtest",
        lambda timeout=120.0: speed_mod.Speed(
            ping_ms=10.0, down_mbps=90.0, up_mbps=12.0, server="T (1)"),
    )
    rc, out, err = _run(capsys, "--db", db, "scan",
                        "--location", "home", "--room", "kitchen",
                        "--spot", "window", "--no-speedtest")
    assert rc == 0
    assert "kitchen" in out
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert len(rows) == 1
        assert rows[0]["location_name"] == "home"
        assert rows[0]["room_name"] == "kitchen"
        assert rows[0]["spot_name"] == "window"
        assert rows[0]["rssi"] == -55
    finally:
        conn.close()


def test_scan_no_wifi_exit_2(monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    def _boom(timeout=2.0):
        raise signal_mod.NoWiFiError("off")
    monkeypatch.setattr(signal_mod, "read_signal", _boom)
    rc, out, err = _run(capsys, "--db", db, "scan",
                        "--location", "home", "--room", "kitchen",
                        "--spot", "window", "--no-speedtest")
    assert rc == 2
    assert err.strip() != ""


def test_scan_signal_unavailable_exit_2(monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    def _boom(timeout=2.0):
        raise signal_mod.SignalUnavailableError("no backend")
    monkeypatch.setattr(signal_mod, "read_signal", _boom)
    rc, out, err = _run(capsys, "--db", db, "scan",
                        "--location", "home", "--room", "kitchen",
                        "--spot", "window", "--no-speedtest")
    assert rc == 2
    assert "PyObjC" in err or "backend" in err or "hint" in err.lower()


def test_scan_speedtest_fail_stores_nulls(monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    monkeypatch.setattr(
        signal_mod, "read_signal",
        lambda timeout=2.0: signal_mod.Signal(ssid="h", rssi=-60),
    )
    def _fail(timeout=120.0):
        raise speed_mod.SpeedtestFailedError("timeout")
    monkeypatch.setattr(speed_mod, "run_speedtest", _fail)
    rc, out, err = _run(capsys, "--db", db, "scan",
                        "--location", "home", "--room", "den",
                        "--spot", "window")
    assert rc == 0
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert len(rows) == 1
        assert rows[0]["down_mbps"] is None
        assert rows[0]["up_mbps"] is None
        assert rows[0]["server"] == "ERROR"
    finally:
        conn.close()


def test_scan_speedtest_unavailable_warns_and_keeps_signal(
        monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    monkeypatch.setattr(
        signal_mod, "read_signal",
        lambda timeout=2.0: signal_mod.Signal(ssid="h", rssi=-61),
    )
    def _missing(timeout=120.0):
        raise speed_mod.SpeedtestUnavailableError("no binary")
    monkeypatch.setattr(speed_mod, "run_speedtest", _missing)
    rc, out, err = _run(capsys, "--db", db, "scan",
                        "--location", "home", "--room", "den2",
                        "--spot", "window")
    assert rc == 0
    assert "speedtest" in err.lower() or "warn" in err.lower()
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert rows[0]["rssi"] == -61
        assert rows[0]["down_mbps"] is None
    finally:
        conn.close()


def test_scan_resolve_autocreate_with_flags(monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    monkeypatch.setattr(
        signal_mod, "read_signal",
        lambda timeout=2.0: signal_mod.Signal(rssi=-70),
    )
    rc, out, err = _run(
        capsys, "--db", db, "scan", "--location", "home",
        "--room", "attic", "--spot", "window",
        "--room-floor", "1", "--room-outdoors", "1",
        "--no-speedtest")
    assert rc == 0
    conn = store_mod.get_db(db)
    try:
        locs = {loc.name: loc for loc in store_mod.list_locations(conn)}
        assert "home" in locs
        rooms = store_mod.list_rooms(conn, location_id=locs["home"].id)
        by_name = {r.name: r for r in rooms}
        assert by_name["attic"].floor == 1
        assert by_name["attic"].outdoors is True
    finally:
        conn.close()


def test_locations_add_and_list(monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    rc, out, err = _run(capsys, "--db", db, "locations", "add",
                        "--name", "office")
    assert rc == 0
    assert out.strip().isdigit()
    rc, out, err = _run(capsys, "--db", db, "locations", "list")
    assert rc == 0
    assert "office" in out
    assert "id" in out.lower() and "name" in out.lower()


def test_list_filters(monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    conn = store_mod.get_db(db)
    try:
        _seed_3level(conn, loc="room-a", room="r", spot="s", floor=0,
                     rssi=-50, note="a1")
        _seed_3level(conn, loc="room-b", room="r", spot="s", floor=1,
                     rssi=-70, note="b1")
    finally:
        conn.close()
    rc, out, err = _run(capsys, "--db", db, "list")
    assert rc == 0
    assert "room-a" in out and "room-b" in out
    # newest first: room-b row before room-a row
    assert out.index("room-b") < out.index("room-a")
    rc, out, err = _run(capsys, "--db", db, "list",
                        "--location", "room-a")
    assert rc == 0
    assert "room-a" in out
    assert "room-b" not in out
    rc, out, err = _run(capsys, "--db", db, "list", "--floor", "1")
    assert rc == 0
    assert "room-b" in out
    assert "room-a" not in out
    rc, out, err = _run(capsys, "--db", db, "list", "--limit", "1")
    assert rc == 0
    assert "room-b" in out
    assert "room-a" not in out


def test_export_csv_content(monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    conn = store_mod.get_db(db)
    try:
        _seed_3level(conn, loc="kitchen", room="r", spot="s",
                     rssi=-55, ssid="home")
    finally:
        conn.close()
    csv_path = str(tmp_path / "out.csv")
    rc, out, err = _run(capsys, "--db", db, "export",
                        "--csv", csv_path)
    assert rc == 0
    with open(csv_path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["location_name"] == "kitchen"
    assert rows[0]["rssi"] == "-55"
    assert "floor" in rows[0] and "outdoors" in rows[0]


def test_scan_db_error_exit_3(monkeypatch, tmp_path, capsys):
    def _boom(path):
        raise sqlite3.Error("locked")
    monkeypatch.setattr(store_mod, "get_db", _boom)
    rc, out, err = _run(capsys, "--db", str(tmp_path / "x.db"),
                        "scan", "--location", "home", "--room", "k",
                        "--spot", "window", "--no-speedtest")
    assert rc == 3
    assert err.strip() != ""


def test_scan_unknown_id_exit_3(monkeypatch, tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    def _noid(conn, id_or_name, floor=0, outdoors=False):
        raise ValueError("unknown location id: 9999")
    monkeypatch.setattr(store_mod, "resolve_location", _noid)
    rc, out, err = _run(capsys, "--db", db, "scan",
                        "--location", "9999", "--room", "k",
                        "--spot", "window", "--no-speedtest")
    assert rc == 3
    assert err.strip() != ""


def test_scan_unwritable_db_exit_3(tmp_path, capsys):
    db = str(tmp_path / "nodir" / "x.db")
    rc, out, err = _run(capsys, "--db", db, "scan",
                        "--location", "home", "--room", "k",
                        "--spot", "window", "--no-speedtest")
    assert rc == 3
    assert err.strip() != ""


def test_export_bad_path_exit_3(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    conn = store_mod.get_db(db)
    try:
        _seed_3level(conn, loc="k", room="r", spot="s", rssi=-55)
    finally:
        conn.close()
    rc, out, err = _run(capsys, "--db", db, "export",
                        "--csv", str(tmp_path / "nodir" / "o.csv"))
    assert rc == 3
    assert err.strip() != ""


def test_list_nulls_show_dash(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    conn = store_mod.get_db(db)
    try:
        _seed_3level(conn, loc="den", room="r", spot="s", rssi=-60)
    finally:
        conn.close()
    rc, out, err = _run(capsys, "--db", db, "list")
    assert rc == 0
    assert "None" not in out
    assert "-" in out


def test_walk_ssid_flag_parses():
    args = cli_mod._build_parser().parse_args(["walk", "--ssid", "home-5g"])
    assert args.ssid == "home-5g"


def test_walk_blank_ssid_rejected(capsys, tmp_path):
    rc = cli_mod.main(["--db", str(tmp_path / "w.db"), "walk",
                       "--ssid", "  ", "--no-speedtest"])
    assert rc == cli_mod.EXIT_STORAGE


def test_list_ssid_filter_passes_through(tmp_path, monkeypatch, capsys):
    seen = {}

    def _fake(conn, location=None, floor=None, ssid=None, limit=50):
        seen["ssid"] = ssid
        return []

    monkeypatch.setattr(store_mod, "list_readings", _fake)
    rc = cli_mod.main(["--db", str(tmp_path / "w.db"), "list",
                       "--ssid", "home-5g"])
    assert rc == cli_mod.EXIT_OK
    assert seen["ssid"] == "home-5g"


def test_scan_location_room_spot(monkeypatch, tmp_path):
    from wifimap import signal as signal_mod, store as store_mod
    from wifimap.cli import main
    db = str(tmp_path / "cli.db")
    monkeypatch.setattr(signal_mod, "read_signal",
        lambda timeout=2.0: signal_mod.Signal(ssid="h", rssi=-55))
    rc = main(["--db", db, "scan", "--location", "home",
               "--room", "kitchen", "--spot", "window", "--no-speedtest"])
    assert rc == 0
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert rows[0]["location_name"] == "home"
        assert rows[0]["room_name"] == "kitchen"
        assert rows[0]["spot_name"] == "window"
    finally:
        conn.close()
