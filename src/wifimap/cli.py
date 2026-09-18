"""CLI: scan/locations/list/export (no walk yet).

Exit codes: 0 ok, 2 no-wifi/signal-unavailable, 3 storage, 4 reserved.
Note: signal-tool missing currently maps to 2 with an install hint;
4 is reserved for speedtest-binary-missing when required (scan currently
warns and proceeds signal-only instead).
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path
from typing import Optional, Sequence

from wifimap import signal as signal_mod
from wifimap import speed as speed_mod
from wifimap import store as store_mod

EXIT_OK = 0
EXIT_NOWIFI = 2
EXIT_STORAGE = 3
# 4 reserved: speedtest binary missing when required.

_DEFAULT_DB = str(Path.home() / "wifi-map.db")

_EXPORT_FIELDS = [
    "id", "ts", "location_id", "location_name", "floor", "outdoors",
    "ssid", "bssid", "rssi", "noise", "snr", "channel", "phy",
    "tx_rate", "ping_ms", "down_mbps", "up_mbps", "server", "note",
]


def _disp(v: object) -> str:
    """Human-table cell: NULL shows as ``-`` (CSV export keeps ``""``)."""
    return "-" if v is None else str(v)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wifimap",
        description="Map home WiFi (scan/locations/list/export).",
        epilog="Exit codes: 0 ok, 2 no-wifi/signal-unavailable "
        "(signal-tool missing maps to 2 with hint), 3 storage, "
        "4 reserved (speedtest binary missing when required).",
    )
    p.add_argument("--db", default=_DEFAULT_DB,
                   help="SQLite DB path (default ~/wifi-map.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="Single snapshot row, print, exit.")
    s.add_argument("--location", required=True, help="Location ID|NAME")
    s.add_argument("--location-floor", type=int, default=0)
    s.add_argument("--location-outdoors", type=int, choices=(0, 1),
                   default=0)
    s.add_argument("--no-speedtest", action="store_true")
    s.add_argument("--note", default=None)

    loc = sub.add_parser("locations", help="Locations CRUD.")
    loc_sub = loc.add_subparsers(dest="locations_cmd", required=False)
    loc_sub.add_parser("list", help="Print id/name/floor/outdoors.")
    ladd = loc_sub.add_parser("add", help="Create a location.")
    ladd.add_argument("--name", required=True)
    ladd.add_argument("--floor", type=int, default=0)
    ladd.add_argument("--outdoors", action="store_true")

    li = sub.add_parser("list", help="History table (joins locations).")
    li.add_argument("--location", default=None, help="Filter ID|NAME")
    li.add_argument("--floor", type=int, default=None)
    li.add_argument("--limit", type=int, default=50)

    ex = sub.add_parser("export", help="Dump CSV for plotting.")
    ex.add_argument("--csv", required=True, help="Output CSV path")
    ex.add_argument("--location", default=None, help="Filter ID|NAME")
    ex.add_argument("--floor", type=int, default=None)
    return p


def _open_db(path: str) -> sqlite3.Connection:
    return store_mod.get_db(path)


def _cmd_scan(db_path: str, args: argparse.Namespace) -> int:
    try:
        conn = _open_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return EXIT_STORAGE
    try:
        try:
            loc_id = store_mod.resolve_location(
                conn, args.location,
                floor=args.location_floor,
                outdoors=bool(args.location_outdoors),
            )
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot resolve location: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        try:
            sig = signal_mod.read_signal()
        except signal_mod.NoWiFiError as exc:
            print("Error: no WiFi: %s" % (exc,), file=sys.stderr)
            return EXIT_NOWIFI
        except signal_mod.SignalUnavailableError as exc:
            print("Error: signal backend unavailable: %s "
                  "(hint: pip install pyobjc-framework-CoreWLAN)"
                  % (exc,), file=sys.stderr)
            return EXIT_NOWIFI
        ping_ms: Optional[float] = None
        down_mbps: Optional[float] = None
        up_mbps: Optional[float] = None
        server: Optional[str] = None
        if not args.no_speedtest:
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
                conn, loc_id, ssid=sig.ssid, bssid=sig.bssid,
                rssi=sig.rssi, noise=sig.noise, snr=sig.snr,
                channel=sig.channel, phy=sig.phy, tx_rate=sig.tx_rate,
                ping_ms=ping_ms, down_mbps=down_mbps, up_mbps=up_mbps,
                server=server, note=args.note,
            )
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot store reading: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        print("#%d %s rssi=%s snr=%s down=%s up=%s note=%s" % (
            rid, args.location, _disp(sig.rssi), _disp(sig.snr),
            _disp(down_mbps), _disp(up_mbps), _disp(args.note)))
        return EXIT_OK
    finally:
        conn.close()


def _cmd_locations_list(db_path: str) -> int:
    try:
        conn = _open_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return EXIT_STORAGE
    try:
        try:
            locs = store_mod.list_locations(conn)
        except (sqlite3.Error, OSError) as exc:
            print("Error: cannot list locations: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        print("%-4s %-20s %-6s %-8s" % ("id", "name", "floor", "outdoors"))
        for loc in locs:
            print("%-4s %-20s %-6s %-8s" % (
                _disp(loc.id), _disp(loc.name), _disp(loc.floor),
                _disp(1 if loc.outdoors else 0)))
        return EXIT_OK
    finally:
        conn.close()


def _cmd_locations_add(db_path: str, args: argparse.Namespace) -> int:
    try:
        conn = _open_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return EXIT_STORAGE
    try:
        try:
            lid = store_mod.create_location(
                conn, args.name, floor=args.floor,
                outdoors=bool(args.outdoors))
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot add location: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        print(lid)
        return EXIT_OK
    finally:
        conn.close()


def _cmd_list(db_path: str, args: argparse.Namespace) -> int:
    try:
        conn = _open_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return EXIT_STORAGE
    try:
        try:
            rows = store_mod.list_readings(
                conn, location=args.location, floor=args.floor,
                limit=args.limit)
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot list readings: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        print("%-4s %-20s %-6s %-6s %-9s %-9s %s" % (
            "id", "location", "floor", "rssi", "down", "up", "note"))
        for r in rows:
            print("%-4s %-20s %-6s %-6s %-9s %-9s %s" % (
                _disp(r.get("id")), _disp(r.get("location_name")),
                _disp(r.get("floor")), _disp(r.get("rssi")),
                _disp(r.get("down_mbps")), _disp(r.get("up_mbps")),
                _disp(r.get("note"))))
        return EXIT_OK
    finally:
        conn.close()


def _cmd_export(db_path: str, args: argparse.Namespace) -> int:
    try:
        conn = _open_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return EXIT_STORAGE
    try:
        try:
            rows = store_mod.list_readings(
                conn, location=args.location, floor=args.floor,
                limit=1000000)
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot export readings: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
    finally:
        conn.close()
    try:
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=_EXPORT_FIELDS,
                               extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: ("" if r.get(k) is None else r.get(k))
                            for k in _EXPORT_FIELDS})
    except OSError as exc:
        print("Error: cannot write CSV: %s" % (exc,), file=sys.stderr)
        return EXIT_STORAGE
    print("wrote %d rows to %s" % (len(rows), args.csv))
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point returning an exit code (no sys.exit inside)."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    db_path: str = args.db
    if args.cmd == "scan":
        return _cmd_scan(db_path, args)
    if args.cmd == "locations":
        if getattr(args, "locations_cmd", None) == "add":
            return _cmd_locations_add(db_path, args)
        return _cmd_locations_list(db_path)
    if args.cmd == "list":
        return _cmd_list(db_path, args)
    if args.cmd == "export":
        return _cmd_export(db_path, args)
    raise AssertionError("unreachable: argparse requires a subcommand")


if __name__ == "__main__":
    sys.exit(main())
