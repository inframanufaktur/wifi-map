"""Walk TUI: live signal poll + snapshot/location keys (stdlib only).

Thread safety (M1 concern): ``store.list_readings`` toggles
``conn.row_factory`` on the passed connection, so connections are NOT
thread-safe to share. The snapshot background thread MUST open its own
``store.get_db(db_path)`` connection and close it; it never touches the
UI thread's connection. ``WalkState`` toast/pending mutations from worker
callbacks and UI-thread reads are serialized by ``WalkState._lock``.
"""
from __future__ import annotations

import shutil
import sqlite3
import statistics
import sys
import threading
import time
from collections import deque
from typing import Callable, Dict, List, Optional, Tuple, Union

from wifimap import signal as signal_mod
from wifimap import ssid as ssid_mod
from wifimap import speed as speed_mod
from wifimap import store as store_mod
from wifimap import traffic as traffic_mod
from wifimap.walk_picker import (
    KEY_CREATE,
    KEY_ENTER_CODES,
    KEY_ESC,
    PICKER_TIMEOUT_RESTORE_MS,
    PickerSelection,
    _active_room_id,
    _create_room_curses,
    _create_spot_curses,
    _fallback_create,
    _fallback_create_room,
    _fallback_create_spot,
    _fallback_floor,
    _fallback_pick,
    _fallback_pick_level,
    _find_room_by_name,
    _list_picker_curses,
    _pick_room_spot_curses,
    _prompt_curses,
    _room_picker_curses,
    _spot_picker_curses,
)
from wifimap.walk_keys import (
    DB_OPEN_FAIL_SLEEP,
    FALLBACK_SETTLE_SLEEP,
    KEY_BENCHMARK,
    KEY_COMPARE,
    KEY_CTRL_C,
    KEY_FLOOR,
    KEY_NEW,
    KEY_NO_INPUT,
    KEY_QUIT,
    KEY_QUIT_UPPER,
    KEY_SNAPSHOT,
    KEY_SPEED_PROBE,
    KEY_SWITCH,
    QUIT_WORDS,
    WORKER_DRAIN_TIMEOUT,
    poll_timeout_ms as _poll_timeout_ms,
)
from wifimap.walk_curses import (
    _baseline_picker_curses,
    _walk_curses as _walk_curses_impl,
)
from wifimap.walk_fallback import (
    _baseline_picker_fallback,
    _walk_fallback as _walk_fallback_impl,
)
from wifimap.walk_snapshot import (
    MetricComparison,
    SnapshotResult,
    SpeedProbeResult,
    SpotBaseline,
    _finish_benchmark,
    _sample_guarded,
    finish_snapshot,
    start_snapshot_thread,
)
from wifimap.walk_state import (
    WalkState as _BaseWalkState,
)
from wifimap.walk_baseline import nonempty_walk_ids as _nonempty_walk_ids
from wifimap.walk_session import (
    _baseline_walk_rows,
    _finish_walk_session,
    _location_label,
    _resolve_preset,
    _start_walk_session,
    comparison_lines,
)
from wifimap.walk_ui import (
    SparkHistory,
    ansi_wrap,
    attempt_read,
    fmt_mbps,
    fmt_rate_val,
    format_addr_line,
    format_extra_line,
    format_meter_left,
    format_meter_row,
    format_net_line,
    format_radio_line,
    format_signal_line,
    graph_width,
    grouped_graph_width,
    history_cap,
    layout_mode,
    meter_layout,
    parse_floor_input,
    picker_move,
    picker_press,
    picker_start_cursor,
    rate_rssi,
    rate_snr,
    rating_style,
    snapshot_payload,
    wide_graph_width,
)
from wifimap.walk_ui import METER_LABEL_W, METER_VAL_W, WIDE_MIN_WIDTH


class WalkState(_BaseWalkState):
    """Walk state with the legacy snapshot-thread patch point."""

    def _start_snapshot_thread(self, *args, **kwargs):
        return start_snapshot_thread(*args, **kwargs)




# ---------------------------------------------------------------------------
# Curses loop
# ---------------------------------------------------------------------------

def _walk_curses(stdscr: object, db_path: str, interval: float,
                 location_preset: Optional[str],
                 no_speedtest: bool,
                 ssid: Optional[str] = None,
                 walk_name: Optional[str] = None,
                 compare_to: Optional[Union[int, str]] = None,
                 ssid_id: Optional[int] = None,
                 network_bssid: Optional[str] = None) -> int:
    return _walk_curses_impl(
        stdscr, db_path, interval, location_preset, no_speedtest,
        ssid, walk_name, compare_to,
        ssid_id=ssid_id, network_bssid=network_bssid,
        state_factory=WalkState,
    )


# ---------------------------------------------------------------------------
# Non-curses fallback (plain ANSI; blocking-input limit documented)
# ---------------------------------------------------------------------------

def _walk_fallback(db_path: str, interval: float,
                   location_preset: Optional[str],
                   no_speedtest: bool,
                   ssid: Optional[str] = None,
                   walk_name: Optional[str] = None,
                   compare_to: Optional[Union[int, str]] = None,
                   ssid_id: Optional[int] = None,
                   network_bssid: Optional[str] = None) -> int:
    return _walk_fallback_impl(
        db_path, interval, location_preset, no_speedtest,
        ssid, walk_name, compare_to,
        ssid_id=ssid_id, network_bssid=network_bssid,
        state_factory=WalkState,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _select_walk_location(
    conn: sqlite3.Connection,
    requested: Optional[str],
    input_fn: Optional[Callable[[str], str]] = None,
) -> int:
    """Resolve a preset or select/create a location before the walk UI."""
    if requested is not None:
        return store_mod.resolve_location(conn, requested)
    if not sys.stdin.isatty():
        raise ValueError(
            "location selection requires a terminal; pass --location NAME")
    read = input_fn or input
    while True:
        locations = store_mod.list_locations(conn)
        print("Select location:")
        for index, location in enumerate(locations, start=1):
            print("%d. %s" % (index, location.name))
        print("%d. Create location" % (len(locations) + 1))
        try:
            raw = read("Location [number, q cancel]: ").strip()
        except (EOFError, OSError):
            raise ssid_mod.SSIDSelectionCancelled(
                "location selection cancelled")
        if raw.lower() in QUIT_WORDS:
            raise ssid_mod.SSIDSelectionCancelled(
                "location selection cancelled")
        try:
            index = int(raw) - 1
        except ValueError:
            continue
        if 0 <= index < len(locations):
            return locations[index].id
        if index != len(locations):
            continue
        try:
            name = read("Location name: ").strip()
        except (EOFError, OSError):
            raise ssid_mod.SSIDSelectionCancelled(
                "location selection cancelled")
        if name:
            return store_mod.resolve_location(conn, name)


def _prepare_walk_selection(
    db_path: str,
    location_preset: Optional[str],
    requested_ssid: Optional[str],
) -> Tuple[str, store_mod.SSID, Optional[str]]:
    """Resolve the location and its SSID before curses takes the terminal."""
    conn = store_mod.get_db(db_path)
    try:
        location_id = _select_walk_location(conn, location_preset)
        identity = None
        if requested_ssid is None:
            try:
                identity = signal_mod.read_network_identity()
            except Exception:  # noqa: BLE001 - detection is best-effort
                identity = None
        selected = ssid_mod.select_ssid_line(
            conn,
            location_id,
            detected_name=identity[0] if identity is not None else None,
            requested=requested_ssid,
        )
        return (
            str(location_id),
            selected,
            identity[1] if identity is not None else None,
        )
    finally:
        conn.close()


def run_walk(db_path: str, interval: float = 1.0,
             location_preset: Optional[str] = None,
             no_speedtest: bool = False,
             ssid: Optional[str] = None,
             walk_name: Optional[str] = None,
             compare_to: Optional[Union[int, str]] = None) -> int:
    """Run walk loop; curses when tty available else ANSI fallback."""
    if interval <= 0:
        print("Error: --interval must be > 0", file=sys.stderr)
        return 3
    if ssid is not None and not ssid.strip():
        print("Error: --ssid must not be blank", file=sys.stderr)
        return 3
    try:
        location_preset, selected_ssid, network_bssid = (
            _prepare_walk_selection(db_path, location_preset, ssid))
    except ssid_mod.SSIDSelectionCancelled as exc:
        print(str(exc), file=sys.stderr)
        return 0
    except (sqlite3.Error, OSError, ValueError) as exc:
        print("Error: cannot prepare walk: %s" % exc, file=sys.stderr)
        return 3
    ssid = selected_ssid.name
    try:
        import curses  # noqa: F401
        use_curses = sys.stdin.isatty() and sys.stdout.isatty()
    except ImportError:
        use_curses = False
    if use_curses:
        import curses as _c

        def _main(stdscr: object) -> int:
            return _walk_curses(
                stdscr, db_path, interval, location_preset, no_speedtest,
                ssid, walk_name, compare_to,
                ssid_id=selected_ssid.id, network_bssid=network_bssid)

        try:
            return _c.wrapper(_main)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # curses init failed → fallback
            print("curses unavailable (%s); using fallback" % (exc,),
                  file=sys.stderr)
    return _walk_fallback(
        db_path, interval, location_preset, no_speedtest, ssid,
        walk_name, compare_to,
        ssid_id=selected_ssid.id, network_bssid=network_bssid)


__all__ = [
    "WalkState",
    "SnapshotResult",
    "SpotBaseline",
    "MetricComparison",
    "SpeedProbeResult",
    "PickerSelection",
    "QUIT_WORDS",
    "KEY_SNAPSHOT",
    "KEY_BENCHMARK",
    "KEY_SWITCH",
    "KEY_NEW",
    "KEY_FLOOR",
    "KEY_COMPARE",
    "KEY_SPEED_PROBE",
    "KEY_QUIT",
    "KEY_CREATE",
    "attempt_read",
    "ansi_wrap",
    "comparison_lines",
    "finish_snapshot",
    "fmt_mbps",
    "fmt_rate_val",
    "format_extra_line",
    "format_radio_line",
    "format_addr_line",
    "format_meter_left",
    "format_meter_row",
    "format_net_line",
    "format_signal_line",
    "history_cap",
    "graph_width",
    "wide_graph_width",
    "grouped_graph_width",
    "layout_mode",
    "meter_layout",
    "parse_floor_input",
    "picker_move",
    "picker_press",
    "picker_start_cursor",
    "rate_rssi",
    "rate_snr",
    "rating_style",
    "snapshot_payload",
    "SparkHistory",
    "start_snapshot_thread",
    "METER_LABEL_W",
    "METER_VAL_W",
    "WIDE_MIN_WIDTH",
    "run_walk",
]
