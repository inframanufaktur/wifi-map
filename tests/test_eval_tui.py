"""Pure state and rendering tests for the evaluation TUI."""
from __future__ import annotations

from wifimap import eval_tui
from wifimap import evaluation


def _reading(row_id, location="home", room="kitchen", spot="window",
             ssid="home-net", rssi=-60, ping=20, **overrides):
    row = {field: "" for field in evaluation.REQUIRED_FIELDS}
    row.update({
        "id": row_id,
        "ts": "2026-09-21T10:%02d:00+00:00" % row_id,
        "spot_id": row_id,
        "room_id": 1,
        "location_id": 1,
        "location_name": location,
        "room_name": room,
        "spot_name": spot,
        "floor": 0,
        "outdoors": 0,
        "ssid": ssid,
        "rssi": rssi,
        "noise": -92,
        "snr": 32,
        "ping_ms": ping,
        "down_mbps": 200,
        "up_mbps": 35,
        "delta_rssi": -10,
        "delta_snr": -8,
        "delta_down_mbps": -80,
        "delta_up_mbps": 0,
    })
    row.update(overrides)
    return evaluation.Reading.from_mapping(row)


def _state():
    report = evaluation.Report(
        source="db/wifi-map.db",
        readings=(
            _reading(1),
            _reading(2, room="office", spot="desk", ssid="work-net",
                     rssi=-75, ping=40),
            _reading(3, location="away", room="hall", spot="door",
                     ssid="", rssi=-85, ping=60),
        ),
    )
    return eval_tui.EvalState(report)


def test_startup_requires_location_or_ssid_then_a_value():
    state = _state()
    assert state.screen == "kind"

    assert state.press("enter") == "continue"
    assert state.screen == "scope"
    assert [option.label for option in state.scopes] == ["home", "away"]

    state.press("enter")
    assert state.screen == "dashboard"
    assert state.scope.label == "home"
    assert {row.location_name for row in state.rows} == {"home"}


def test_startup_can_select_unknown_ssid():
    state = _state()
    state.press("down")
    state.press("enter")
    assert [option.label for option in state.scopes] == [
        "home-net", "work-net", "Unknown"]

    state.press("end")
    state.press("enter")
    assert state.scope.label == "Unknown"
    assert len(state.rows) == 1
    assert state.rows[0].location_name == "away"


def test_escape_moves_back_through_selection_flow():
    state = _state()
    state.press("enter")
    state.press("enter")

    state.press("esc")
    assert state.screen == "scope"
    state.press("esc")
    assert state.screen == "kind"


def test_dashboard_keys_change_metric_order_view_and_cursor():
    state = _state()
    state.press("enter")
    state.press("enter")
    assert state.metric.key == "rssi"
    assert state.grouped is True
    assert state.reverse is False

    state.press("tab")
    state.press("r")
    state.press("v")
    state.press("down")

    assert state.metric.key == "snr"
    assert state.reverse is True
    assert state.grouped is False
    assert state.cursor == 1


def test_shift_tab_cycles_metrics_backward_with_wraparound():
    state = _state()
    state.press("enter")
    state.press("enter")

    state.press("shift-tab")
    assert state.metric.key == "delta_up_mbps"

    state.press("tab")
    assert state.metric.key == "rssi"


def test_curses_backtab_normalizes_to_shift_tab():
    class FakeCurses:
        KEY_UP = 1
        KEY_DOWN = 2
        KEY_HOME = 3
        KEY_END = 4
        KEY_ENTER = 5
        KEY_BTAB = 6

    assert eval_tui._normalize_key(6, FakeCurses) == "shift-tab"


def test_quit_is_available_from_every_screen():
    state = _state()
    assert state.press("q") == "quit"
    state.press("enter")
    assert state.press("q") == "quit"


def test_render_kind_and_scope_show_clear_instructions():
    state = _state()
    kind_lines = [line.text for line in eval_tui.render(state, 80, 20)]
    assert any("Evaluate by" in line for line in kind_lines)
    assert any("Location" in line for line in kind_lines)
    assert any("SSID" in line for line in kind_lines)

    state.press("enter")
    scope_lines = [line.text for line in eval_tui.render(state, 80, 20)]
    assert any("Choose Location" in line for line in scope_lines)
    assert any("home" in line and "2 readings" in line
               for line in scope_lines)


def test_render_dashboard_is_responsive_and_explains_ratings():
    state = _state()
    state.press("enter")
    state.press("enter")

    wide = [line.text for line in eval_tui.render(state, 120, 24)]
    narrow = [line.text for line in eval_tui.render(state, 55, 16)]

    assert any("Location: home" in line for line in wide)
    assert any("median" in line and "missing" in line for line in wide)
    assert any("RSSI" in line and "SNR" in line and "down" in line
               for line in wide)
    assert any("WEAK" in line or "GREAT" in line or "OK" in line
               for line in wide)
    assert all(len(line) <= 120 for line in wide)
    assert all(len(line) <= 55 for line in narrow)
    assert any("keys:" in line for line in narrow)


def test_dashboard_rows_show_room_floor_spot_in_all_layouts():
    state = _state()
    state.press("enter")
    state.press("enter")

    wide = [line.text for line in eval_tui.render(state, 120, 24)]
    narrow = [line.text for line in eval_tui.render(state, 70, 20)]

    assert any("room / floor / spot" in line for line in wide)
    assert any("office / 0 / desk" in line for line in wide)
    assert any("room / floor / spot" in line for line in narrow)
    assert any("office / 0 / desk" in line for line in narrow)


def test_selected_details_expose_all_stored_reading_data():
    report = evaluation.Report(
        source="db/wifi-map.db",
        readings=(_reading(
            1, bssid="aa:bb:cc", channel="36 (160MHz)", phy="802.11ac",
            tx_rate=650, server="nearby (42)", note="router-side",
            noise=-93, snr=33, down_mbps=210.5, up_mbps=36.2,
            delta_rssi=-12, delta_snr=-8,
            delta_down_mbps=-69.5, delta_up_mbps=1.2),),
    )
    state = eval_tui.EvalState(report)
    state.press("enter")
    state.press("enter")

    lines = [line.text for line in eval_tui.render(state, 140, 30)]
    output = "\n".join(lines)

    assert "home / kitchen / 0 / window" in output
    assert "SSID home-net" in output and "BSSID aa:bb:cc" in output
    assert "channel 36 (160MHz)" in output and "PHY 802.11ac" in output
    assert "tx 650" in output
    assert "noise -93" in output and "SNR 33" in output
    assert "ΔRSSI -12" in output and "ΔSNR -8" in output
    assert "ping 20.0" in output and "down 210.5" in output
    assert "up 36.2" in output and "server nearby (42)" in output
    assert "note: router-side" in output


def test_wide_rating_follows_the_focused_metric():
    state = _state()
    state.press("enter")
    state.press("enter")
    state.press("tab")  # SNR: both rows are GREAT, although one RSSI is WEAK

    lines = [line.text for line in eval_tui.render(state, 120, 24)]

    assert any("GREAT" in line for line in lines)
    assert not any("WEAK" in line for line in lines)


def test_render_empty_report_has_an_actionable_empty_state():
    state = eval_tui.EvalState(
        evaluation.Report(source="empty.csv", readings=()))

    lines = [line.text for line in eval_tui.render(state, 80, 20)]

    assert any("No readings" in line for line in lines)
    assert any("q" in line for line in lines)


def test_render_strips_terminal_control_characters_from_source_and_data():
    report = evaluation.Report(
        source="evil\x1b[2J.csv",
        readings=(_reading(
            1, location="home\nforged", room="kit\x1b[31mchen"),),
    )
    state = eval_tui.EvalState(report)

    kind_lines = eval_tui.render(state, 100, 20)
    state.press("enter")
    scope_lines = eval_tui.render(state, 100, 20)

    output = "\n".join(
        line.text for line in kind_lines + scope_lines)
    assert "\x1b" not in output
    assert "home\nforged" not in output
