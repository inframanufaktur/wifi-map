"""CLI: scan/walk/eval/locations/list/export.

Exit codes: 0 ok, 2 no-wifi/signal-unavailable, 3 storage, 4 reserved.
Note: signal-tool missing currently maps to 2 with an install hint;
4 is reserved for speedtest-binary-missing when required (scan currently
warns and proceeds signal-only instead; walk snapshots do the same).
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from wifimap import signal as signal_mod
from wifimap import speed as speed_mod
from wifimap import store as store_mod
from wifimap import evaluation as evaluation_mod
from wifimap import eval_tui as eval_tui_mod

EXIT_OK = 0
EXIT_NOWIFI = 2
EXIT_STORAGE = 3
# 4 reserved: speedtest binary missing when required.

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = str(_PROJECT_ROOT / "db" / "wifi-map.db")

_EXPORT_FIELDS = list(evaluation_mod.REQUIRED_FIELDS)

_DELTA_SPECS = (
    ("rssi", "delta_rssi", round),
    ("snr", "delta_snr", round),
    ("down_mbps", "delta_down_mbps", lambda d: round(d, 1)),
    ("up_mbps", "delta_up_mbps", lambda d: round(d, 1)),
)


def _disp(v: object) -> str:
    """Human-table cell: NULL shows as ``-`` (CSV export keeps ``""``)."""
    return "-" if v is None else str(v)


def _sample_countdown(seconds: float = 5.0, tick: float = 1.0,
                      write=None, _sleep=None) -> None:
    """Tick a countdown to stderr before the blocking sampling call."""
    w = write if write is not None else sys.stderr.write
    sleep = _sleep if _sleep is not None else time.sleep
    n = max(1, int(round(seconds / tick)))
    for i in range(n, 0, -1):
        w("sampling %gs... %d\n" % (seconds, i))
        if i > 1:
            sleep(tick)


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
                   help="Session SSID override (manual)")
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
        try:
            identity = signal_mod.read_network_identity()
        except Exception:  # noqa: BLE001 - identity is best-effort
            identity = None
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
        if identity is not None:
            if sig.ssid is None:
                sig.ssid = identity[0]
            if sig.bssid is None:
                sig.bssid = identity[1]
        elif sig.ssid is None or sig.bssid is None:
            print("Warning: network name unavailable (sudo skipped); "
                  "SSID unknown, tagging unaffected", file=sys.stderr)
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
                conn, spot_id, ssid=sig.ssid, bssid=sig.bssid,
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
        print("%-4s %-20s" % ("id", "name"))
        for loc in locs:
            print("%-4s %-20s" % (
                _disp(loc.id), _disp(loc.name)))
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
            lid = store_mod.create_location(conn, args.name)
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot add location: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        print(lid)
        return EXIT_OK
    finally:
        conn.close()


def _cmd_rooms_list(db_path: str, args: argparse.Namespace) -> int:
    try:
        conn = _open_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return EXIT_STORAGE
    try:
        try:
            loc_id = store_mod.lookup_location(conn, args.location)
            rooms = store_mod.list_rooms(conn, location_id=loc_id)
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot list rooms: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        print("%-4s %-20s %-6s %-8s" % ("id", "name", "floor", "outdoors"))
        for rm in rooms:
            print("%-4s %-20s %-6s %-8s" % (
                _disp(rm.id), _disp(rm.name), _disp(rm.floor),
                _disp(1 if rm.outdoors else 0)))
        return EXIT_OK
    finally:
        conn.close()


def _cmd_rooms_add(db_path: str, args: argparse.Namespace) -> int:
    try:
        conn = _open_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return EXIT_STORAGE
    try:
        try:
            loc_id = store_mod.resolve_location(conn, args.location)
            rid = store_mod.create_room(
                conn, loc_id, args.name, floor=args.floor,
                outdoors=bool(args.outdoors))
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot add room: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        print(rid)
        return EXIT_OK
    finally:
        conn.close()


def _cmd_spots_list(db_path: str, args: argparse.Namespace) -> int:
    try:
        conn = _open_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return EXIT_STORAGE
    try:
        try:
            loc_id = store_mod.lookup_location(conn, args.location)
            room_id = store_mod.lookup_room(conn, loc_id, args.room)
            spots = store_mod.list_spots(conn, room_id=room_id)
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot list spots: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        print("%-4s %-20s" % ("id", "name"))
        for sp in spots:
            print("%-4s %-20s" % (_disp(sp.id), _disp(sp.name)))
        return EXIT_OK
    finally:
        conn.close()


def _cmd_spots_add(db_path: str, args: argparse.Namespace) -> int:
    try:
        conn = _open_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return EXIT_STORAGE
    try:
        try:
            loc_id = store_mod.resolve_location(conn, args.location)
            room_id = store_mod.resolve_room(conn, loc_id, args.room)
            sid = store_mod.create_spot(conn, room_id, args.name)
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot add spot: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        print(sid)
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
            ssid = getattr(args, "ssid", None)
            if ssid is not None:
                ssid = ssid.strip()
                if not ssid:
                    print("Error: --ssid must not be blank",
                          file=sys.stderr)
                    return EXIT_STORAGE
            rows = store_mod.list_readings(
                conn, location=args.location, room=args.room,
                spot=args.spot, floor=args.floor,
                ssid=ssid, limit=args.limit)
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot list readings: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        print("%-4s %-20s %-20s %-20s %-6s %-6s %-9s %-9s %s" % (
            "id", "location", "room", "spot", "floor", "rssi",
            "down", "up", "note"))
        for r in rows:
            print("%-4s %-20s %-20s %-20s %-6s %-6s %-9s %-9s %s" % (
                _disp(r.get("id")), _disp(r.get("location_name")),
                _disp(r.get("room_name")), _disp(r.get("spot_name")),
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
            ssid = getattr(args, "ssid", None)
            if ssid is not None:
                ssid = ssid.strip()
                if not ssid:
                    print("Error: --ssid must not be blank",
                          file=sys.stderr)
                    return EXIT_STORAGE
            rows = store_mod.list_readings(
                conn, location=args.location, room=args.room,
                spot=args.spot, floor=args.floor,
                ssid=ssid, limit=1000000)
            bench_cache = {}
            for r in rows:
                lid = r.get("location_id")
                if lid not in bench_cache:
                    try:
                        bench_cache[lid] = store_mod.get_benchmark(
                            conn, lid)
                    except Exception:  # noqa: BLE001 - delta is best-effort
                        bench_cache[lid] = None
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
                row = {k: ("" if r.get(k) is None else r.get(k))
                       for k in _EXPORT_FIELDS}
                bench = bench_cache.get(r.get("location_id"))
                for key, col, rnd in _DELTA_SPECS:
                    row[col] = ""
                    c = r.get(key)
                    b = bench.get(key) if isinstance(bench, dict) else None
                    if (c is None or b is None
                            or isinstance(c, bool) or isinstance(b, bool)
                            or not isinstance(c, (int, float))
                            or not isinstance(b, (int, float))):
                        continue
                    row[col] = rnd(c - b)
                w.writerow(row)
    except OSError as exc:
        print("Error: cannot write CSV: %s" % (exc,), file=sys.stderr)
        return EXIT_STORAGE
    print("wrote %d rows to %s" % (len(rows), args.csv))
    return EXIT_OK


def _cmd_benchmark_set(db_path: str, args: argparse.Namespace) -> int:
    try:
        conn = _open_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return EXIT_STORAGE
    try:
        try:
            loc_id = store_mod.resolve_location(conn, args.location)
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot resolve location: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        try:
            old = store_mod.get_benchmark(conn, loc_id)
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot read benchmark: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        if old is not None and not args.force:
            print("existing benchmark: rssi=%s snr=%s down=%s up=%s note=%s"
                  % (_disp(old.get("rssi")), _disp(old.get("snr")),
                     _disp(old.get("down_mbps")), _disp(old.get("up_mbps")),
                     _disp(old.get("note"))))
            try:
                ans = input("Overwrite benchmark for %s? [y/N] "
                            % (args.location,)).strip().lower()
            except (EOFError, OSError):
                ans = ""
            if ans not in ("y", "yes"):
                print("benchmark kept")
                return EXIT_OK
        _sample_countdown()
        try:
            sig = signal_mod.sample_signal()
        except signal_mod.NoWiFiError as exc:
            print("Error: no WiFi: %s" % (exc,), file=sys.stderr)
            return EXIT_NOWIFI
        except signal_mod.SignalUnavailableError as exc:
            print("Error: signal backend unavailable: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_NOWIFI
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
            store_mod.set_benchmark(
                conn, loc_id, ssid=sig.ssid, bssid=sig.bssid,
                rssi=sig.rssi, noise=sig.noise, snr=sig.snr,
                channel=sig.channel, phy=sig.phy, tx_rate=sig.tx_rate,
                ping_ms=ping_ms, down_mbps=down_mbps, up_mbps=up_mbps,
                server=server, note=args.note)
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot store benchmark: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        print("benchmark set for %s rssi=%s snr=%s down=%s up=%s note=%s"
              % (args.location, _disp(sig.rssi), _disp(sig.snr),
                 _disp(down_mbps), _disp(up_mbps), _disp(args.note)))
        return EXIT_OK
    finally:
        conn.close()


def _cmd_benchmark_show(db_path: str, args: argparse.Namespace) -> int:
    try:
        conn = _open_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return EXIT_STORAGE
    try:
        try:
            loc_id = store_mod.lookup_location(conn, args.location)
            bench = store_mod.get_benchmark(conn, loc_id)
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot show benchmark: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        if bench is None:
            print("no benchmark for %s" % (args.location,), file=sys.stderr)
            return EXIT_STORAGE
        print("benchmark for %s rssi=%s snr=%s down=%s up=%s note=%s" % (
            args.location, _disp(bench.get("rssi")),
            _disp(bench.get("snr")), _disp(bench.get("down_mbps")),
            _disp(bench.get("up_mbps")), _disp(bench.get("note"))))
        return EXIT_OK
    finally:
        conn.close()


def _cmd_benchmark_clear(db_path: str, args: argparse.Namespace) -> int:
    try:
        conn = _open_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return EXIT_STORAGE
    try:
        try:
            loc_id = store_mod.lookup_location(conn, args.location)
            bench = store_mod.get_benchmark(conn, loc_id)
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot clear benchmark: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        if bench is None:
            print("no benchmark for %s" % (args.location,), file=sys.stderr)
            return EXIT_STORAGE
        if not args.force:
            try:
                ans = input("Clear benchmark for %s? [y/N] "
                            % (args.location,)).strip().lower()
            except (EOFError, OSError):
                ans = ""
            if ans not in ("y", "yes"):
                print("benchmark kept")
                return EXIT_OK
        try:
            store_mod.clear_benchmark(conn, loc_id)
        except (sqlite3.Error, OSError, ValueError) as exc:
            print("Error: cannot clear benchmark: %s" % (exc,),
                  file=sys.stderr)
            return EXIT_STORAGE
        print("benchmark cleared for %s" % (args.location,))
        return EXIT_OK
    finally:
        conn.close()


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
