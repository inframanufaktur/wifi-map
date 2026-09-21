"""TUI tests (M4): pure helpers + snapshot worker. No live network/curses."""
from __future__ import annotations

import copy
import sqlite3
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


def _db(tmp_path):
    return str(tmp_path / "tui.db")


def _seed_3level(conn, loc="home", room="kitchen", spots=("window", "bed"),
                 floor=0, outdoors=False):
    """Create location + room + spots; return (lid, rid, spot_ids)."""
    lid = store_mod.create_location(conn, loc)
    rid = store_mod.create_room(conn, lid, room, floor=floor,
                                outdoors=outdoors)
    sids = [store_mod.create_spot(conn, rid, name) for name in spots]
    return lid, rid, sids


def test_fallback_pick_enter_confirms_active(monkeypatch, tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid, rid, (wid, bid) = _seed_3level(conn)
        st = tui_mod.WalkState(db)
        st.active_location_id = lid
        st.active_spot_id = wid
        # Enter at room level confirms kitchen, Enter at spot level
        # confirms window (prefilled active drilldown).
        monkeypatch.setattr("builtins.input", lambda *args: "")
        assert tui_mod._fallback_pick(conn, st) is True
        assert st.active_spot_id == wid  # prefilled active kept
    finally:
        conn.close()


def test_fallback_pick_cancel(monkeypatch, tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid, rid, _ = _seed_3level(conn)
        st = tui_mod.WalkState(db)
        st.active_location_id = lid
        monkeypatch.setattr("builtins.input", lambda *args: "q")
        assert tui_mod._fallback_pick(conn, st) is False
        assert st.active_spot_id is None
    finally:
        conn.close()


def test_fallback_pick_scoped_to_location(monkeypatch, tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid, rid, (wid, bid) = _seed_3level(conn)
        other = store_mod.create_location(conn, "office")
        other_room = store_mod.create_room(conn, other, "desk")
        other_spot = store_mod.create_spot(conn, other_room, "chair")
        st = tui_mod.WalkState(db)
        st.active_location_id = lid
        # digit 1 picks kitchen (only room in home), digit 2 picks bed.
        answers = iter(["1", "2"])
        monkeypatch.setattr("builtins.input",
                            lambda *args: next(answers))
        assert tui_mod._fallback_pick(conn, st) is True
        assert st.active_spot_id == bid
        assert st.active_spot_id != other_spot
    finally:
        conn.close()


def test_fallback_pick_no_preset_toasts(monkeypatch, tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        _seed_3level(conn)
        st = tui_mod.WalkState(db)
        assert tui_mod._fallback_pick(conn, st) is False
        assert "preset" in st.ui_snapshot()[0].lower()
    finally:
        conn.close()


def test_find_room_by_name(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid, rid, _ = _seed_3level(conn, room="kitchen", floor=1)
        assert tui_mod._find_room_by_name(conn, lid, "kitchen") == (rid, 1)
        assert tui_mod._find_room_by_name(conn, lid, "nope") is None
        other = store_mod.create_location(conn, "office")
        assert tui_mod._find_room_by_name(conn, other, "kitchen") is None
    finally:
        conn.close()


def test_fallback_create_room_reuses_on_integrity_error(
        monkeypatch, tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid, rid, _ = _seed_3level(conn, room="kitchen", floor=1)

        def _boom(*args, **kwargs):
            raise sqlite3.IntegrityError(
                "UNIQUE constraint failed: rooms.location_id, rooms.name")

        monkeypatch.setattr(store_mod, "create_room", _boom)
        answers = iter(["kitchen", "2", "n"])
        monkeypatch.setattr("builtins.input",
                            lambda *args: next(answers))
        st = tui_mod.WalkState(db)
        st.active_location_id = lid
        room_id, note = tui_mod._fallback_create_room(conn, st)
        assert room_id == rid
        assert note == "room exists (floor 1); adding spot there"
    finally:
        conn.close()


def test_fallback_create_reuses_room_then_spot(monkeypatch, tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid, rid, _ = _seed_3level(conn, room="kitchen", floor=1)

        def _boom(*args, **kwargs):
            raise sqlite3.IntegrityError(
                "UNIQUE constraint failed: rooms.location_id, rooms.name")

        monkeypatch.setattr(store_mod, "create_room", _boom)
        # room name, floor, outdoors, then spot name for reused room.
        answers = iter(["kitchen", "2", "n", "corner"])
        monkeypatch.setattr("builtins.input",
                            lambda *args: next(answers))
        st = tui_mod.WalkState(db)
        st.active_location_id = lid
        tui_mod._fallback_create(conn, st)
        assert st.active_spot_id is not None
        spot = store_mod.list_spots(conn, room_id=rid)
        assert [sp.name for sp in spot] == ["window", "bed", "corner"]
        assert "room exists" in st.ui_snapshot()[0]
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


def test_finish_snapshot_speed_ok(tmp_path, monkeypatch):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        _, _, (sid, _) = _seed_3level(conn, loc="den", room="r",
                                      spots=("s1", "s2"))
    finally:
        conn.close()
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(ssid="h", bssid=None, rssi=-60,
                                          noise=-90, snr=30, channel="1",
                                          phy="n", tx_rate="5"))

    def _sp():
        return speed_mod.Speed(ping_ms=9.0, down_mbps=80.0, up_mbps=10.0,
                               server="S (1)")

    res = tui_mod.finish_snapshot(db, sid, run_speedtest_fn=_sp)
    assert res.ok and res.reading_id is not None
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert rows[0]["down_mbps"] == 80.0
        assert rows[0]["rssi"] == -60
        assert rows[0]["snr"] == 30
    finally:
        conn.close()


def test_finish_snapshot_no_wifi_result_message(tmp_path, monkeypatch):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        _, _, (sid, _) = _seed_3level(conn, loc="den", room="r",
                                      spots=("s1", "s2"))
    finally:
        conn.close()

    def _off(*a, **k):
        raise signal_mod.NoWiFiError("off")

    monkeypatch.setattr(signal_mod, "sample_signal", _off)
    res = tui_mod.finish_snapshot(db, sid)
    assert res.ok is False
    assert "NO-WIFI" in res.message
    conn = store_mod.get_db(db)
    try:
        assert store_mod.list_readings(conn) == []
    finally:
        conn.close()


def test_finish_snapshot_other_error_empty_signal_row(tmp_path, monkeypatch):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        _, _, (sid, _) = _seed_3level(conn, loc="den", room="r",
                                      spots=("s1", "s2"))
    finally:
        conn.close()

    def _boom(*a, **k):
        raise RuntimeError("backend gone")

    monkeypatch.setattr(signal_mod, "sample_signal", _boom)
    res = tui_mod.finish_snapshot(db, sid, no_speedtest=True)
    assert res.ok
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert rows[0]["rssi"] is None
    finally:
        conn.close()


def test_finish_snapshot_speed_fail_nulls_error(tmp_path, monkeypatch):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        _, _, (sid, _) = _seed_3level(conn, loc="den", room="r",
                                      spots=("s1", "s2"))
    finally:
        conn.close()
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(ssid="h", rssi=-61))

    def _fail():
        raise speed_mod.SpeedtestFailedError("timeout")

    res = tui_mod.finish_snapshot(db, sid, run_speedtest_fn=_fail)
    assert res.ok
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert rows[0]["down_mbps"] is None
        assert rows[0]["server"] == "ERROR"
    finally:
        conn.close()


def test_finish_snapshot_missing_binary_signal_only(tmp_path, monkeypatch):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        _, _, (sid, _) = _seed_3level(conn, loc="den", room="r",
                                      spots=("s1", "s2"))
    finally:
        conn.close()
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(ssid="h", rssi=-62))

    def _missing():
        raise speed_mod.SpeedtestUnavailableError("no binary")

    res = tui_mod.finish_snapshot(db, sid, run_speedtest_fn=_missing)
    assert res.ok
    assert "signal-only" in res.message.lower() or "warn" in res.message.lower()
    conn = store_mod.get_db(db)
    try:
        assert store_mod.list_readings(conn)[0]["rssi"] == -62
    finally:
        conn.close()


def test_start_snapshot_thread_daemon_join(tmp_path, monkeypatch):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        _, _, (sid, _) = _seed_3level(conn, loc="lab", room="r",
                                      spots=("s1", "s2"))
    finally:
        conn.close()
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(ssid="h", rssi=-55))
    done = []
    t = tui_mod.start_snapshot_thread(
        db, sid, no_speedtest=True,
        on_done=lambda r: done.append(r))
    assert t.daemon is True
    t.join(timeout=10)
    assert done and done[0].ok
    conn = store_mod.get_db(db)
    try:
        assert store_mod.list_readings(conn)[0]["rssi"] == -55
    finally:
        conn.close()


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
        _, rid, (sid, _) = _seed_3level(conn, loc="home", room="attic",
                                        floor=0)
        st = tui_mod.WalkState(db)
        st.active_location_id = store_mod.resolve_location(conn, "home")
        st.active_spot_id = sid
        assert "1" in st.set_floor(conn, 1)
        assert store_mod.get_room(conn, rid).floor == 1
        with pytest.raises(ValueError):
            tui_mod.parse_floor_input("bad")
    finally:
        conn.close()


def test_walk_state_set_floor_unknown_spot_toasts(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        _seed_3level(conn)
        st = tui_mod.WalkState(db)
        st.active_spot_id = 9999
        assert "unknown" in st.set_floor(conn, 0).lower()
        st2 = tui_mod.WalkState(db)
        assert "no active" in st2.set_floor(conn, 0).lower()
    finally:
        conn.close()


def test_walk_state_set_floor_unique_conflict_toasts(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid = store_mod.create_location(conn, "home")
        store_mod.create_room(conn, lid, "dup", floor=0)
        other = store_mod.create_room(conn, lid, "dup", floor=1)
        other_spot = store_mod.create_spot(conn, other, "s")
        st = tui_mod.WalkState(db)
        st.active_location_id = lid
        st.active_spot_id = other_spot
        assert "DB error" in st.set_floor(conn, 0)
        assert store_mod.get_room(conn, other).floor == 1
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
        lid = store_mod.create_location(conn, "den")
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
        _, _, (sid, _) = _seed_3level(conn, loc="lab", room="kitchen",
                                      spots=("window", "bed"), floor=1)
        assert tui_mod._location_label(conn, sid) == (
            "#%d lab/kitchen/window (floor 1)" % sid)
        assert "deleted" in tui_mod._location_label(conn, sid + 999)

        def _boom(conn_arg, spot_id):
            import sqlite3
            raise sqlite3.Error("locked")

        monkeypatch.setattr(store_mod, "get_spot", _boom)
        assert "db error" in tui_mod._location_label(conn, sid)
    finally:
        conn.close()


def test_finish_benchmark_samples_signal(tmp_path, monkeypatch):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid, _rid, _spots = _seed_3level(conn, loc="lab", room="r",
                                         spots=("s1", "s2"))
    finally:
        conn.close()
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(ssid="h", rssi=-45, noise=-90,
                                          snr=45))
    res = tui_mod._finish_benchmark(db, lid, no_speedtest=True)
    assert res.ok
    assert res.rssi == -45 and res.snr == 45
    conn = store_mod.get_db(db)
    try:
        bench = store_mod.get_benchmark(conn, lid)
        assert bench["rssi"] == -45
    finally:
        conn.close()


def test_try_snapshot_success_path(tmp_path, monkeypatch):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        _, _, (sid, _) = _seed_3level(conn, loc="den", room="r",
                                      spots=("s1", "s2"))
    finally:
        conn.close()
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(ssid="h", rssi=-60, noise=-90,
                                          snr=30))
    st = tui_mod.WalkState(db, no_speedtest=True)
    st.active_spot_id = sid
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
        assert rows[0]["spot_id"] == sid
    finally:
        conn.close()


def test_try_snapshot_no_spot_toasts(tmp_path):
    st = tui_mod.WalkState(str(tmp_path / "w.db"), no_speedtest=True)
    assert st.try_snapshot() is None
    assert "no active spot" in st.ui_snapshot()[0].lower()


def test_try_snapshot_sets_running_toast(tmp_path, monkeypatch):
    st = tui_mod.WalkState(str(tmp_path / "w.db"), no_speedtest=True)
    st.active_spot_id = 1
    st.sig = signal_mod.Signal(ssid="h", rssi=-60, noise=-90, snr=30)
    captured = {}

    def fake_thread(*args, **kwargs):
        captured["called"] = True

        class _T:
            def join(self, timeout=None):
                pass
        return _T()

    monkeypatch.setattr(tui_mod, "start_snapshot_thread", fake_thread)
    t = st.try_snapshot()
    assert t is not None
    assert captured["called"]
    toast, _ = st.ui_snapshot()
    assert "snapshot running" in toast.lower()


def test_run_walk_rejects_bad_interval(capsys, tmp_path):
    assert tui_mod.run_walk(str(tmp_path / "w.db"), interval=0) == 3
    _, err = capsys.readouterr()
    assert "--interval" in err
    assert tui_mod.run_walk(str(tmp_path / "w.db"), interval=-1.0) == 3


def test_fallback_create_valid_and_invalid(monkeypatch, tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid = store_mod.create_location(conn, "home")
        st = tui_mod.WalkState(db)
        st.active_location_id = lid
        answers = iter(["kitchen", "0", "y", "window"])
        monkeypatch.setattr("builtins.input",
                            lambda *args: next(answers))
        tui_mod._fallback_create(conn, st)
        assert st.active_spot_id is not None
        spot = store_mod.get_spot(conn, st.active_spot_id)
        assert spot is not None and spot.name == "window"
        room = store_mod.get_room(conn, spot.room_id)
        assert room is not None and room.name == "kitchen"
        assert room.outdoors is True
        # invalid floor → toast, active untouched
        before = st.active_spot_id
        answers = iter(["shed", "notanint", "n"])
        monkeypatch.setattr("builtins.input",
                            lambda *args: next(answers))
        tui_mod._fallback_create(conn, st)
        assert st.active_spot_id == before
        assert "cancelled" in st.ui_snapshot()[0].lower()
    finally:
        conn.close()


def test_fallback_floor_valid_and_invalid(monkeypatch, tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid, rid, (sid, _) = _seed_3level(conn, loc="home", room="office",
                                          floor=0)
        st = tui_mod.WalkState(db)
        st.active_location_id = lid
        st.active_spot_id = sid
        monkeypatch.setattr("builtins.input", lambda *args: "2")
        tui_mod._fallback_floor(conn, st)
        assert store_mod.get_room(conn, rid).floor == 2
        assert "2" in st.ui_snapshot()[0]
        monkeypatch.setattr("builtins.input", lambda *args: "bad")
        tui_mod._fallback_floor(conn, st)
        assert store_mod.get_room(conn, rid).floor == 2
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


def test_ssid_override_backfills_and_tags_snapshot(tmp_path, monkeypatch):
    db = str(tmp_path / "w.db")
    conn = store_mod.get_db(db)
    try:
        _, _, (sid, _) = _seed_3level(conn, loc="den", room="r",
                                      spots=("s1", "s2"))
    finally:
        conn.close()
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(ssid="other", rssi=-60))
    st = tui_mod.WalkState(db, no_speedtest=True, ssid_override="home-5g")
    st.active_spot_id = sid
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


def test_selected_ssid_entity_tags_walk_and_snapshot(tmp_path, monkeypatch):
    db = str(tmp_path / "w.db")
    conn = store_mod.get_db(db)
    try:
        location_id, _, (spot_id, _) = _seed_3level(
            conn, loc="den", room="r", spots=("s1", "s2"))
        ssid_id = store_mod.create_ssid(conn, location_id, "home-5g")
        state = tui_mod.WalkState(
            db, no_speedtest=True, ssid_id=ssid_id, ssid_name="home-5g")
        state.active_location_id = location_id
        tui_mod._start_walk_session(
            conn, state, walk_name="entity walk", compare_to=None)
        walk_id = state.current_walk_id
        assert store_mod.get_walk(conn, walk_id).ssid_id == ssid_id
    finally:
        conn.close()

    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(ssid="other", rssi=-60))
    state.active_spot_id = spot_id
    thread = state.try_snapshot()
    assert thread is not None
    thread.join(timeout=10)

    conn = store_mod.get_db(db)
    try:
        row = store_mod.list_readings(conn, walk_id=walk_id)[0]
        assert row["ssid_id"] == ssid_id
        assert row["ssid"] == "home-5g"
    finally:
        conn.close()


def test_run_walk_preflight_resolves_ssid_flag(
        tmp_path, monkeypatch):
    db = str(tmp_path / "walk.db")
    captured = {}
    monkeypatch.setattr(tui_mod.sys.stdin, "isatty", lambda: False)

    def fallback(db_path, interval, location_preset, no_speedtest,
                 ssid=None, walk_name=None, compare_to=None, ssid_id=None,
                 network_bssid=None):
        captured.update({
            "location": location_preset,
            "ssid": ssid,
            "ssid_id": ssid_id,
            "bssid": network_bssid,
        })
        return 0

    monkeypatch.setattr(tui_mod, "_walk_fallback", fallback)

    assert tui_mod.run_walk(
        db, location_preset="home", ssid="home-5g",
        no_speedtest=True) == 0
    conn = store_mod.get_db(db)
    try:
        location_id = store_mod.lookup_location(conn, "home")
        selected = store_mod.list_ssids(conn, location_id)
        assert len(selected) == 1
        assert captured == {
            "location": str(location_id),
            "ssid": "home-5g",
            "ssid_id": selected[0].id,
            "bssid": None,
        }
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
    assert line == "▁▃▅▇"
    h2 = tui_mod.SparkHistory(maxlen=8)
    h2.append(-50)
    h2.append(None)
    h2.append(-50)
    assert h2.sparkline(-90, -30, 3)[1] == " "
    h3 = tui_mod.SparkHistory(maxlen=3)
    for v in [1, 2, 3, 4]:
        h3.append(v)
    assert h3.sparkline(1, 4, 10) == "       ▃▅▇"
    assert tui_mod.SparkHistory(maxlen=4).sparkline(0, 1, 4) == ""


def test_poll_appends_history(tmp_path):
    st = tui_mod.WalkState(str(tmp_path / "w.db"), history_max=5)
    st.poll(read_fn=lambda: signal_mod.Signal(rssi=-60, noise=-90, snr=30))
    st.poll(read_fn=lambda: signal_mod.Signal(rssi=-61, noise=-91, snr=29))
    assert st.hist_rssi.sparkline(-90, -30, 5) != ""
    assert len(st.hist_rssi.sparkline(-90, -30, 5)) == 5


def test_poll_nowifi_appends_gap(tmp_path):
    def _off():
        raise signal_mod.NoWiFiError("off")

    st = tui_mod.WalkState(str(tmp_path / "w.db"), history_max=5)
    for _ in range(3):
        st.poll(read_fn=_off)
    assert st.no_wifi is True
    assert st.hist_rssi.sparkline(-90, -30, 3) == "   "


def test_sparkline_right_aligns_newest():
    h = tui_mod.SparkHistory(maxlen=60)
    h.append(-60)
    h.append(-50)
    line = h.sparkline(-90, -30, 5)
    assert len(line) == 5
    assert line[:3] == "   "
    assert line[3:] != "   "


def test_history_cap_windows():
    assert tui_mod.history_cap(1.0) == 60
    assert tui_mod.history_cap(0.5) == 120
    assert tui_mod.history_cap(90.0) == 1


def test_rating_style_maps_all_ratings():
    assert tui_mod.rating_style("GREAT") == (1, "32")
    assert tui_mod.rating_style("OK") == (2, "33")
    assert tui_mod.rating_style("WEAK") == (3, "31")
    assert tui_mod.rating_style("UNKNOWN") == (0, "37")


def test_layout_mode_boundary():
    assert tui_mod.layout_mode(99) == "narrow"
    assert tui_mod.layout_mode(100) == "wide"
    assert tui_mod.layout_mode(200) == "wide"


def test_curses_pair_for_rating_uses_rating_style():
    pair, _ = tui_mod.rating_style(tui_mod.rate_rssi(-55))
    assert pair == 1
    pair, _ = tui_mod.rating_style(tui_mod.rate_rssi(-80))
    assert pair == 3


def test_ansi_wrap_respects_no_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert tui_mod.ansi_wrap("x", "32") == "\x1b[32mx\x1b[0m"
    monkeypatch.setenv("NO_COLOR", "1")
    assert tui_mod.ansi_wrap("x", "32") == "x"


def test_graph_width_fits_w_minus_1():
    # label(6) + bar + suffix(6) must fit w-1; old w-12 truncated "]".
    for w in (40, 80, 100):
        gw = tui_mod.graph_width(w, 6, 6)
        assert 6 + gw + 6 <= w - 1
        assert gw == max(10, w - 13)
    assert tui_mod.graph_width(20, 6, 6) == 10


def test_wide_graph_width_fits_w_minus_1():
    left = len("RSSI -57 dBm [GREAT]")
    for w in (100, 120, 200):
        gw = tui_mod.wide_graph_width(w, left, 6, 3, 0)
        assert len("RSSI -57 dBm [GREAT]") + 3 + 6 + gw <= w - 1


def test_grouped_graph_width_aligns_right_edges():
    p1 = "RSSI %4d dBm [%-7s] " % (-60, "GREAT")
    p2 = "SNR %3d dB [%-7s] " % (30, "GREAT")
    p3 = "noise %4d dBm ch %s phy %s tx %s " % (-90, "36", "ax", "9")
    for w in (80, 100, 120):
        gw = tui_mod.grouped_graph_width(w, [p1, p2, p3])
        assert gw == max(10, w - max(len(p1), len(p2), len(p3)) - 6 - 1)
        for p in (p1, p2, p3):
            assert len(p) + gw + len(" [60s]") <= w - 1
    assert tui_mod.grouped_graph_width(20, [p1, p2, p3]) == 10
    assert tui_mod.grouped_graph_width(80, []) == max(10, 80 - 0 - 6 - 1)


def test_format_meter_left_no_pad_inside_brackets():
    left_rssi = tui_mod.format_meter_left("RSSI", "-63 dBm", "OK")
    left_snr = tui_mod.format_meter_left("SNR", "29 dB", "GREAT")
    left_noise = tui_mod.format_meter_left("noise", "-92 dBm", None)
    # no padding inside brackets: hugging text
    assert "[OK]" in left_rssi and "[OK " not in left_rssi
    assert "[GREAT]" in left_snr and "[GREAT " not in left_snr
    # fixed-width label field 6 chars
    assert left_rssi.startswith("RSSI  ")
    assert left_snr.startswith("SNR   ")
    assert left_noise.startswith("noise ")
    # value field fixed width: label(6)+value(10) prefix equal length
    assert left_rssi[6:16] == "-63 dBm   "
    assert left_snr[6:16] == "29 dB     "
    assert left_noise[6:16] == "-92 dBm   "


def test_format_meter_row_pipe_aligns():
    lefts = [
        tui_mod.format_meter_left("RSSI", "-63 dBm", "OK"),
        tui_mod.format_meter_left("SNR", "29 dB", "GREAT"),
        tui_mod.format_meter_left("noise", "-92 dBm", None),
    ]
    max_left, gw = tui_mod.meter_layout(80, lefts)
    assert max_left == max(len(s) for s in lefts)
    assert gw == max(10, 80 - max_left - 3 - 6 - 1)
    rows = [tui_mod.format_meter_row(*args, bar="##", max_left=max_left)
            for args in (("RSSI", "-63 dBm", "OK"),
                         ("SNR", "29 dB", "GREAT"),
                         ("noise", "-92 dBm", None))]
    pipes = [r.index("|") for r in rows]
    assert pipes[0] == pipes[1] == pipes[2] == max_left + 1
    for r in rows:
        assert r.endswith("## [60s]")
        assert "[OK ]" not in r and "[GREAT ]" not in r


def test_format_radio_line_placeholders():
    assert tui_mod.format_radio_line(None, None, None) == "mcs - band - sec -"
    assert tui_mod.format_radio_line(9, "5 GHz", "WPA3 Personal") == (
        "mcs 9 band 5 GHz sec WPA3 Personal")


def test_format_addr_line_omits_when_unknown():
    assert tui_mod.format_addr_line(None, None, None) == ""
    assert tui_mod.format_addr_line("1.2.3.4", None, None) == (
        "IP 1.2.3.4 RTR - MAC -")
    line = tui_mod.format_addr_line("192.168.178.50", "192.168.178.1",
                                    "aa:bb:cc:dd:ee:ff")
    assert "192.168.178.50" in line and "192.168.178.1" in line


def test_ensure_addrs_one_shot_and_never_raise(tmp_path):
    st = tui_mod.WalkState(str(tmp_path / "w.db"))
    assert st.ip is None and st.router is None and st.mac is None
    calls = []

    def _fn():
        calls.append(1)
        return ("1.1.1.1", "1.1.1.254", "aa:bb:cc:00:11:22")

    assert st.ensure_addrs(addrs_fn=_fn) == ("1.1.1.1", "1.1.1.254", "aa:bb:cc:00:11:22")
    assert st.ensure_addrs(addrs_fn=_fn) == ("1.1.1.1", "1.1.1.254", "aa:bb:cc:00:11:22")
    assert len(calls) == 1  # one-shot

    st2 = tui_mod.WalkState(str(tmp_path / "w2.db"))

    def _boom():
        raise RuntimeError("nope")

    assert st2.ensure_addrs(addrs_fn=_boom) is None
    assert (st2.ip, st2.router, st2.mac) == (None, None, None)


def test_snapshot_payload_excludes_display_only():
    sig = signal_mod.Signal(ssid="h", rssi=-60, mcs=9, band="5 GHz",
                            security="WPA3 Personal")
    payload = tui_mod.snapshot_payload(sig)
    assert "mcs" not in payload and "band" not in payload
    assert "security" not in payload


def test_walk_state_last_result_delta(tmp_path):
    assert tui_mod.KEY_BENCHMARK == "b"
    st = tui_mod.WalkState(str(tmp_path / "w.db"))
    assert st.last_result == ""
    cur = {"rssi": -60, "snr": 30, "down_mbps": 80.0, "up_mbps": 10.0}
    bench = {"rssi": -70, "snr": 20, "down_mbps": 100.0, "up_mbps": 20.0}
    delta = store_mod.format_benchmark_delta(cur, bench)
    assert delta != ""
    st.last_result = "saved #1 vs bench (%s)" % delta
    assert "vs bench" in st.last_result


def test_snapshot_last_result_delta_with_speed(tmp_path, monkeypatch):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid, _rid, (sid, _bid) = _seed_3level(conn)
        store_mod.set_benchmark(
            conn, lid, rssi=-70, snr=20, ping_ms=30.0,
            down_mbps=100.0, up_mbps=20.0)
    finally:
        conn.close()
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(ssid="h", rssi=-60, noise=-90,
                                          snr=30))
    st = tui_mod.WalkState(db)
    st.active_location_id = lid
    st.active_spot_id = sid
    conn = store_mod.get_db(db)
    try:
        st.refresh_benchmark(conn)
    finally:
        conn.close()

    def fake_speedtest():
        return speed_mod.Speed(ping_ms=25.0, down_mbps=80.0, up_mbps=10.0)

    t = st.try_snapshot(run_speedtest_fn=fake_speedtest)
    assert t is not None
    t.join(timeout=10)
    with st._lock:
        toast, last = st.toast, st.last_result
    assert "vs bench" in last
    assert "down -20.0" in last and "up -10.0" in last
    # toast carries the same delta (spec: saved #N (vs bench ...))
    assert last == toast


def test_snapshot_last_result_no_benchmark_plain(tmp_path):
    st = tui_mod.WalkState(str(tmp_path / "w.db"))
    st.on_snapshot_done(tui_mod.SnapshotResult(ok=True, reading_id=1,
                                               message="saved #1"))
    with st._lock:
        toast, last = st.toast, st.last_result
    assert last == "saved #1"
    assert "vs bench" not in last
    assert toast == last


def test_fmt_mbps():
    assert tui_mod.fmt_mbps(None) == "-"
    assert tui_mod.fmt_mbps(267.524744) == "267.5"
    assert tui_mod.fmt_mbps(0) == "0.0"


def test_fmt_rate_val():
    assert tui_mod.fmt_rate_val(None) == "-"
    assert tui_mod.fmt_rate_val(80.0) == "80.0 Mbps"


def test_sparkline_auto_scales_to_buffer_max():
    h = tui_mod.SparkHistory(maxlen=10)
    for v in (1.0, 2.0, 4.0):
        h.append(v)
    s = h.sparkline_auto(4)
    assert len(s) == 4
    assert s[-1] == "▇"
    assert s == " ▃▄▇"  # right-aligned; buffer max (4.0) → full block


def test_sparkline_auto_empty_uses_min_hi():
    h = tui_mod.SparkHistory(maxlen=4)
    assert h.sparkline_auto(3) == ""
    h.append(None)
    assert h.sparkline_auto(3) == "   "  # None blanks, flat scale


def test_walkstate_poll_traffic_history():
    rates_seq = [None, (1.0, 0.5), (2.0, 1.0)]
    calls = {"n": 0}

    def tf():
        n = min(calls["n"], len(rates_seq) - 1)
        calls["n"] += 1
        return rates_seq[n]

    st = tui_mod.WalkState("/tmp/unused.db", traffic_fn=tf)
    st.poll(read_fn=lambda: signal_mod.Signal(rssi=-60, noise=-90, snr=30))
    assert st.last_rates is None
    assert list(st.hist_down._buf) == [None]
    st.poll(read_fn=lambda: signal_mod.Signal(rssi=-60, noise=-90, snr=30))
    assert st.last_rates == (1.0, 0.5)
    st.poll(read_fn=lambda: signal_mod.Signal(rssi=-60, noise=-90, snr=30))
    assert st.last_rates == (2.0, 1.0)
    assert list(st.hist_up._buf)[-1] == 1.0


def test_walkstate_poll_traffic_fn_raises():
    def tf():
        raise RuntimeError("netstat gone")

    st = tui_mod.WalkState("/tmp/unused.db", traffic_fn=tf)
    st.poll(read_fn=lambda: signal_mod.Signal(rssi=-60))
    assert st.last_rates is None


def test_finish_snapshot_message_includes_speed(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    conn = store_mod.get_db(db)
    try:
        _, _, (sid, _) = _seed_3level(conn, loc="den", room="r",
                                      spots=("s1", "s2"))
    finally:
        conn.close()
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(ssid="h", rssi=-60, snr=30))

    def _sp():
        return speed_mod.Speed(ping_ms=9.0, down_mbps=80.0, up_mbps=10.0,
                               server="S (1)")

    res = tui_mod.finish_snapshot(db, sid, run_speedtest_fn=_sp)
    assert res.ok
    assert "down 80.0 up 10.0 Mbps" in res.message


def test_finish_snapshot_message_no_speedtest(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    conn = store_mod.get_db(db)
    try:
        _, _, (sid, _) = _seed_3level(conn, loc="den", room="r",
                                      spots=("s1", "s2"))
    finally:
        conn.close()
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *a, **k: signal_mod.Signal(ssid="h", rssi=-60, snr=30))
    res = tui_mod.finish_snapshot(db, sid, no_speedtest=True)
    assert res.ok
    assert "Mbps" not in res.message
    assert res.message.startswith("saved #")


# ---------------------------------------------------------------------------
# Live comparison against a prior walk (RED tests; production follows)
# ---------------------------------------------------------------------------

def _seed_comparison_walks(conn):
    """Two physical spots plus before/current walk records."""
    lid, _rid, (window, bed) = _seed_3level(
        conn, loc="home", room="office", spots=("window", "bed"))
    before = store_mod.create_walk(conn, lid, "before mesh")
    current = store_mod.create_walk(conn, lid, "after mesh")
    return lid, window, bed, before, current


def test_walk_comparison_selects_and_caches_per_spot_medians(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid, window, _bed, before, current = _seed_comparison_walks(conn)
        store_mod.add_reading(
            conn, window, walk_id=before, rssi=-80, noise=-90, snr=10,
            ping_ms=30.0, down_mbps=40.0, up_mbps=8.0)
        store_mod.add_reading(
            conn, window, walk_id=before, rssi=-70, noise=-94, snr=24,
            ping_ms=20.0, down_mbps=80.0, up_mbps=12.0)

        walks = store_mod.list_walks(conn, location_id=lid)
        assert [walk.id for walk in walks] == [current, before]

        state = tui_mod.WalkState(
            db, current_walk_id=current, traffic_fn=lambda: None)
        state.select_baseline_walk(conn, before)
        state.set_active_spot(window)
        baseline = state.baseline_for_active_spot()
        assert baseline is not None
        assert baseline.rssi == pytest.approx(-75.0)
        assert baseline.noise == pytest.approx(-92.0)
        assert baseline.snr == pytest.approx(17.0)
        assert baseline.ping_ms == pytest.approx(25.0)
        assert baseline.down_mbps == pytest.approx(60.0)
        assert baseline.up_mbps == pytest.approx(10.0)

        # Baselines are loaded once, not re-queried on every live poll.
        conn.execute("DELETE FROM readings WHERE walk_id = ?", (before,))
        conn.commit()
        assert state.baseline_for_active_spot() == baseline
    finally:
        conn.close()


def test_walk_comparison_uses_rolling_current_signal_at_active_spot(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        _lid, window, _bed, before, current = _seed_comparison_walks(conn)
        store_mod.add_reading(
            conn, window, walk_id=before, rssi=-75, noise=-90, snr=15)
        state = tui_mod.WalkState(
            db, current_walk_id=current, comparison_window=3,
            traffic_fn=lambda: None)
        state.select_baseline_walk(conn, before)
        state.set_active_spot(window)
    finally:
        conn.close()

    for rssi, noise, snr in ((-66, -92, 26), (-60, -96, 36),
                             (-63, -94, 31)):
        state.poll(read_fn=lambda r=rssi, n=noise, s=snr:
                   signal_mod.Signal(rssi=r, noise=n, snr=s))

    comparison = state.active_comparison()
    assert comparison["rssi"].current == pytest.approx(-63.0)
    assert comparison["rssi"].baseline == pytest.approx(-75.0)
    assert comparison["rssi"].delta == pytest.approx(12.0)
    assert comparison["noise"].current == pytest.approx(-94.0)
    assert comparison["noise"].delta == pytest.approx(-4.0)
    assert comparison["snr"].current == pytest.approx(31.0)
    assert comparison["snr"].delta == pytest.approx(16.0)


def test_walk_comparison_missing_baseline_spot_keeps_live_values(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        _lid, window, bed, before, current = _seed_comparison_walks(conn)
        store_mod.add_reading(conn, window, walk_id=before, rssi=-75)
        state = tui_mod.WalkState(
            db, current_walk_id=current, traffic_fn=lambda: None)
        state.select_baseline_walk(conn, before)
        state.set_active_spot(bed)
    finally:
        conn.close()

    state.poll(read_fn=lambda: signal_mod.Signal(
        rssi=-58, noise=-93, snr=35))
    assert state.baseline_for_active_spot() is None
    comparison = state.active_comparison()
    assert comparison["rssi"].current == pytest.approx(-58.0)
    assert comparison["rssi"].baseline is None
    assert comparison["rssi"].delta is None


def test_walk_comparison_resets_rolling_values_on_spot_switch(tmp_path):
    state = tui_mod.WalkState(
        str(tmp_path / "walk.db"), comparison_window=3,
        traffic_fn=lambda: None)
    state.set_active_spot(10)
    state.poll(read_fn=lambda: signal_mod.Signal(rssi=-55, snr=35))
    assert state.active_comparison()["rssi"].current == pytest.approx(-55.0)

    state.set_active_spot(20)
    assert state.active_comparison()["rssi"].current is None
    state.poll(read_fn=lambda: signal_mod.Signal(rssi=-80, snr=10))
    assert state.active_comparison()["rssi"].current == pytest.approx(-80.0)

    # Returning to a spot starts a fresh observation window too.
    state.set_active_spot(10)
    assert state.active_comparison()["rssi"].current is None


def test_transient_speed_probe_updates_comparison_without_saving(tmp_path):
    assert tui_mod.KEY_SPEED_PROBE == "t"
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        _lid, window, _bed, before, current = _seed_comparison_walks(conn)
        store_mod.add_reading(
            conn, window, walk_id=before, ping_ms=25.0,
            down_mbps=50.0, up_mbps=10.0)
        state = tui_mod.WalkState(db, current_walk_id=current)
        state.select_baseline_walk(conn, before)
        state.set_active_spot(window)
        before_count = len(store_mod.list_readings(conn))
    finally:
        conn.close()

    thread = state.try_speed_probe(run_speedtest_fn=lambda: speed_mod.Speed(
        ping_ms=15.0, down_mbps=90.0, up_mbps=20.0,
        server="test server"))
    assert thread is not None
    thread.join(timeout=10)
    probe = state.speed_probe_for_active_spot()
    assert probe is not None
    assert probe.down_mbps == pytest.approx(90.0)
    comparison = state.active_comparison()
    assert comparison["down_mbps"].delta == pytest.approx(40.0)
    assert comparison["up_mbps"].delta == pytest.approx(10.0)
    assert comparison["ping_ms"].delta == pytest.approx(-10.0)

    conn = store_mod.get_db(db)
    try:
        assert len(store_mod.list_readings(conn)) == before_count
    finally:
        conn.close()


def test_saved_snapshot_carries_current_walk_id(tmp_path, monkeypatch):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        _lid, window, _bed, _before, current = _seed_comparison_walks(conn)
    finally:
        conn.close()
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *args, **kwargs: signal_mod.Signal(rssi=-60, snr=30))

    state = tui_mod.WalkState(
        db, current_walk_id=current, no_speedtest=True)
    state.set_active_spot(window)
    thread = state.try_snapshot()
    assert thread is not None
    thread.join(timeout=10)

    conn = store_mod.get_db(db)
    try:
        saved = store_mod.list_readings(conn, walk_id=current)
        assert len(saved) == 1
        assert saved[0]["walk_id"] == current
        assert saved[0]["spot_id"] == window
    finally:
        conn.close()


def test_stale_speed_probe_result_does_not_appear_after_spot_switch(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def delayed_speedtest():
        started.set()
        assert release.wait(timeout=10)
        return speed_mod.Speed(
            ping_ms=12.0, down_mbps=100.0, up_mbps=20.0)

    state = tui_mod.WalkState(str(tmp_path / "walk.db"))
    state.set_active_spot(10)
    thread = state.try_speed_probe(run_speedtest_fn=delayed_speedtest)
    assert thread is not None
    assert started.wait(timeout=10)
    state.set_active_spot(20)
    release.set()
    thread.join(timeout=10)

    assert state.speed_probe_for_active_spot() is None
    comparison = state.active_comparison()
    assert comparison["down_mbps"].current is None


def test_walk_curses_labels_passive_rates_as_traffic(
        tmp_path, monkeypatch):
    class RecordingScreen:
        def __init__(self):
            self.rows = {}

        def nodelay(self, _enabled):
            pass

        def timeout(self, _milliseconds):
            pass

        def clear(self):
            self.rows = {}

        def getmaxyx(self):
            return (30, 120)

        def addstr(self, row, col, text, *args):
            self.rows.setdefault(row, []).append((col, text))

        def refresh(self):
            pass

        def getch(self):
            return ord("q")

        def rendered_lines(self):
            return ["".join(text for _col, text in sorted(parts))
                    for _row, parts in sorted(self.rows.items())]

    def poll_once(state):
        state.sig = signal_mod.Signal(rssi=-60, noise=-90, snr=30)
        state.last_rates = (12.0, 3.0)
        state.hist_rssi.append(-60)
        state.hist_noise.append(-90)
        state.hist_snr.append(30)
        state.hist_down.append(12.0)
        state.hist_up.append(3.0)

    monkeypatch.setattr(tui_mod.WalkState, "poll", poll_once)
    monkeypatch.setattr(
        tui_mod.WalkState, "ensure_identity", lambda self: None)
    monkeypatch.setattr(
        tui_mod.WalkState, "ensure_addrs", lambda self: None)
    screen = RecordingScreen()

    assert tui_mod._walk_curses(
        screen, str(tmp_path / "walk.db"), interval=1.0,
        location_preset=None, no_speedtest=False) == 0
    rendered = "\n".join(screen.rendered_lines()).lower()
    assert "traffic down" in rendered
    assert "traffic up" in rendered


def test_baseline_name_must_be_unambiguous_and_ids_still_work(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid, _rid, (spot, _other) = _seed_3level(conn)
        older = store_mod.create_walk(
            conn, lid, "before", started_at="2026-09-20T08:00:00+00:00")
        newer = store_mod.create_walk(
            conn, lid, "before", started_at="2026-09-21T08:00:00+00:00")
        current = store_mod.create_walk(conn, lid, "after")
        store_mod.add_reading(conn, spot, walk_id=older, rssi=-80)
        store_mod.add_reading(conn, spot, walk_id=newer, rssi=-70)
        state = tui_mod.WalkState(db, current_walk_id=current)

        with pytest.raises(ValueError, match=r"ambiguous.*IDs.*%d.*%d" % (
                newer, older)):
            state.select_baseline_walk(conn, "before")

        assert state.select_baseline_walk(conn, older) == older
        state.set_active_spot(spot)
        assert state.baseline_for_active_spot().rssi == -80
    finally:
        conn.close()


def test_baseline_picker_rows_show_name_start_id_and_off(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid, _rid, (spot, _other) = _seed_3level(conn)
        before = store_mod.create_walk(
            conn, lid, "before mesh",
            started_at="2026-09-20T08:15:00+00:00")
        store_mod.add_reading(conn, spot, walk_id=before, rssi=-70)
        current = store_mod.create_walk(conn, lid, "after mesh")
        state = tui_mod.WalkState(db, current_walk_id=current)
        state.active_location_id = lid

        rows = tui_mod._baseline_walk_rows(conn, state)
        assert rows[-1] == (0, "Off")
        assert rows[0][0] == before
        assert "before mesh" in rows[0][1]
        assert "2026-09-20T08:15:00+00:00" in rows[0][1]
        assert "#%d" % before in rows[0][1]
    finally:
        conn.close()


def test_stale_snapshot_does_not_update_spot_specific_ui(
        tmp_path, monkeypatch):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        _lid, _rid, (first, second) = _seed_3level(conn)
    finally:
        conn.close()
    monkeypatch.setattr(
        signal_mod, "sample_signal",
        lambda *args, **kwargs: signal_mod.Signal(rssi=-60, snr=30))
    started = threading.Event()
    release = threading.Event()

    def delayed_speedtest():
        started.set()
        assert release.wait(timeout=10)
        return speed_mod.Speed(
            ping_ms=10.0, down_mbps=100.0, up_mbps=20.0)

    state = tui_mod.WalkState(db)
    state.set_active_spot(first)
    thread = state.try_snapshot(run_speedtest_fn=delayed_speedtest)
    assert thread is not None
    assert started.wait(timeout=10)
    state.set_active_spot(second)
    release.set()
    thread.join(timeout=10)

    with state._lock:
        assert state.pending == 0
        assert state.toast == ""
        assert state.last_result == ""
    assert state.speed_probe_for_active_spot() is None
    assert state.active_comparison()["down_mbps"].current is None
    conn = store_mod.get_db(db)
    try:
        saved = store_mod.list_readings(conn)
        assert len(saved) == 1 and saved[0]["spot_id"] == first
    finally:
        conn.close()


def test_pending_measurement_blocks_snapshots_and_probes(
        tmp_path, monkeypatch):
    state = tui_mod.WalkState(str(tmp_path / "walk.db"))
    state.set_active_spot(1)

    class PendingThread:
        def join(self, timeout=None):
            pass

    monkeypatch.setattr(
        tui_mod, "start_snapshot_thread",
        lambda *args, **kwargs: PendingThread())
    first = state.try_snapshot()
    assert first is not None
    assert state.ui_snapshot()[1] == 1
    assert state.try_snapshot() is None
    assert state.try_speed_probe(
        run_speedtest_fn=lambda: speed_mod.Speed()) is None
    assert "already running" in state.ui_snapshot()[0]


def test_running_probe_blocks_snapshot(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def delayed_speedtest():
        started.set()
        assert release.wait(timeout=10)
        return speed_mod.Speed(down_mbps=80.0, up_mbps=10.0)

    state = tui_mod.WalkState(str(tmp_path / "walk.db"))
    state.set_active_spot(1)
    thread = state.try_speed_probe(run_speedtest_fn=delayed_speedtest)
    assert thread is not None
    assert started.wait(timeout=10)
    assert state.try_snapshot() is None
    assert "already running" in state.ui_snapshot()[0]
    release.set()
    thread.join(timeout=10)


def test_finish_walk_waits_for_snapshot_before_marking_ended(
        tmp_path, monkeypatch):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid, _rid, (spot, _other) = _seed_3level(conn)
        walk = store_mod.create_walk(conn, lid, "after")
    finally:
        conn.close()
    started = threading.Event()
    release = threading.Event()

    def delayed_signal():
        started.set()
        assert release.wait(timeout=10)
        return signal_mod.Signal(rssi=-60, snr=30)

    monkeypatch.setattr(signal_mod, "sample_signal", delayed_signal)
    state = tui_mod.WalkState(
        db, no_speedtest=True, current_walk_id=walk)
    state.set_active_spot(spot)
    worker = state.try_snapshot()
    assert worker is not None and started.wait(timeout=10)
    timer = threading.Timer(0.05, release.set)
    timer.start()

    conn = store_mod.get_db(db)
    try:
        assert tui_mod._finish_walk_session(
            conn, state, worker_timeout=2.0) is True
        saved = store_mod.list_readings(conn, walk_id=walk)
        ended = store_mod.get_walk(conn, walk).ended_at
        assert len(saved) == 1
        assert ended is not None
        assert saved[0]["ts"] <= ended
    finally:
        conn.close()
        timer.join(timeout=10)


def test_finish_walk_timeout_leaves_walk_open_and_reports_error(
        tmp_path, capsys):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid = store_mod.create_location(conn, "home")
        walk = store_mod.create_walk(conn, lid, "after")
        state = tui_mod.WalkState(db, current_walk_id=walk)

        class StuckWorker:
            def join(self, timeout=None):
                pass

            def is_alive(self):
                return True

        state._workers.append(StuckWorker())
        assert tui_mod._finish_walk_session(
            conn, state, worker_timeout=0.0) is False
        assert store_mod.get_walk(conn, walk).ended_at is None
        assert "still running" in capsys.readouterr().err
    finally:
        conn.close()


def test_live_comparison_table_includes_tx_rate(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        _lid, spot, _other, before, current = _seed_comparison_walks(conn)
        store_mod.add_reading(conn, spot, walk_id=before, tx_rate="400")
        state = tui_mod.WalkState(
            db, current_walk_id=current, comparison_window=2,
            traffic_fn=lambda: None)
        state.select_baseline_walk(conn, before)
        state.set_active_spot(spot)
    finally:
        conn.close()

    state.poll(read_fn=lambda: signal_mod.Signal(tx_rate="600"))
    state.poll(read_fn=lambda: signal_mod.Signal(tx_rate="800"))
    rendered = "\n".join(tui_mod.comparison_lines(state))
    assert "TX rate" in rendered
    assert "700" in rendered
    assert "400" in rendered
    assert "+300" in rendered


def test_empty_walks_are_not_selectable_and_latest_skips_them(tmp_path):
    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid, _rid, (spot, _other) = _seed_3level(conn)
        measured = store_mod.create_walk(conn, lid, "measured")
        store_mod.add_reading(conn, spot, walk_id=measured, rssi=-70)
        empty = store_mod.create_walk(conn, lid, "empty")
        current = store_mod.create_walk(conn, lid, "current")
        state = tui_mod.WalkState(db, current_walk_id=current)
        state.active_location_id = lid

        rows = tui_mod._baseline_walk_rows(conn, state)
        assert [walk_id for walk_id, _label in rows] == [measured, 0]
        with pytest.raises(ValueError, match="no readings"):
            state.select_baseline_walk(conn, empty)

        startup = tui_mod.WalkState(db)
        startup.active_location_id = lid
        tui_mod._start_walk_session(
            conn, startup, walk_name="after", compare_to="latest")
        assert startup.baseline_walk_id == measured
    finally:
        conn.close()


def test_baseline_picker_keeps_cursor_visible_beyond_nine_rows(tmp_path):
    import curses

    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        lid, _rid, (spot, _other) = _seed_3level(conn)
        walks = []
        for index in range(12):
            walk = store_mod.create_walk(conn, lid, "walk-%02d" % index)
            store_mod.add_reading(conn, spot, walk_id=walk, rssi=-70)
            walks.append(walk)
        current = store_mod.create_walk(conn, lid, "current")
        state = tui_mod.WalkState(db, current_walk_id=current)
        state.active_location_id = lid
        rows = tui_mod._baseline_walk_rows(conn, state)
        state.baseline_walk_id = rows[0][0]

        class SmallScreen:
            def __init__(self):
                self.keys = iter([curses.KEY_DOWN] * 10 + [10])
                self.current = []
                self.frames = []

            def timeout(self, _milliseconds):
                pass

            def getmaxyx(self):
                return (6, 80)

            def clear(self):
                self.current = []

            def addstr(self, row, col, text, *args):
                self.current.append(text)

            def refresh(self):
                self.frames.append(tuple(self.current))

            def getch(self):
                return next(self.keys)

        screen = SmallScreen()
        selected = tui_mod._baseline_picker_curses(
            screen, conn, state, poll_timeout_ms=10)
        assert selected == rows[10][0]
        assert any("#%d" % selected in line for line in screen.frames[-1])
        assert len(screen.frames[-1]) <= 5
    finally:
        conn.close()


def test_walk_curses_creates_and_finishes_named_walk(tmp_path, monkeypatch):
    class QuitScreen:
        def nodelay(self, _enabled):
            pass

        def timeout(self, _milliseconds):
            pass

        def clear(self):
            pass

        def getmaxyx(self):
            return (30, 120)

        def addstr(self, *_args):
            pass

        def refresh(self):
            pass

        def getch(self):
            return ord("q")

    db = _db(tmp_path)
    conn = store_mod.get_db(db)
    try:
        location_id = store_mod.create_location(conn, "home")
    finally:
        conn.close()
    monkeypatch.setattr(tui_mod.WalkState, "poll", lambda self: None)
    monkeypatch.setattr(
        tui_mod.WalkState, "ensure_identity", lambda self: None)
    monkeypatch.setattr(
        tui_mod.WalkState, "ensure_addrs", lambda self: None)

    result = tui_mod._walk_curses(
        QuitScreen(), db, interval=1.0, location_preset="home",
        no_speedtest=True, walk_name="before-install")

    assert result == 0
    conn = store_mod.get_db(db)
    try:
        walks = store_mod.list_walks(conn, location_id=location_id)
        assert len(walks) == 1
        assert walks[0].name == "before-install"
        assert walks[0].started_at
        assert walks[0].ended_at
    finally:
        conn.close()
