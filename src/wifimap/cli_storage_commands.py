"""Storage, export, and benchmark command handlers."""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from typing import Optional

from wifimap import signal as signal_mod
from wifimap import speed as speed_mod
from wifimap import store as store_mod
from wifimap.cli_common import (
    EXIT_NOWIFI,
    EXIT_OK,
    EXIT_STORAGE,
    _DELTA_SPECS,
    _EXPORT_FIELDS,
    _disp,
    _open_db,
    _sample_countdown,
)


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
