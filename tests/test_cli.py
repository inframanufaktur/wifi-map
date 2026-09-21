"""CLI tests (M3): scan/locations/list/export. No live network."""
from __future__ import annotations

import csv
import sqlite3

import pytest

from wifimap import signal as signal_mod
from wifimap import speed as speed_mod
from wifimap import store as store_mod
from wifimap import evaluation as evaluation_mod
from wifimap import eval_tui as eval_tui_mod
from wifimap import cli as cli_mod
from wifimap.cli import main


def _run(capsys, *argv):
    rc = main(list(argv))
    out = capsys.readouterr()
    return rc, out.out, out.err


@pytest.fixture(autouse=True)
def _no_countdown_sleep(monkeypatch):
    """Kill real sleeps from the scan sampling countdown (cli tests)."""
    monkeypatch.setattr(cli_mod.time, "sleep", lambda s: None)


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


def test_eval_uses_selected_database(monkeypatch, tmp_path):
    db = str(tmp_path / "readings.db")
    report = evaluation_mod.Report(source=db, readings=())
    seen = []
    monkeypatch.setattr(evaluation_mod, "load_db", lambda path: (
        seen.append(("db", str(path))) or report))
    monkeypatch.setattr(eval_tui_mod, "run_eval", lambda value: (
        seen.append(("tui", value.source)) or 0))

    rc = main(["--db", db, "eval"])

    assert rc == 0
    assert seen == [("db", db), ("tui", db)]


def test_eval_csv_overrides_database_source(monkeypatch, tmp_path):
    csv_path = str(tmp_path / "report.csv")
    report = evaluation_mod.Report(source=csv_path, readings=())
    seen = []
    monkeypatch.setattr(evaluation_mod, "load_csv", lambda path: (
        seen.append(("csv", str(path))) or report))
    monkeypatch.setattr(evaluation_mod, "load_db", lambda path: (
        pytest.fail("database loader must not run with --csv")))
    monkeypatch.setattr(eval_tui_mod, "run_eval", lambda value: 0)

    rc = main(["eval", "--csv", csv_path])

    assert rc == 0
    assert seen == [("csv", csv_path)]


def test_eval_defaults_to_project_database():
    args = cli_mod._build_parser().parse_args(["eval"])

    assert args.db == str(cli_mod._PROJECT_ROOT / "db" / "wifi-map.db")


def test_eval_source_error_exits_3(monkeypatch, tmp_path, capsys):
    db = str(tmp_path / "missing.db")
    monkeypatch.setattr(
        evaluation_mod, "load_db",
        lambda path: (_ for _ in ()).throw(
            evaluation_mod.ReportError("database does not exist")),
    )
    monkeypatch.setattr(
        eval_tui_mod, "run_eval",
        lambda report: pytest.fail("TUI must not run for invalid source"),
    )

    rc = main(["--db", db, "eval"])

    assert rc == 3
    assert "database does not exist" in capsys.readouterr().err


def test_scan_insert_path(monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(
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
                        "--spot", "window", "--ssid", "home-net",
                        "--no-speedtest")
    assert rc == 0
    assert "kitchen" in out
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert len(rows) == 1
        assert rows[0]["location_name"] == "home"
        assert rows[0]["room_name"] == "kitchen"
        assert rows[0]["spot_name"] == "window"
        assert rows[0]["ssid"] == "home-net"
        assert rows[0]["bssid"] == "aa:bb"
        assert rows[0]["ssid_id"] is not None
        assert rows[0]["rssi"] == -55
    finally:
        conn.close()


def test_scan_ssid_flag_parses():
    args = cli_mod._build_parser().parse_args([
        "scan", "--location", "home", "--room", "kitchen",
        "--spot", "window", "--ssid", "home-net",
    ])

    assert args.ssid == "home-net"


def test_scan_requires_ssid_in_noninteractive_mode(
        monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    sampled = []
    monkeypatch.setattr(signal_mod, "read_network_identity", lambda: None)
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: sampled.append(True) or signal_mod.Signal(rssi=-55),
    )

    rc, out, err = _run(
        capsys, "--db", db, "scan", "--location", "home",
        "--room", "kitchen", "--spot", "window", "--no-speedtest",
    )

    assert rc == cli_mod.EXIT_STORAGE
    assert "--ssid" in err
    assert sampled == []


def test_scan_prints_sampling_notice(monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(rssi=-55, noise=-95, snr=40),
    )
    rc, out, err = _run(capsys, "--db", db, "scan",
                        "--location", "home", "--room", "kitchen",
                        "--spot", "window", "--ssid", "home-net",
                        "--no-speedtest")
    assert rc == 0
    assert "sampling 5s... 5" in err
    assert "sampling 5s... 1" in err


def test_scan_sampling_countdown_ticks(monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    slept = []
    monkeypatch.setattr(cli_mod.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(rssi=-55, noise=-95, snr=40),
    )
    rc, out, err = _run(capsys, "--db", db, "scan",
                        "--location", "home", "--room", "kitchen",
                        "--spot", "window", "--ssid", "home-net",
                        "--no-speedtest")
    assert rc == 0
    assert "sampling 5s... 5" in err
    assert "sampling 5s... 1" in err
    assert slept == [1.0] * 4


def test_scan_no_wifi_exit_2(monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    def _boom(*a, **k):
        raise signal_mod.NoWiFiError("off")
    monkeypatch.setattr(signal_mod, "sample_signal", _boom)
    rc, out, err = _run(capsys, "--db", db, "scan",
                        "--location", "home", "--room", "kitchen",
                        "--spot", "window", "--ssid", "home-net",
                        "--no-speedtest")
    assert rc == 2
    assert err.strip() != ""


def test_scan_signal_unavailable_exit_2(monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    def _boom(*a, **k):
        raise signal_mod.SignalUnavailableError("no backend")
    monkeypatch.setattr(signal_mod, "sample_signal", _boom)
    rc, out, err = _run(capsys, "--db", db, "scan",
                        "--location", "home", "--room", "kitchen",
                        "--spot", "window", "--ssid", "home-net",
                        "--no-speedtest")
    assert rc == 2
    assert "PyObjC" in err or "backend" in err or "hint" in err.lower()


def test_scan_speedtest_fail_stores_nulls(monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(ssid="h", rssi=-60),
    )
    def _fail(timeout=120.0):
        raise speed_mod.SpeedtestFailedError("timeout")
    monkeypatch.setattr(speed_mod, "run_speedtest", _fail)
    rc, out, err = _run(capsys, "--db", db, "scan",
                        "--location", "home", "--room", "den",
                        "--spot", "window", "--ssid", "home-net")
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
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(ssid="h", rssi=-61),
    )
    def _missing(timeout=120.0):
        raise speed_mod.SpeedtestUnavailableError("no binary")
    monkeypatch.setattr(speed_mod, "run_speedtest", _missing)
    rc, out, err = _run(capsys, "--db", db, "scan",
                        "--location", "home", "--room", "den2",
                        "--spot", "window", "--ssid", "home-net")
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
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(rssi=-70),
    )
    rc, out, err = _run(
        capsys, "--db", db, "scan", "--location", "home",
        "--room", "attic", "--spot", "window",
        "--ssid", "home-net",
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
                     rssi=-55, ssid="home",
                     bssid="aa:bb:cc:dd:ee:ff",
                     path_probe_count=60,
                     gateway_rtt_ms=3.0, gateway_p95_ms=6.0,
                     gateway_loss_pct=0.0,
                     internet_rtt_ms=18.0, internet_p95_ms=31.0,
                     internet_loss_pct=2.0,
                     internet_max_outage_ms=1000)
        store_mod.set_access_point_name(
            conn, "aa:bb:cc:dd:ee:ff", "Kitchen mesh")
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
    assert rows[0]["bssid"] == "aa:bb:cc:dd:ee:ff"
    assert rows[0]["ap_name"] == "Kitchen mesh"
    assert rows[0]["path_probe_count"] == "60"
    assert rows[0]["gateway_rtt_ms"] == "3.0"
    assert rows[0]["internet_loss_pct"] == "2.0"
    assert "floor" in rows[0] and "outdoors" in rows[0]


def test_aps_name_uses_current_access_point(monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    monkeypatch.setattr(
        signal_mod, "read_network_identity",
        lambda: ("home-net", "60:8d:26:8d:cf:3d"),
    )

    rc, out, err = _run(
        capsys, "--db", db, "aps", "name", "--name", "Office mesh")

    assert rc == 0
    conn = store_mod.get_db(db)
    try:
        assert store_mod.get_access_point_name(
            conn, "60:8d:26:8d:cf:3d") == "Office mesh"
    finally:
        conn.close()


def test_aps_current_prints_address_and_name(monkeypatch, tmp_path, capsys):
    db = _db_path(monkeypatch, tmp_path)
    conn = store_mod.get_db(db)
    try:
        store_mod.set_access_point_name(
            conn, "60:8d:26:8d:cf:3d", "Office mesh")
    finally:
        conn.close()
    monkeypatch.setattr(
        signal_mod, "read_network_identity",
        lambda: ("home-net", "60:8d:26:8d:cf:3d"),
    )

    rc, out, err = _run(capsys, "--db", db, "aps", "current")

    assert rc == 0
    assert out == "60:8d:26:8d:cf:3d  Office mesh\n"


def test_export_csv_includes_benchmark_delta(tmp_path, capsys):
    """Export rows carry deltas vs the location benchmark (blank if none)."""
    db = str(tmp_path / "cli.db")
    conn = store_mod.get_db(db)
    try:
        lid = store_mod.create_location(conn, "home")
        store_mod.set_benchmark(
            conn, lid, rssi=-50, snr=45,
            down_mbps=100.0, up_mbps=20.0)
        rid = store_mod.create_room(conn, lid, "kitchen")
        sid = store_mod.create_spot(conn, rid, "window")
        store_mod.add_reading(
            conn, sid, rssi=-55, snr=40,
            down_mbps=90.0, up_mbps=18.0)
        other = store_mod.create_location(conn, "away")
        rid2 = store_mod.create_room(conn, other, "garden")
        sid2 = store_mod.create_spot(conn, rid2, "bench")
        store_mod.add_reading(conn, sid2, rssi=-70, snr=30)
    finally:
        conn.close()
    csv_path = str(tmp_path / "out.csv")
    rc, out, err = _run(capsys, "--db", db, "export", "--csv", csv_path)
    assert rc == 0
    with open(csv_path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2
    by_loc = {r["location_name"]: r for r in rows}
    home = by_loc["home"]
    assert home["delta_rssi"] == "-5"
    assert home["delta_snr"] == "-5"
    assert home["delta_down_mbps"] == "-10.0"
    assert home["delta_up_mbps"] == "-2.0"
    away = by_loc["away"]
    assert away["delta_rssi"] == ""
    assert away["delta_snr"] == ""
    assert away["delta_down_mbps"] == ""
    assert away["delta_up_mbps"] == ""


def test_export_csv_includes_walk_metadata(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    conn = store_mod.get_db(db)
    try:
        location_id = store_mod.create_location(conn, "home")
        room_id = store_mod.create_room(conn, location_id, "kitchen")
        spot_id = store_mod.create_spot(conn, room_id, "window")
        walk_id = store_mod.create_walk(
            conn, location_id, "before-install",
            started_at="2026-09-21T08:00:00+00:00",
        )
        store_mod.finish_walk(
            conn, walk_id, ended_at="2026-09-21T08:30:00+00:00")
        store_mod.add_reading(conn, spot_id, rssi=-65, walk_id=walk_id)
    finally:
        conn.close()

    csv_path = str(tmp_path / "out.csv")
    rc, _, _ = _run(
        capsys, "--db", db, "export", "--csv", csv_path)

    assert rc == cli_mod.EXIT_OK
    with open(csv_path, newline="") as fh:
        row = next(csv.DictReader(fh))
    assert row["walk_id"] == str(walk_id)
    assert row["walk_name"] == "before-install"
    assert row["walk_started_at"] == "2026-09-21T08:00:00+00:00"
    assert row["walk_ended_at"] == "2026-09-21T08:30:00+00:00"


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


def test_walk_name_and_comparison_flags_parse():
    args = cli_mod._build_parser().parse_args([
        "walk", "--location", "schwarze-witwe",
        "--name", "after-mesh", "--compare-to", "before-install",
    ])

    assert args.name == "after-mesh"
    assert args.compare_to == "before-install"


def test_walk_forwards_session_options(monkeypatch, tmp_path):
    from wifimap import tui as tui_mod

    seen = {}

    def _run_walk(db_path, interval=1.0, location_preset=None,
                  no_speedtest=False, ssid=None, walk_name=None,
                  compare_to=None):
        seen.update({
            "db_path": db_path,
            "location": location_preset,
            "walk_name": walk_name,
            "compare_to": compare_to,
        })
        return 0

    monkeypatch.setattr(tui_mod, "run_walk", _run_walk)
    db = str(tmp_path / "wifi.db")

    rc = cli_mod.main([
        "--db", db, "walk", "--location", "schwarze-witwe",
        "--name", "after-mesh", "--compare-to", "before-install",
        "--no-speedtest",
    ])

    assert rc == 0
    assert seen == {
        "db_path": db,
        "location": "schwarze-witwe",
        "walk_name": "after-mesh",
        "compare_to": "before-install",
    }


@pytest.mark.parametrize("flag", ["--name", "--compare-to"])
def test_walk_rejects_blank_session_options(flag, capsys, tmp_path):
    rc = cli_mod.main([
        "--db", str(tmp_path / "w.db"), "walk", "--location", "home",
        flag, "  ", "--no-speedtest",
    ])

    assert rc == cli_mod.EXIT_STORAGE
    assert "%s must not be blank" % flag in capsys.readouterr().err


def test_walk_blank_ssid_rejected(capsys, tmp_path):
    rc = cli_mod.main(["--db", str(tmp_path / "w.db"), "walk",
                       "--ssid", "  ", "--no-speedtest"])
    assert rc == cli_mod.EXIT_STORAGE


def test_list_ssid_filter_passes_through(tmp_path, monkeypatch, capsys):
    seen = {}

    def _fake(conn, location=None, room=None, spot=None, floor=None,
              ssid=None, limit=50):
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
    monkeypatch.setattr(signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(ssid="h", rssi=-55))
    rc = main(["--db", db, "scan", "--location", "home",
               "--room", "kitchen", "--spot", "window",
               "--ssid", "home-net", "--no-speedtest"])
    assert rc == 0
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert rows[0]["location_name"] == "home"
        assert rows[0]["room_name"] == "kitchen"
        assert rows[0]["spot_name"] == "window"
    finally:
        conn.close()


def test_list_filter_room_spot(tmp_path):
    from wifimap.store import get_db, create_location, create_room, create_spot, add_reading, list_readings
    conn = get_db(str(tmp_path / "f.db"))
    try:
        home = create_location(conn, "home")
        k = create_room(conn, home, "kitchen")
        w = create_spot(conn, k, "window")
        b = create_spot(conn, k, "bed")
        add_reading(conn, w, rssi=-50)
        add_reading(conn, b, rssi=-70)
        assert len(list_readings(conn, spot="window")) == 1
        assert len(list_readings(conn, room="kitchen")) == 2
    finally:
        conn.close()


def test_list_cli_room_spot_flags(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    conn = store_mod.get_db(db)
    try:
        _seed_3level(conn, loc="home", room="kitchen", spot="window",
                     rssi=-50, note="w1")
        lid = store_mod.resolve_location(conn, "home")
        rid = store_mod.resolve_room(conn, lid, "kitchen")
        sid = store_mod.create_spot(conn, rid, "bed")
        store_mod.add_reading(conn, sid, rssi=-70, note="b1")
    finally:
        conn.close()
    rc, out, err = _run(capsys, "--db", db, "list", "--room", "kitchen")
    assert rc == 0
    assert "w1" in out and "b1" in out
    rc, out, err = _run(capsys, "--db", db, "list", "--spot", "window")
    assert rc == 0
    assert "w1" in out
    assert "b1" not in out
    assert "kitchen" in out and "window" in out


def test_export_csv_has_room_spot_columns(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    conn = store_mod.get_db(db)
    try:
        _seed_3level(conn, loc="home", room="kitchen", spot="window",
                     rssi=-55, ssid="home")
    finally:
        conn.close()
    csv_path = str(tmp_path / "out.csv")
    rc, out, err = _run(capsys, "--db", db, "export", "--csv", csv_path,
                        "--room", "kitchen", "--spot", "window")
    assert rc == 0
    with open(csv_path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    for col in ("spot_id", "room_id", "location_id", "location_name",
                "room_name", "spot_name"):
        assert col in rows[0]
    assert rows[0]["room_name"] == "kitchen"
    assert rows[0]["spot_name"] == "window"


def test_rooms_spots_list_via_main(tmp_path, capsys):
    """Regression: rooms/spots list dispatch must pass args (was TypeError)."""
    db = str(tmp_path / "cli.db")
    conn = store_mod.get_db(db)
    try:
        _seed_3level(conn, loc="schwarze-witwe", room="kitchen",
                      spot="window", rssi=-55)
    finally:
        conn.close()
    rc, out, err = _run(capsys, "--db", db, "rooms", "list",
                        "--location", "schwarze-witwe")
    assert rc == 0
    assert "kitchen" in out
    rc, out, err = _run(capsys, "--db", db, "spots", "list",
                        "--location", "schwarze-witwe",
                        "--room", "kitchen")
    assert rc == 0
    assert "window" in out


def test_rooms_spots_bare_subcommand_exits_2(tmp_path, capsys):
    """Regression: bare rooms/spots must usage-error, not AttributeError."""
    import pytest as _pytest
    db = str(tmp_path / "cli.db")
    with _pytest.raises(SystemExit) as exc:
        main(["--db", db, "rooms"])
    assert exc.value.code == 2
    with _pytest.raises(SystemExit) as exc:
        main(["--db", db, "spots"])
    assert exc.value.code == 2


def test_rooms_spots_list_unknown_no_create(tmp_path, capsys):
    """List on unknown names errors (exit 3) and writes no rows."""
    db = str(tmp_path / "cli.db")
    conn = store_mod.get_db(db)
    try:
        _seed_3level(conn, loc="home", room="kitchen", spot="window",
                      rssi=-55)
        n_loc = len(store_mod.list_locations(conn))
        n_room = len(store_mod.list_rooms(conn))
    finally:
        conn.close()
    rc, out, err = _run(capsys, "--db", db, "rooms", "list",
                        "--location", "TYPO")
    assert rc == 3
    assert "unknown" in err.lower()
    rc, out, err = _run(capsys, "--db", db, "spots", "list",
                        "--location", "home", "--room", "TYPO")
    assert rc == 3
    assert "unknown" in err.lower()
    conn = store_mod.get_db(db)
    try:
        assert len(store_mod.list_locations(conn)) == n_loc
        assert len(store_mod.list_rooms(conn)) == n_room
    finally:
        conn.close()


def test_benchmark_set_and_show(monkeypatch, tmp_path, capsys):
    db = str(tmp_path / "bench.db")
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(
            ssid="h", bssid="aa", rssi=-45, noise=-90, snr=45,
            channel="36", phy="802.11ax", tx_rate="1200"),
    )
    rc = main(["--db", db, "benchmark", "set", "--location", "HOME",
               "--no-speedtest"])
    out = capsys.readouterr()
    assert rc == 0
    assert "benchmark" in out.out.lower()
    rc2 = main(["--db", db, "benchmark", "show", "--location", "HOME"])
    out2 = capsys.readouterr()
    assert rc2 == 0
    assert "-45" in out2.out


def test_benchmark_set_stderr_speedtest_notice(monkeypatch, tmp_path, capsys):
    db = str(tmp_path / "bench_notice.db")
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(
            ssid="h", bssid="aa", rssi=-45, noise=-90, snr=45,
            channel="36", phy="802.11ax", tx_rate="1200"),
    )
    monkeypatch.setattr(
        speed_mod, "run_speedtest",
        lambda timeout=120.0: speed_mod.Speed(
            ping_ms=10.0, down_mbps=100.0, up_mbps=20.0, server="x"),
    )
    rc = main(["--db", db, "benchmark", "set", "--location", "HOME"])
    out = capsys.readouterr()
    assert rc == 0
    assert "speedtest running" in out.err


def test_scan_shows_delta_when_benchmark(monkeypatch, tmp_path, capsys):
    db = str(tmp_path / "delta.db")
    conn = store_mod.get_db(db)
    lid = store_mod.resolve_location(conn, "HOME")
    store_mod.set_benchmark(conn, lid, rssi=-45, snr=32,
                            down_mbps=300.0, up_mbps=40.0)
    conn.close()
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(
            ssid="h", bssid="aa", rssi=-67, noise=-91, snr=24,
            channel="36", phy="802.11ax", tx_rate="800"),
    )
    rc = main(["--db", db, "scan", "--location", "HOME",
               "--room", "K", "--spot", "W", "--ssid", "home-net",
               "--no-speedtest"])
    out = capsys.readouterr()
    assert rc == 0
    assert "vs bench" in out.out
