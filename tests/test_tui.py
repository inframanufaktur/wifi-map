"""TUI tests (M4): pure helpers + snapshot worker. No live network/curses."""
from __future__ import annotations

import copy
import threading

import pytest

from wifimap import signal as signal_mod
from wifimap import speed as speed_mod
from wifimap import store as store_mod
from wifimap import tui as tui_mod


def test_format_signal_line_full():
    sig = signal_mod.Signal(ssid="h", bssid="aa", rssi=-55, noise=-95,
                            snr=40, channel="36", phy="802.11ax", tx_rate="9")
    line = tui_mod.format_signal_line(sig)
    assert "-55 dBm" in line and "40 dB" in line and "h" in line


def test_format_signal_line_unknown():
    line = tui_mod.format_signal_line(signal_mod.Signal())
    assert "UNKNOWN" in line
    assert "None" not in line


def test_picker_press_digit_single_path():
    # digits go through picker_press only (no parallel helper logic)
    assert tui_mod.picker_press("1", 0, 3) == (0, "confirm", 0)
    assert tui_mod.picker_press("3", 0, 3) == (2, "confirm", 2)
    assert tui_mod.picker_press("4", 0, 3)[1] == "ignore"  # out of range
    assert tui_mod.picker_press("+", 0, 3) == (3, "confirm", 3)  # create row
    assert tui_mod.picker_press("q", 0, 3)[1] == "cancel"
    assert tui_mod.picker_press("x", 0, 3)[1] == "ignore"


def test_picker_start_cursor_prefills_active():
    assert tui_mod.picker_start_cursor([10, 20, 30], 20) == 1
    assert tui_mod.picker_start_cursor([10, 20, 30], None) == 0
    assert tui_mod.picker_start_cursor([10, 20, 30], 9999) == 0
    assert tui_mod.picker_start_cursor([], None) == 0


def test_picker_move_wraps_over_create_row():
    assert tui_mod.picker_move(0, 1, 2) == 1
    assert tui_mod.picker_move(2, 1, 2) == 0  # last loc wraps (create row is 2)
    assert tui_mod.picker_move(0, -1, 2) == 2  # up from top → create row
    assert tui_mod.picker_move(0, 1, 0) == 0  # empty list: only create row


def test_picker_press_enter_confirms_prefilled_cursor():
    # single Enter confirms the prefilled active instantly (spec §1)
    assert tui_mod.picker_press("", 1, 3) == (1, "confirm", 1)
    assert tui_mod.picker_press("\n", 1, 3) == (1, "confirm", 1)
    assert tui_mod.picker_press("\r", 2, 3) == (2, "confirm", 2)
    assert tui_mod.picker_press("enter", 0, 0) == (0, "confirm", 0)


def test_picker_press_arrows_and_create():
    assert tui_mod.picker_press("down", 0, 2) == (1, "move", None)
    assert tui_mod.picker_press("up", 0, 2) == (2, "move", None)
    assert tui_mod.picker_press("+", 0, 2) == (2, "confirm", 2)
    assert tui_mod.picker_press("1", 2, 3) == (0, "confirm", 0)
    assert tui_mod.picker_press("9", 0, 3)[1] == "ignore"
    assert tui_mod.picker_press("q", 1, 3) == (1, "cancel", None)
    assert tui_mod.picker_press("\x1b", 1, 3) == (1, "cancel", None)
    assert tui_mod.picker_press("x", 1, 3) == (1, "ignore", None)


def test_fallback_pick_enter_confirms_active(monkeypatch, tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        a = store_mod.create_location(conn, "room-a", floor=0)
        store_mod.create_location(conn, "room-b", floor=1)
        st = tui_mod.WalkState(db)
        st.active_id = a
        monkeypatch.setattr("builtins.input", lambda *args: "")
        assert tui_mod._fallback_pick(conn, st) is True
        assert st.active_id == a  # prefilled active kept
    finally:
        conn.close()


def test_fallback_pick_cancel(monkeypatch, tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        store_mod.create_location(conn, "room-a", floor=0)
        st = tui_mod.WalkState(db)
        monkeypatch.setattr("builtins.input", lambda *args: "q")
        assert tui_mod._fallback_pick(conn, st) is False
        assert st.active_id is None
    finally:
        conn.close()


def test_parse_floor_input_ok_and_negative():
    assert tui_mod.parse_floor_input("0") == 0
    assert tui_mod.parse_floor_input(" 2 ") == 2
    assert tui_mod.parse_floor_input("-1") == -1


@pytest.mark.parametrize("bad", ["", "  ", "1.5", "abc", "f2"])
def test_parse_floor_input_errors(bad):
    with pytest.raises(ValueError):
        tui_mod.parse_floor_input(bad)


def test_snapshot_payload_freezes_signal():
    sig = signal_mod.Signal(ssid="h", rssi=-60, noise=-90, snr=30)
    payload = tui_mod.snapshot_payload(sig)
    assert payload["rssi"] == -60 and payload["ssid"] == "h"
    sig.rssi = -1  # mutate after freeze; payload unaffected
    assert payload["rssi"] == -60


def test_attempt_read_retries_then_unknown():
    calls = {"n": 0}

    def _fail():
        calls["n"] += 1
        raise ValueError("parse boom")

    sig = tui_mod.attempt_read(_fail, max_retries=3)
    assert sig == signal_mod.Signal()
    assert calls["n"] == 4  # initial + 3 retries


def test_attempt_read_recovers():
    seq = [ValueError("x"), signal_mod.Signal(rssi=-50)]

    def _flaky():
        v = seq.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    assert tui_mod.attempt_read(_flaky, max_retries=3).rssi == -50


def test_attempt_read_nowifi_propagates_immediately():
    def _off():
        raise signal_mod.NoWiFiError("off")

    with pytest.raises(signal_mod.NoWiFiError):
        tui_mod.attempt_read(_off, max_retries=3)


def _db(tmp_path):
    return str(tmp_path / "tui.db")


def test_finish_snapshot_speed_ok(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid = store_mod.create_location(conn, "den")
    finally:
        conn.close()
    sig = {"ssid": "h", "bssid": None, "rssi": -60, "noise": -90,
           "snr": 30, "channel": "1", "phy": "n", "tx_rate": "5"}

    def _sp():
        return speed_mod.Speed(ping_ms=9.0, down_mbps=80.0, up_mbps=10.0,
                               server="S (1)")

    res = tui_mod.finish_snapshot(db, lid, dict(sig), run_speedtest_fn=_sp)
    assert res.ok and res.reading_id is not None
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert rows[0]["down_mbps"] == 80.0
        assert rows[0]["rssi"] == -60
    finally:
        conn.close()


def test_finish_snapshot_speed_fail_nulls_error(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid = store_mod.create_location(conn, "den")
    finally:
        conn.close()

    def _fail():
        raise speed_mod.SpeedtestFailedError("timeout")

    res = tui_mod.finish_snapshot(
        db, lid, {"ssid": "h", "rssi": -61}, run_speedtest_fn=_fail)
    assert res.ok
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert rows[0]["down_mbps"] is None
        assert rows[0]["server"] == "ERROR"
    finally:
        conn.close()


def test_finish_snapshot_missing_binary_signal_only(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid = store_mod.create_location(conn, "den")
    finally:
        conn.close()

    def _missing():
        raise speed_mod.SpeedtestUnavailableError("no binary")

    res = tui_mod.finish_snapshot(
        db, lid, {"ssid": "h", "rssi": -62}, run_speedtest_fn=_missing)
    assert res.ok
    assert "signal-only" in res.message.lower() or "warn" in res.message.lower()
    conn = store_mod.get_db(db)
    try:
        assert store_mod.list_readings(conn)[0]["rssi"] == -62
    finally:
        conn.close()


def test_start_snapshot_thread_daemon_join(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid = store_mod.create_location(conn, "lab")
    finally:
        conn.close()
    sig = signal_mod.Signal(ssid="h", rssi=-55)
    done = []
    t = tui_mod.start_snapshot_thread(
        db, lid, sig, no_speedtest=True,
        on_done=lambda r: done.append(r))
    assert t.daemon is True
    t.join(timeout=10)
    assert done and done[0].ok
    conn = store_mod.get_db(db)
    try:
        assert store_mod.list_readings(conn)[0]["rssi"] == -55
    finally:
        conn.close()
    # thread froze a copy: mutating original post-spawn is safe
    sig.rssi = -1


def test_walk_state_nowifi_blocks_snapshot(tmp_path):
    st = tui_mod.WalkState(str(tmp_path / "w.db"))
    st.no_wifi = True
    st.active_id = 1
    assert st.try_snapshot() is None
    assert "blocked" in st.toast.lower()


def test_walk_state_set_floor(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid = store_mod.create_location(conn, "attic", floor=0)
        st = tui_mod.WalkState(db)
        st.active_id = lid
        assert "1" in st.set_floor(conn, 1)
        locs = {loc.name: loc for loc in store_mod.list_locations(conn)}
        assert locs["attic"].floor == 1
        with pytest.raises(ValueError):
            tui_mod.parse_floor_input("bad")
    finally:
        conn.close()


def test_walk_state_set_floor_unique_conflict_toasts(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        store_mod.create_location(conn, "dup", floor=0)
        other = store_mod.create_location(conn, "dup", floor=1)
        st = tui_mod.WalkState(db)
        st.active_id = other
        assert "DB error" in st.set_floor(conn, 0)
        assert store_mod.get_location(conn, other).floor == 1
    finally:
        conn.close()


def test_walk_state_poll_unknown_after_retries(monkeypatch, tmp_path):
    st = tui_mod.WalkState(str(tmp_path / "w.db"))
    monkeypatch.setattr(
        signal_mod, "read_signal",
        lambda timeout=2.0: (_ for _ in ()).throw(ValueError("parse")),
    )
    st.poll()
    assert st.sig == signal_mod.Signal()  # UNKNOWN, no crash
    assert st.no_wifi is False


def test_concurrent_snapshot_callbacks_counter_exact(tmp_path):
    st = tui_mod.WalkState(str(tmp_path / "w.db"))
    n = 50
    with st._lock:
        st.pending = n
    threads = [
        threading.Thread(
            target=st.on_snapshot_done,
            args=(tui_mod.SnapshotResult(ok=True, reading_id=i,
                                         message="saved #%d" % i),),
        )
        for i in range(n)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads)
    toast, pending = st.ui_snapshot()
    assert pending == 0  # no lost decrement under lock
    assert toast.startswith("saved #")


def test_resolve_preset_routes(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid = store_mod.create_location(conn, "den", floor=0)
        assert tui_mod._resolve_preset(conn, None) is None
        assert tui_mod._resolve_preset(conn, "den") == lid
        assert tui_mod._resolve_preset(conn, str(lid)) == lid
        # unknown int id → None (caller toasts "unknown preset ...")
        assert tui_mod._resolve_preset(conn, 9999) is None
        # unknown name auto-creates per resolve_location semantics
        new_id = tui_mod._resolve_preset(conn, "brand-new-room")
        assert isinstance(new_id, int) and new_id != lid
    finally:
        conn.close()


def test_location_label_states(tmp_path, monkeypatch):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        assert tui_mod._location_label(conn, None) == "(none)"
        lid = store_mod.create_location(conn, "lab", floor=1)
        assert tui_mod._location_label(conn, lid) == "#%d lab (floor 1)" % lid
        assert "deleted" in tui_mod._location_label(conn, lid + 999)

        def _boom(conn_arg, loc_id):
            import sqlite3
            raise sqlite3.Error("locked")

        monkeypatch.setattr(store_mod, "get_location", _boom)
        assert "db error" in tui_mod._location_label(conn, lid)
    finally:
        conn.close()


def test_try_snapshot_success_path(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid = store_mod.create_location(conn, "den", floor=0)
    finally:
        conn.close()
    st = tui_mod.WalkState(db, no_speedtest=True)
    st.active_id = lid
    st.sig = signal_mod.Signal(ssid="h", rssi=-60, noise=-90, snr=30)
    t = st.try_snapshot()
    assert t is not None
    t.join(timeout=10)
    toast, pending = st.ui_snapshot()
    assert pending == 0
    assert "saved #" in toast
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert len(rows) == 1
        assert rows[0]["rssi"] == -60
        assert rows[0]["location_id"] == lid
    finally:
        conn.close()


def test_run_walk_rejects_bad_interval(capsys, tmp_path):
    assert tui_mod.run_walk(str(tmp_path / "w.db"), interval=0) == 3
    _, err = capsys.readouterr()
    assert "--interval" in err
    assert tui_mod.run_walk(str(tmp_path / "w.db"), interval=-1.0) == 3


def test_fallback_create_valid_and_invalid(monkeypatch, tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        st = tui_mod.WalkState(db)
        answers = iter(["garden", "0", "y"])
        monkeypatch.setattr("builtins.input",
                            lambda *args: next(answers))
        tui_mod._fallback_create(conn, st)
        assert st.active_id is not None
        loc = store_mod.get_location(conn, st.active_id)
        assert loc is not None and loc.name == "garden"
        assert loc.outdoors is True
        # invalid floor → toast, active untouched
        before = st.active_id
        answers = iter(["shed", "notanint", "n"])
        monkeypatch.setattr("builtins.input",
                            lambda *args: next(answers))
        tui_mod._fallback_create(conn, st)
        assert st.active_id == before
        assert "cancelled" in st.ui_snapshot()[0].lower()
    finally:
        conn.close()


def test_fallback_floor_valid_and_invalid(monkeypatch, tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid = store_mod.create_location(conn, "office", floor=0)
        st = tui_mod.WalkState(db)
        st.active_id = lid
        monkeypatch.setattr("builtins.input", lambda *args: "2")
        tui_mod._fallback_floor(conn, st)
        assert store_mod.get_location(conn, lid).floor == 2
        assert "2" in st.ui_snapshot()[0]
        monkeypatch.setattr("builtins.input", lambda *args: "bad")
        tui_mod._fallback_floor(conn, st)
        assert store_mod.get_location(conn, lid).floor == 2
        assert "integer" in st.ui_snapshot()[0].lower()
    finally:
        conn.close()


def test_ssid_override_skips_autodetect(tmp_path):
    st = tui_mod.WalkState(str(tmp_path / "w.db"), ssid_override="home-5g")
    calls = []

    def _boom():
        calls.append(1)
        raise AssertionError("autodetect must not run")

    st.ensure_identity(identity_fn=_boom)
    assert st.net_ssid == "home-5g"
    assert calls == []


def test_ssid_override_backfills_and_tags_snapshot(tmp_path):
    db = str(tmp_path / "w.db")
    conn = store_mod.get_db(db)
    try:
        lid = store_mod.create_location(conn, "den", floor=0)
    finally:
        conn.close()
    st = tui_mod.WalkState(db, no_speedtest=True, ssid_override="home-5g")
    st.active_id = lid
    st.poll(read_fn=lambda: signal_mod.Signal(ssid="other", rssi=-60))
    assert st.sig.ssid == "home-5g"
    t = st.try_snapshot()
    assert t is not None
    t.join(timeout=10)
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert rows[0]["ssid"] == "home-5g"
    finally:
        conn.close()


def test_ssid_override_blank_normalizes_to_none(tmp_path):
    st = tui_mod.WalkState(str(tmp_path / "w.db"), ssid_override="   ")
    assert st.ssid_override is None


def test_rate_rssi_snr_thresholds():
    assert tui_mod.rate_rssi(-55) == "GREAT"
    assert tui_mod.rate_rssi(-65) == "OK"
    assert tui_mod.rate_rssi(-80) == "WEAK"
    assert tui_mod.rate_rssi(None) == "UNKNOWN"
    assert tui_mod.rate_snr(30) == "GREAT"
    assert tui_mod.rate_snr(20) == "OK"
    assert tui_mod.rate_snr(5) == "WEAK"
    assert tui_mod.rate_snr(None) == "UNKNOWN"


def test_sparkline_vectors_gaps_and_window():
    h = tui_mod.SparkHistory(maxlen=8)
    for v in [-90, -70, -50, -30]:
        h.append(v)
    line = h.sparkline(-90, -30, 4)
    assert line == "▁▃▆█"
    h2 = tui_mod.SparkHistory(maxlen=8)
    h2.append(-50)
    h2.append(None)
    h2.append(-50)
    assert h2.sparkline(-90, -30, 3)[1] == " "
    h3 = tui_mod.SparkHistory(maxlen=3)
    for v in [1, 2, 3, 4]:
        h3.append(v)
    assert h3.sparkline(1, 4, 10) == "▃▆█"
    assert tui_mod.SparkHistory(maxlen=4).sparkline(0, 1, 4) == ""
