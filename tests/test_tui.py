"""TUI tests (M4): pure helpers + snapshot worker. No live network/curses."""
from __future__ import annotations

import copy

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


def test_pick_location_index_digits_and_create():
    assert tui_mod.pick_location_index("1", 3, None) == 0
    assert tui_mod.pick_location_index("3", 3, None) == 2
    assert tui_mod.pick_location_index("4", 3, None) is None
    assert tui_mod.pick_location_index("+", 3, None) == 3  # create-row sentinel
    assert tui_mod.pick_location_index("q", 3, None) is None
    assert tui_mod.pick_location_index("x", 3, None) is None
    assert tui_mod.pick_location_index("", 3, None) is None


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
    assert tui_mod.picker_press("+", 0, 2) == (2, "move", None)
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
