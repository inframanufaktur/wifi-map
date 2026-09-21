"""CLI: scan/walk/eval/locations/list/export.

Exit codes: 0 ok, 2 no-wifi/signal-unavailable, 3 storage, 4 reserved.
Note: signal-tool missing currently maps to 2 with an install hint;
4 is reserved for speedtest-binary-missing when required (scan currently
warns and proceeds signal-only instead; walk snapshots do the same).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from typing import Optional, Sequence

from wifimap import signal as signal_mod
from wifimap import ssid as ssid_mod
from wifimap import speed as speed_mod
from wifimap import store as store_mod
from wifimap import evaluation as evaluation_mod
from wifimap import eval_tui as eval_tui_mod
from wifimap.cli_common import (
    EXIT_NOWIFI,
    EXIT_OK,
    EXIT_STORAGE,
    _DEFAULT_DB,
    _PROJECT_ROOT,
    _disp,
    _open_db,
    _sample_countdown,
)
from wifimap.cli_storage_commands import (
    _cmd_aps_current,
    _cmd_aps_list,
    _cmd_aps_name,
    _cmd_benchmark_clear,
    _cmd_benchmark_set,
    _cmd_benchmark_show,
    _cmd_export,
    _cmd_list,
    _cmd_locations_add,
    _cmd_locations_list,
    _cmd_rooms_add,
    _cmd_rooms_list,
    _cmd_spots_add,
    _cmd_spots_list,
)



def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wifimap",
        description="Map and evaluate home WiFi.",
        epilog="Exit codes: 0 ok, 2 no-wifi/signal-unavailable "
        "(signal-tool missing maps to 2 with hint), 3 storage, "
        "4 reserved (speedtest binary missing when required).",
    )
    p.add_argument("--db", default=_DEFAULT_DB,
                   help="SQLite DB path (default <project>/db/wifi-map.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="Single snapshot row, print, exit.")
    s.add_argument("--location", required=True, help="Location ID|NAME")
    s.add_argument("--room", required=True, help="Room ID|NAME")
    s.add_argument("--spot", required=True, help="Spot ID|NAME")
    s.add_argument(
        "--ssid", default=None,
        help="Use or create this location SSID (prompts when omitted)",
    )
    s.add_argument("--room-floor", type=int, default=0)
    s.add_argument("--room-outdoors", type=int, choices=(0, 1),
                   default=0)
    s.add_argument("--no-speedtest", action="store_true")
    s.add_argument("--note", default=None)

    w = sub.add_parser("walk", help="Live walkthrough TUI, snapshot with `s`.")
    w.add_argument("--interval", type=float, default=1.0)
    w.add_argument("--no-speedtest", action="store_true")
    w.add_argument("--location", default=None, help="Preset ID|NAME")
    w.add_argument("--ssid", default=None,
                   help="Use or create this location SSID")
    w.add_argument(
        "--name", default=None,
        help="Name this saved walk (defaults to its start timestamp)")
    w.add_argument(
        "--compare-to", default=None, metavar="WALK",
        help="Compare live measurements with a prior walk ID or name")

    ev = sub.add_parser(
        "eval", help="Evaluate saved readings in a terminal UI.")
    ev.add_argument(
        "--csv", default=None,
        help="Read a current-schema CSV export instead of the database")

    loc = sub.add_parser("locations", help="Locations CRUD.")
    loc_sub = loc.add_subparsers(dest="locations_cmd", required=False)
    loc_sub.add_parser("list", help="Print id/name.")
    ladd = loc_sub.add_parser("add", help="Create a location.")
    ladd.add_argument("--name", required=True)

    rm = sub.add_parser("rooms", help="Rooms CRUD.")
    rm_sub = rm.add_subparsers(dest="rooms_cmd", required=True)
    rmlist = rm_sub.add_parser("list", help="Print id/name/floor/outdoors.")
    rmlist.add_argument("--location", required=True, help="Location ID|NAME")
    rmadd = rm_sub.add_parser("add", help="Create a room.")
    rmadd.add_argument("--location", required=True, help="Location ID|NAME")
    rmadd.add_argument("--name", required=True)
    rmadd.add_argument("--floor", type=int, default=0)
    rmadd.add_argument("--outdoors", action="store_true")

    sp = sub.add_parser("spots", help="Spots CRUD.")
    sp_sub = sp.add_subparsers(dest="spots_cmd", required=True)
    splist = sp_sub.add_parser("list", help="Print id/name.")
    splist.add_argument("--location", required=True, help="Location ID|NAME")
    splist.add_argument("--room", required=True, help="Room ID|NAME")
    spadd = sp_sub.add_parser("add", help="Create a spot.")
    spadd.add_argument("--location", required=True, help="Location ID|NAME")
    spadd.add_argument("--room", required=True, help="Room ID|NAME")
    spadd.add_argument("--name", required=True)

    aps = sub.add_parser("aps", help="Name recognizable access points.")
    aps_sub = aps.add_subparsers(dest="aps_cmd", required=True)
    aps_sub.add_parser(
        "current", help="Print the current BSSID and optional name.")
    aps_sub.add_parser("list", help="List access-point names and BSSIDs.")
    ap_name = aps_sub.add_parser(
        "name", help="Name the current or an explicit access point.")
    ap_name.add_argument("--name", required=True)
    ap_name.add_argument(
        "--bssid", default=None,
        help="Access-point BSSID; defaults to the current connection")

    li = sub.add_parser("list", help="History table (joins locations).")
    li.add_argument("--location", default=None, help="Filter ID|NAME")
    li.add_argument("--room", default=None, help="Filter ID|NAME")
    li.add_argument("--spot", default=None, help="Filter ID|NAME")
    li.add_argument("--floor", type=int, default=None)
    li.add_argument("--ssid", default=None, help="Filter by SSID")
    li.add_argument("--limit", type=int, default=50)

    ex = sub.add_parser("export", help="Dump CSV for plotting.")
    ex.add_argument("--csv", required=True, help="Output CSV path")
    ex.add_argument("--location", default=None, help="Filter ID|NAME")
    ex.add_argument("--room", default=None, help="Filter ID|NAME")
    ex.add_argument("--spot", default=None, help="Filter ID|NAME")
    ex.add_argument("--floor", type=int, default=None)
    ex.add_argument("--ssid", default=None, help="Filter by SSID")
    bm = sub.add_parser("benchmark",
                        help="Ideal-conditions reference per location.")
    bm_sub = bm.add_subparsers(dest="benchmark_cmd", required=True)
    bm_set = bm_sub.add_parser("set", help="Capture benchmark for a location.")
    bm_set.add_argument("--location", required=True, help="Location ID|NAME")
    bm_set.add_argument("--no-speedtest", action="store_true")
    bm_set.add_argument("--note", default=None)
    bm_set.add_argument("--force", action="store_true")
    bm_show = bm_sub.add_parser("show", help="Print benchmark for a location.")
    bm_show.add_argument("--location", required=True, help="Location ID|NAME")
    bm_clear = bm_sub.add_parser("clear",
                                help="Delete benchmark for a location.")
    bm_clear.add_argument("--location", required=True, help="Location ID|NAME")
    bm_clear.add_argument("--force", action="store_true")
    return p




def _cmd_scan(db_path: str, args: argparse.Namespace) -> int:
    try:
        conn = _open_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return EXIT_STORAGE
    try:
        try:
            loc_id = store_mod.resolve_location(conn, args.location)
            room_id = store_mod.resolve_room(
                conn, loc_id, args.room,
                floor=args.room_floor,
                outdoors=bool(args.room_outdoors),
            )
            spot_id = store_mod.resolve_spot(conn, room_id, args.spot)
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot resolve location: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        identity = None
        if args.ssid is None:
            try:
                identity = signal_mod.read_network_identity()
            except Exception:  # noqa: BLE001 - identity is best-effort
                identity = None
        try:
            selected_ssid = ssid_mod.select_ssid_line(
                conn,
                loc_id,
                detected_name=identity[0] if identity is not None else None,
                requested=args.ssid,
            )
        except ssid_mod.SSIDSelectionCancelled:
            print("SSID selection cancelled", file=sys.stderr)
            return EXIT_OK
        except (ssid_mod.SSIDSelectionError, sqlite3.Error, ValueError) as exc:
            print("Error: cannot select SSID: %s" % (exc,), file=sys.stderr)
            return EXIT_STORAGE
        _sample_countdown()
        try:
            sig = signal_mod.sample_signal()
        except signal_mod.NoWiFiError as exc:
            print("Error: no WiFi: %s" % (exc,), file=sys.stderr)
            return EXIT_NOWIFI
        except signal_mod.SignalUnavailableError as exc:
            print("Error: signal backend unavailable: %s "
                  "(hint: pip install pyobjc-framework-CoreWLAN)"
                  % (exc,), file=sys.stderr)
            return EXIT_NOWIFI
        sig.ssid = selected_ssid.name
        if identity is not None:
            if sig.bssid is None:
                sig.bssid = identity[1]
        ping_ms: Optional[float] = None
        down_mbps: Optional[float] = None
        up_mbps: Optional[float] = None
        server: Optional[str] = None
        if not args.no_speedtest:
            print("speedtest running...", file=sys.stderr, flush=True)
            try:
                sp = speed_mod.run_speedtest()
                ping_ms, down_mbps, up_mbps, server = (
                    sp.ping_ms, sp.down_mbps, sp.up_mbps, sp.server)
            except speed_mod.SpeedtestUnavailableError as exc:
                print("Warning: %s; proceeding signal-only" % (exc,),
                      file=sys.stderr)
            except speed_mod.SpeedtestFailedError:
                ping_ms, down_mbps, up_mbps, server = None, None, None, "ERROR"
        try:
            rid = store_mod.add_reading(
                conn, spot_id, ssid_id=selected_ssid.id, bssid=sig.bssid,
                rssi=sig.rssi, noise=sig.noise, snr=sig.snr,
                channel=sig.channel, phy=sig.phy, tx_rate=sig.tx_rate,
                ping_ms=ping_ms, down_mbps=down_mbps, up_mbps=up_mbps,
                server=server, note=args.note,
            )
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot store reading: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        print("#%d %s/%s/%s rssi=%s snr=%s down=%s up=%s note=%s" % (
            rid, args.location, args.room, args.spot,
            _disp(sig.rssi), _disp(sig.snr),
            _disp(down_mbps), _disp(up_mbps), _disp(args.note)),
            end="")
        try:
            bench = store_mod.get_benchmark(conn, loc_id)
            delta = store_mod.format_benchmark_delta(
                {"rssi": sig.rssi, "snr": sig.snr,
                 "down_mbps": down_mbps, "up_mbps": up_mbps},
                bench)
            if delta:
                print(" (vs bench: %s)" % delta, end="")
        except Exception:  # noqa: BLE001 - delta never breaks scan
            pass
        print("")
        return EXIT_OK
    finally:
        conn.close()


def _cmd_walk(db_path: str, args: argparse.Namespace) -> int:
    from wifimap import tui as tui_mod

    ssid = getattr(args, "ssid", None)
    if ssid is not None:
        ssid = ssid.strip()
        if not ssid:
            print("Error: --ssid must not be blank", file=sys.stderr)
            return EXIT_STORAGE
    walk_name = getattr(args, "name", None)
    if walk_name is not None:
        walk_name = walk_name.strip()
        if not walk_name:
            print("Error: --name must not be blank", file=sys.stderr)
            return EXIT_STORAGE
    compare_to = getattr(args, "compare_to", None)
    if compare_to is not None:
        compare_to = compare_to.strip()
        if not compare_to:
            print("Error: --compare-to must not be blank", file=sys.stderr)
            return EXIT_STORAGE
    return tui_mod.run_walk(
        db_path, interval=args.interval,
        location_preset=args.location,
        no_speedtest=args.no_speedtest,
        ssid=ssid,
        walk_name=walk_name,
        compare_to=compare_to,
    )


def _cmd_eval(db_path: str, args: argparse.Namespace) -> int:
    try:
        report = (evaluation_mod.load_csv(args.csv) if args.csv
                  else evaluation_mod.load_db(db_path))
    except evaluation_mod.ReportError as exc:
        print("Error: cannot load evaluation source: %s" % exc,
              file=sys.stderr)
        return EXIT_STORAGE
    return eval_tui_mod.run_eval(report)




def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point returning an exit code (no sys.exit inside)."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    db_path: str = args.db
    if args.cmd == "scan":
        return _cmd_scan(db_path, args)
    if args.cmd == "walk":
        return _cmd_walk(db_path, args)
    if args.cmd == "eval":
        return _cmd_eval(db_path, args)
    if args.cmd == "locations":
        if getattr(args, "locations_cmd", None) == "add":
            return _cmd_locations_add(db_path, args)
        return _cmd_locations_list(db_path)
    if args.cmd == "rooms":
        if getattr(args, "rooms_cmd", None) == "add":
            return _cmd_rooms_add(db_path, args)
        return _cmd_rooms_list(db_path, args)
    if args.cmd == "spots":
        if getattr(args, "spots_cmd", None) == "add":
            return _cmd_spots_add(db_path, args)
        return _cmd_spots_list(db_path, args)
    if args.cmd == "aps":
        if args.aps_cmd == "name":
            return _cmd_aps_name(db_path, args)
        if args.aps_cmd == "current":
            return _cmd_aps_current(db_path)
        return _cmd_aps_list(db_path)
    if args.cmd == "list":
        return _cmd_list(db_path, args)
    if args.cmd == "export":
        return _cmd_export(db_path, args)
    if args.cmd == "benchmark":
        if args.benchmark_cmd == "set":
            return _cmd_benchmark_set(db_path, args)
        if args.benchmark_cmd == "show":
            return _cmd_benchmark_show(db_path, args)
        if args.benchmark_cmd == "clear":
            return _cmd_benchmark_clear(db_path, args)
    raise AssertionError("unreachable: argparse requires a subcommand")


if __name__ == "__main__":
    sys.exit(main())
