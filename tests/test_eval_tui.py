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


def test_dashboard_keys_change_metric_order_mode_and_cursor():
    state = _state()
    state.press("enter")
    state.press("enter")
    assert state.metric.key == "rssi"
    assert state.grouped is True
    assert state.reverse is False

    state.press("tab")
    state.press("r")
    state.press("m")
    state.press("down")

    assert state.metric.key == "noise"
    assert state.reverse is True
    assert state.grouped is False
    assert state.cursor == 1


def test_v_does_not_change_the_dashboard_mode():
    state = _state()
    state.press("enter")
    state.press("enter")

    state.press("v")

    assert state.grouped is True


def test_shift_tab_cycles_metrics_backward_with_wraparound():
    state = _state()
    state.press("enter")
    state.press("enter")

    state.press("shift-tab")
    assert state.metric.key == "delta_up_mbps"

    state.press("tab")
    assert state.metric.key == "rssi"


def test_tab_cycles_through_noise_snr_and_tx_rate():
    state = _state()
    state.press("enter")
    state.press("enter")

    state.press("tab")
    assert state.metric.key == "noise"
    state.press("tab")
    assert state.metric.key == "snr"
    state.press("tab")
    assert state.metric.key == "tx_rate"


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
    assert any("mode: spot summary" in line for line in wide)
    assert any("m mode" in line and "Enter details" in line for line in wide)
    assert all(len(line) <= 120 for line in wide)
    assert all(len(line) <= 55 for line in narrow)
    assert any("keys:" in line for line in narrow)


def test_dashboard_documents_compare_key_at_standard_width():
    state = _state()
    state.press("enter")
    state.press("enter")

    lines = [line.text for line in eval_tui.render(state, 80, 24)]

    assert any("c compare" in line for line in lines)
    assert any("q quit" in line for line in lines)


def test_wide_dashboard_shows_the_focused_noise_and_tx_rate_values():
    report = evaluation.Report(
        source="db/wifi-map.db",
        readings=(_reading(1, noise=-92, tx_rate=433),),
    )
    state = eval_tui.EvalState(report)
    state.press("enter")
    state.press("enter")

    state.press("tab")
    noise_lines = [
        line.text for line in eval_tui.render(state, 120, 24)]
    assert any("focus: noise" in line for line in noise_lines)
    assert any("-92 dBm" in line for line in noise_lines)

    state.press("tab")
    state.press("tab")
    tx_lines = [line.text for line in eval_tui.render(state, 120, 24)]
    assert any("focus: TX rate" in line for line in tx_lines)
    assert any("433 Mbps" in line for line in tx_lines)


def test_wide_dashboard_puts_one_focus_column_before_context_metrics():
    report = evaluation.Report(
        source="db/wifi-map.db",
        readings=(_reading(
            1, room="arbeitszimmer", spot="schreibtisch"),),
    )
    state = eval_tui.EvalState(report)
    state.press("enter")
    state.press("enter")

    rssi_lines = [
        line.text for line in eval_tui.render(state, 120, 24)]
    rssi_header = next(
        line for line in rssi_lines if "room / floor / spot" in line)

    rssi_context = rssi_header.split("│", 2)[2]
    assert "RSSI" not in rssi_context.split()
    assert rssi_header.index("FOCUS: RSSI") < rssi_header.index("SNR")
    table_rows = [line for line in rssi_lines
                  if "│" in line and "FOCUS:" not in line]
    assert any("arbeitszimmer / 0 / schreibtisch" in line
               for line in table_rows)

    state.press("tab")
    noise_lines = [
        line.text for line in eval_tui.render(state, 120, 24)]
    noise_header = next(
        line for line in noise_lines if "room / floor / spot" in line)

    assert noise_header.count("noise") == 1
    assert noise_header.index("FOCUS: noise") < noise_header.index("RSSI")


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


def test_enter_opens_spot_history_with_all_stored_reading_data():
    report = evaluation.Report(
        source="db/wifi-map.db",
        readings=(_reading(
            1, bssid="aa:bb:cc", ap_name="Kitchen mesh",
            channel="36 (160MHz)", phy="802.11ac",
            tx_rate=650, server="nearby (42)", note="router-side",
            noise=-93, snr=33, down_mbps=210.5, up_mbps=36.2,
            delta_rssi=-12, delta_snr=-8,
            delta_down_mbps=-69.5, delta_up_mbps=1.2,
            path_probe_count=60,
            gateway_rtt_ms=3, gateway_p95_ms=6,
            gateway_loss_pct=0, gateway_max_outage_ms=0,
            internet_rtt_ms=18, internet_p95_ms=31,
            internet_loss_pct=2, internet_max_outage_ms=1000),),
    )
    state = eval_tui.EvalState(report)
    state.press("enter")
    state.press("enter")
    state.press("enter")

    lines = [line.text for line in eval_tui.render(state, 140, 30)]
    output = "\n".join(lines)

    assert state.screen == "detail"
    assert "Reading history" in output
    assert "home / kitchen / 0 / window" in output
    assert "SSID home-net" in output
    assert "AP Kitchen mesh · aa:bb:cc" in output
    assert "channel 36 (160MHz)" in output and "PHY 802.11ac" in output
    assert "tx 650" in output
    assert "noise -93" in output and "SNR 33" in output
    assert "ΔRSSI -12" in output and "ΔSNR -8" in output
    assert "ping 20.0" in output and "down 210.5" in output
    assert "up 36.2" in output and "server nearby (42)" in output
    assert "path router: median 3 ms | p95 6 ms | loss 0%" in output
    assert "path internet: median 18 ms | p95 31 ms | loss 2%" in output
    assert output.count("60 probes") == 2
    assert "note: router-side" in output


def test_spot_history_contains_each_reading_newest_first():
    report = evaluation.Report(
        source="db/wifi-map.db",
        readings=(
            _reading(1, channel="36", bssid="aa:01", rssi=-70),
            _reading(2, channel="44", bssid="aa:02", rssi=-55),
        ),
    )
    state = eval_tui.EvalState(report)
    state.press("enter")
    state.press("enter")
    state.press("enter")

    assert state.screen == "detail"
    assert len(state.detail_readings) == 2
    assert state.detail_readings[0].id == 2

    output = "\n".join(
        line.text for line in eval_tui.render(state, 140, 30))
    assert "2 readings" in output
    assert "channel 44" in output
    assert "AP aa:02" in output

    state.press("down")
    output = "\n".join(
        line.text for line in eval_tui.render(state, 140, 30))
    assert state.detail_index == 1
    assert "channel 36" in output
    assert "AP aa:01" in output

    state.press("esc")
    assert state.screen == "dashboard"


def test_individual_reading_details_do_not_expand_to_whole_spot():
    report = evaluation.Report(
        source="db/wifi-map.db",
        readings=(
            _reading(1, channel="36"),
            _reading(2, channel="44"),
        ),
    )
    state = eval_tui.EvalState(report)
    state.press("enter")
    state.press("enter")
    state.press("m")
    state.press("enter")

    assert state.screen == "detail"
    assert len(state.detail_readings) == 1


def test_wide_rating_follows_the_focused_metric():
    state = _state()
    state.press("enter")
    state.press("enter")
    state.press("tab")  # noise
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


def _walk_reading(row_id, walk_id, walk_name, started_at, *, spot_id,
                  spot, rssi, noise=-92, snr=30, tx_rate=400,
                  ping=20, down=200, up=35, **overrides):
    return _reading(
        row_id, spot=spot, rssi=rssi, noise=noise, snr=snr,
        tx_rate=tx_rate, ping=ping, down_mbps=down, up_mbps=up,
        spot_id=spot_id, walk_id=walk_id, walk_name=walk_name,
        walk_started_at=started_at,
        walk_ended_at=started_at.replace("09:00", "09:30"),
        **overrides
    )


def _comparison_state():
    report = evaluation.Report(
        source="db/wifi-map.db",
        readings=(
            _walk_reading(
                1, 10, "before mesh", "2026-09-20T09:00:00+00:00",
                spot_id=1, spot="window", rssi=-80, channel="36",
                bssid="aa:01", note="baseline one"),
            _walk_reading(
                2, 10, "before mesh", "2026-09-20T09:00:00+00:00",
                spot_id=1, spot="window", rssi=-70, channel="36",
                bssid="aa:02", note="baseline two"),
            _walk_reading(
                3, 10, "before mesh", "2026-09-20T09:00:00+00:00",
                spot_id=2, spot="old corner", rssi=-85),
            _walk_reading(
                4, 20, "after mesh", "2026-09-21T09:00:00+00:00",
                spot_id=1, spot="window", rssi=-55, noise=-95, snr=40,
                tx_rate=800, ping=10, down=400, up=70, channel="44",
                bssid="bb:01", phy="802.11ax", server="near (1)",
                note="mesh installed"),
            _walk_reading(
                5, 20, "after mesh", "2026-09-21T09:00:00+00:00",
                spot_id=3, spot="new corner", rssi=-65),
        ),
    )
    state = eval_tui.EvalState(report)
    state.press("enter")
    state.press("enter")
    return state


def _enter_comparison(state):
    state.press("c")
    assert state.screen == "compare_before"
    state.press("enter")
    assert state.screen == "compare_after"
    state.press("enter")
    assert state.screen == "compare_dashboard"


def test_compare_flow_selects_baseline_then_candidate_from_current_scope():
    state = _comparison_state()

    state.press("c")

    assert state.screen == "compare_before"
    assert [walk.name for walk in state.walks] == [
        "after mesh", "before mesh"]
    assert state.walks[state.walk_index].name == "before mesh"

    state.press("enter")
    assert state.screen == "compare_after"
    assert [walk.name for walk in state.walk_candidates] == ["after mesh"]

    state.press("enter")
    assert state.screen == "compare_dashboard"
    assert state.before_walk.name == "before mesh"
    assert state.after_walk.name == "after mesh"
    assert {row.status for row in state.comparison_rows} == {"matched"}


def test_comparison_defaults_to_matched_spots_and_can_show_all_spots():
    state = _comparison_state()
    _enter_comparison(state)

    assert state.comparison_show_all is False
    assert [row.status for row in state.comparison_rows] == ["matched"]

    state.press("f")

    assert state.comparison_show_all is True
    assert {row.status for row in state.comparison_rows} == {
        "matched", "before_only", "after_only"}


def test_compare_selector_has_actionable_empty_state_for_too_few_walks():
    state = _state()
    state.press("enter")
    state.press("enter")

    state.press("c")
    lines = [line.text for line in eval_tui.render(state, 80, 20)]

    assert state.screen == "compare_before"
    assert any("two named walks" in line.lower() for line in lines)
    state.press("enter")
    assert state.screen == "compare_before"


def test_comparison_keys_cycle_raw_metrics_reorder_swap_and_return():
    state = _comparison_state()
    _enter_comparison(state)

    assert state.comparison_metric.key == "rssi"
    state.press("shift-tab")
    assert state.comparison_metric.key == "up_mbps"
    state.press("tab")
    state.press("tab")
    assert state.comparison_metric.key == "noise"

    state.press("r")
    assert state.comparison_reverse is True

    before_id = state.before_walk.walk_id
    after_id = state.after_walk.walk_id
    state.press("x")
    assert state.before_walk.walk_id == after_id
    assert state.after_walk.walk_id == before_id

    state.press("c")
    assert state.screen == "compare_before"
    state.press("esc")
    assert state.screen == "dashboard"


def test_render_comparison_dashboard_shows_coverage_changes_and_statuses():
    state = _comparison_state()
    _enter_comparison(state)

    wide = [line.text for line in eval_tui.render(state, 120, 28)]
    narrow = [line.text for line in eval_tui.render(state, 54, 20)]
    output = "\n".join(wide)

    assert "before mesh" in output and "after mesh" in output
    assert "1 matched" in output
    assert "1 not revisited" in output
    assert "1 new" in output
    assert "BETTER" in output
    assert "-75" in output and "-55" in output and "+20" in output
    assert "NOT REVISITED" not in output
    assert "NEW" not in output
    assert any("f all spots" in line and "x swap" in line for line in wide)
    state.press("f")
    all_spots = "\n".join(
        line.text for line in eval_tui.render(state, 120, 28))
    assert "NOT REVISITED" in all_spots
    assert "NEW" in all_spots
    assert all(len(line) <= 120 for line in wide)
    assert all(len(line) <= 54 for line in narrow)


def test_standard_width_comparison_uses_one_aligned_row_per_place():
    state = _comparison_state()
    _enter_comparison(state)
    matched = next(
        row for row in state.comparison_rows if row.status == "matched")
    object.__setattr__(matched.place, "room_name", "arbeitszimmer")
    object.__setattr__(matched.place, "spot_name", "schreibtisch")
    state.comparison_cursor = state.comparison_rows.index(matched)

    lines = eval_tui.render(state, 80, 24)
    output = "\n".join(line.text for line in lines)

    assert "missing 0" in output
    selected_rows = [
        line for line in lines
        if "arbeitszimmer / 0 / schreibtisch" in line.text
    ]
    assert len(selected_rows) == 1
    selected = selected_rows[0]
    assert selected.text.startswith(">")
    assert "-75" in selected.text
    assert "-55" in selected.text
    assert "+20" in selected.text
    assert "BETTER" in selected.text
    assert selected.style == "great"


def test_narrow_comparison_preserves_room_floor_and_spot_context():
    state = _comparison_state()
    _enter_comparison(state)
    matched = next(
        row for row in state.comparison_rows if row.status == "matched")
    object.__setattr__(
        matched.place, "room_name",
        "extraordinarily-long-conference-room-name")
    object.__setattr__(matched.place, "spot_name", "window-seat")

    lines = eval_tui.render(state, 54, 20)
    place_line = next(
        line.text for line in lines if "window-seat" in line.text)

    assert "extraordinarily" in place_line
    assert " / 0 / window-seat" in place_line


def test_comparison_detail_shows_all_metrics_both_histories_and_full_reading():
    state = _comparison_state()
    _enter_comparison(state)
    matched_index = next(
        index for index, row in enumerate(state.comparison_rows)
        if row.status == "matched")
    state.comparison_cursor = matched_index

    state.press("enter")
    lines = [line.text for line in eval_tui.render(state, 140, 42)]
    output = "\n".join(lines)

    assert state.screen == "compare_detail"
    for label in ("RSSI", "noise", "SNR", "TX rate", "ping", "down", "up"):
        assert label in output
    assert "Before history — before mesh" in output
    assert "After history — after mesh" in output
    assert "channel 36" in output
    assert "AP aa:02" in output
    assert "baseline two" in output

    state.press("right")
    output = "\n".join(
        line.text for line in eval_tui.render(state, 140, 42))
    assert state.comparison_detail_side == "after"
    assert "channel 44" in output
    assert "AP bb:01" in output
    assert "mesh installed" in output

    state.press("esc")
    assert state.screen == "compare_dashboard"


def test_comparison_detail_labels_not_revisited_and_new_spots():
    state = _comparison_state()
    _enter_comparison(state)
    state.press("f")

    old_index = next(
        index for index, row in enumerate(state.comparison_rows)
        if row.status == "before_only")
    state.comparison_cursor = old_index
    state.press("enter")
    old_output = "\n".join(
        line.text for line in eval_tui.render(state, 100, 32))
    assert "NOT REVISITED" in old_output
    assert "No after readings" in old_output

    state.press("esc")
    new_index = next(
        index for index, row in enumerate(state.comparison_rows)
        if row.status == "after_only")
    state.comparison_cursor = new_index
    state.press("enter")
    new_output = "\n".join(
        line.text for line in eval_tui.render(state, 100, 32))
    assert "NEW" in new_output
    assert "No before readings" in new_output


def test_ssid_comparison_candidates_stay_with_baseline_location():
    readings = list(_comparison_state().report.readings)
    readings.append(_walk_reading(
        6, 30, "office walk", "2026-09-22T09:00:00+00:00",
        spot_id=30, spot="desk", rssi=-60,
        location="office", location_id=2, room="workspace"))
    state = eval_tui.EvalState(evaluation.Report(
        source="db/wifi-map.db", readings=tuple(readings)))
    state.press("down")  # SSID
    state.press("enter")
    state.press("enter")  # home-net

    state.press("c")
    selector = "\n".join(
        line.text for line in eval_tui.render(state, 100, 24))
    assert "before mesh" in selector and "home" in selector
    assert "office walk" in selector and "office" in selector

    state.press("enter")  # oldest: home / before mesh

    assert state.before_walk.location_name == "home"
    assert [walk.name for walk in state.walk_candidates] == ["after mesh"]
    candidate_output = "\n".join(
        line.text for line in eval_tui.render(state, 100, 24))
    assert "location: home" in candidate_output
    assert "office walk" not in candidate_output


def test_ssid_comparison_explains_when_location_has_no_candidate():
    report = evaluation.Report(
        source="db/wifi-map.db",
        readings=(
            _walk_reading(
                1, 10, "home only", "2026-09-20T09:00:00+00:00",
                spot_id=1, spot="window", rssi=-70),
            _walk_reading(
                2, 20, "office only", "2026-09-21T09:00:00+00:00",
                spot_id=2, spot="desk", rssi=-60,
                location="office", location_id=2, room="workspace"),
        ),
    )
    state = eval_tui.EvalState(report)
    state.press("down")
    state.press("enter")
    state.press("enter")
    state.press("c")
    state.press("enter")

    output = "\n".join(
        line.text for line in eval_tui.render(state, 90, 20))
    assert state.screen == "compare_after"
    assert "No other named walk for location home" in output
    state.press("enter")
    assert state.screen == "compare_after"


def test_walk_selectors_scroll_to_keep_cursor_visible():
    readings = tuple(
        _walk_reading(
            index, 100 + index, "walk-%02d" % index,
            "2026-09-%02dT09:00:00+00:00" % (index + 1),
            spot_id=1, spot="window", rssi=-70 + index,
        )
        for index in range(12)
    )
    state = eval_tui.EvalState(evaluation.Report(
        source="db/wifi-map.db", readings=readings))
    state.press("enter")
    state.press("enter")
    state.press("c")

    before_lines = eval_tui.render(state, 80, 10)
    assert any("walk-00" in line.text for line in before_lines)
    assert any("walk-00" in line.text and line.style == "selected"
               for line in before_lines)
    assert any("keys:" in line.text for line in before_lines)

    state.press("enter")
    state.press("end")
    after_lines = eval_tui.render(state, 80, 10)
    selected_after = state.walk_candidates[state.candidate_index].name
    assert any(selected_after in line.text and line.style == "selected"
               for line in after_lines)
    assert any("keys:" in line.text for line in after_lines)


def test_compact_comparison_detail_pages_make_all_content_reachable():
    state = _comparison_state()
    _enter_comparison(state)
    state.comparison_cursor = next(
        index for index, row in enumerate(state.comparison_rows)
        if row.status == "matched")
    state.press("enter")

    metrics = [line.text for line in eval_tui.render(state, 80, 24)]
    metric_output = "\n".join(metrics)
    for label in ("RSSI", "noise", "SNR", "TX rate", "ping", "down", "up"):
        assert label in metric_output
    assert any("section" in line and "Tab/Shift+Tab" in line
               for line in metrics)

    state.press("tab")
    histories = [line.text for line in eval_tui.render(state, 80, 24)]
    history_output = "\n".join(histories)
    assert "Before history — before mesh" in history_output
    assert "After history — after mesh" in history_output
    assert "AP aa:02" in history_output
    assert any("section" in line and "Tab/Shift+Tab" in line
               for line in histories)

    state.press("tab")
    detail = [line.text for line in eval_tui.render(state, 80, 24)]
    detail_output = "\n".join(detail)
    assert "Selected before reading" in detail_output
    for content in ("network:", "radio:", "signal:", "speed:", "note:"):
        assert content in detail_output
    assert any("section" in line and "Tab/Shift+Tab" in line
               for line in detail)
    assert all(len(line) <= 80 for line in metrics + histories + detail)

    state.press("shift-tab")
    assert state.comparison_detail_page == 1
