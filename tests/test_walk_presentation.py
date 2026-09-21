"""Walk history and comparison presentation, without live Wi-Fi."""
import pytest

from wifimap.walk_ui import SparkHistory, rate_rssi
from wifimap import store
from wifimap.walk_session import comparison_lines
from wifimap.walk_state import WalkState
from wifimap.walk_snapshot import SpeedProbeResult
from wifimap.signal import Signal
from wifimap.path_monitor import PathSample


def chart_text(history, lo=-90, hi=-30, width=4, height=3):
    return ["".join(text for text, _pair in row)
            for row in history.chart(lo, hi, width, height)]


def test_chart_uses_full_block_and_resolves_small_signal_changes():
    history = SparkHistory(maxlen=4)
    for value in (-60, -57, -54, -30):
        history.append(value)
    rows = chart_text(history)
    columns = list(zip(*rows))
    assert len(set(columns)) == 4
    assert columns[-1] == ("█", "█", "█")


def test_chart_resamples_whole_window_when_narrowed():
    history = SparkHistory(maxlen=8)
    for value in (-90, -90, -70, -70, -50, -50, -30, -30):
        history.append(value)
    reference = SparkHistory(maxlen=4)
    for value in (-90, -70, -50, -30):
        reference.append(value)
    assert chart_text(history) == chart_text(reference)


def test_chart_warmup_gaps_and_resizing_keep_time_positions():
    history = SparkHistory(maxlen=8)
    history.append(-30)
    history.append(None)
    history.append(-30)
    history.append(-30)
    rows = chart_text(history, width=8)
    assert rows == ["    █ ██"] * 3
    assert chart_text(history, width=16) == ["        ██  ████"] * 3


def test_chart_colors_belong_to_each_sample_not_latest_reading():
    history = SparkHistory(maxlen=3)
    for value in (-80, -65, -50):
        history.append(value)
    rows = history.chart(-90, -30, 3, 3, rating=rate_rssi)
    assert [pair for _text, pair in rows[-1]] == [3, 2, 1]


def test_chart_snr_does_not_flatten_above_40():
    history = SparkHistory(maxlen=3)
    for value in (40, 46, 60):
        history.append(value)
    assert len(set(zip(*chart_text(history, 0, 60, 3)))) == 3


def test_chart_empty_and_zero_width_are_safe():
    history = SparkHistory()
    assert chart_text(history) == ["    "] * 3
    assert chart_text(history, width=0) == [""] * 3


def compared_state(tmp_path):
    state = WalkState(str(tmp_path / "walk.db"), traffic_fn=lambda: None)
    conn = store.get_db(state.db_path)
    try:
        location = store.create_location(conn, "office")
        room = store.create_room(conn, location, "room")
        spot = store.create_spot(conn, room, "desk")
        before = store.create_walk(conn, location, "before mesh")
        for server in ("Berlin", "Hamburg"):
            store.add_reading(
                conn, spot, walk_id=before, ts="2026-09-01T12:00:00+00:00",
                rssi=-74, noise=-96, snr=22, tx_rate="158",
                ping_ms=40.5, down_mbps=58.5, up_mbps=16, server=server)
        state.active_location_id = location
        state.set_active_spot(spot)
        state.select_baseline_walk(conn, before)
    finally:
        conn.close()
    state.poll(read_fn=lambda: Signal(rssi=-46, noise=-92, snr=46, tx_rate="130"))
    state._speed_probe = SpeedProbeResult(
        True, spot, state._comparison_generation,
        ping_ms=7.1, down_mbps=92.7, up_mbps=33.2, server="Berlin")
    return state


def test_comparison_uses_specific_outcomes_and_delta_units(tmp_path):
    rendered = "\n".join(comparison_lines(compared_state(tmp_path)))
    assert "+28 dB" in rendered and "stronger" in rendered
    assert "+4 dB" in rendered and "noisier" in rendered
    assert "-28 Mbps" in rendered and "lower link rate" in rendered
    assert "-33.4 ms" in rendered and "less latency" in rendered
    assert "+34.2 Mbps" in rendered and "faster" in rendered


def test_comparison_exposes_baseline_date_count_and_mixed_servers(tmp_path):
    state = compared_state(tmp_path)
    rendered = "\n".join(comparison_lines(state))
    assert "2026-09-01" in rendered
    assert "2 readings" in rendered
    assert "2 speed tests" in rendered
    assert "Berlin" in rendered and "Hamburg" in rendered


def test_comparison_does_not_color_rounded_zero_as_an_improvement(tmp_path):
    from wifimap.walk_session import comparison_rows

    state = compared_state(tmp_path)
    state._speed_probe = SpeedProbeResult(
        True, state.active_spot_id, state._comparison_generation, ping_ms=40.46)
    ping_row = next(row for row in comparison_rows(state)
                    if row[0][0].startswith("ping "))
    assert ping_row[-1] == ("0.0 ms  unchanged", 0)


def test_saved_speed_test_retains_its_server_for_comparison(tmp_path, monkeypatch):
    from wifimap import signal, speed

    state = compared_state(tmp_path)
    monkeypatch.setattr(signal, "sample_signal", lambda: Signal(rssi=-46))
    worker = state.try_snapshot(run_speedtest_fn=lambda: speed.Speed(
        ping_ms=7, down_mbps=90, up_mbps=30, server="Munich"))
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert state.speed_probe_for_active_spot().server == "Munich"
    assert "now Munich" in "\n".join(comparison_lines(state))


class RecordingScreen:
    def __init__(self, height, width):
        self.height, self.width = height, width
        self.overflows = []
        self.clear()

    def clear(self):
        self.cells = [[(" ", 0)] * self.width for _ in range(self.height)]

    def nodelay(self, enabled):
        pass

    def timeout(self, milliseconds):
        pass

    def getmaxyx(self):
        return self.height, self.width

    def addstr(self, row, col, text, attr=0):
        if row >= self.height or col + len(text) > self.width:
            self.overflows.append((row, col, text))
        for offset, char in enumerate(text):
            self.cells[row][col + offset] = (char, attr)

    def refresh(self):
        pass

    def getch(self):
        return ord("q")

    def text(self):
        return "\n".join("".join(char for char, _attr in row).rstrip()
                         for row in self.cells)


def render_walk(state, monkeypatch, height=44, width=98):
    import curses
    from wifimap.walk_curses import _walk_curses

    monkeypatch.setattr(state, "poll", lambda: None)
    monkeypatch.setattr(state, "ensure_identity", lambda: None)
    monkeypatch.setattr(state, "ensure_addrs", lambda: None)
    monkeypatch.setattr(state, "refresh_benchmark", lambda conn: None)
    monkeypatch.setattr(state, "start_path_monitor", lambda: None)
    monkeypatch.setattr(state, "stop_path_monitor", lambda: None)
    for method in ("start_color", "use_default_colors", "init_pair", "curs_set"):
        monkeypatch.setattr(curses, method, lambda *args: None)
    monkeypatch.setattr(curses, "has_colors", lambda: True)
    monkeypatch.setattr(curses, "color_pair", lambda pair: pair << 8)
    screen = RecordingScreen(height, width)
    assert _walk_curses(screen, state.db_path, 1, None, False,
                        state_factory=lambda *args, **kwargs: state) == 0
    return screen


@pytest.mark.parametrize("height,width", [(44, 98), (36, 80), (36, 160)])
def test_walk_layout_preserves_metrics_benchmark_and_footer(
        tmp_path, monkeypatch, height, width):
    state = compared_state(tmp_path)
    state.net_ssid = "io-net"
    state.net_bssid = state.sig.bssid = "60:8d:26:8d:cf:3d"
    state.ip, state.router, state.mac = "192.0.2.20", "192.0.2.1", "aa:bb:cc:dd:ee:ff"
    state.benchmark = dict(rssi=-40, snr=40, down_mbps=40.6, up_mbps=33.8,
                           ts="2026-08-01T00:00:00Z", note="Near router", server="Berlin")
    state.last_result = "saved #17 down 92.7 up 33.2 Mbps"
    state.record_path_sample(PathSample(
        "192.0.2.1", 3.0, "1.1.1.1", 18.0))
    state.record_path_sample(PathSample(
        "192.0.2.1", 5.0, "1.1.1.1", None, primary_failed=True))
    for value in (-80, -65, -50) * 20:
        state.hist_rssi.append(value)
        state.hist_snr.append(value + 92)
        state.hist_noise.append(-92)
        state.hist_down.append(12)
        state.hist_up.append(3)
    state.last_rates = (12, 3)
    screen = render_walk(state, monkeypatch, height, width)
    rendered = screen.text()
    for text in ("RSSI", "SNR", "noise", "AP", "60:8d:26:8d:cf:3d",
                 "PATH  router", "internet", "p95", "loss", "×",
                 "traffic down", "traffic up", "LINK",
                 "COMPARE", "TX rate", "ping", "+34.2 Mbps", "+17.2 Mbps",
                 "BENCHMARK", "40.6", "33.8", "saved #17", "[s] save"):
        assert text in rendered, rendered
    assert not screen.overflows
    import curses
    footer_attrs = [attr for _char, attr in screen.cells[height - 2]]
    assert all(attr & curses.A_REVERSE for attr in footer_attrs)
    if height == 44:
        colors = {attr & 0xff00 for row in screen.cells for _char, attr in row}
        assert {1 << 8, 2 << 8, 3 << 8} <= colors
