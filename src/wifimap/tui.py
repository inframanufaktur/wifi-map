"""Walk TUI: live signal poll + snapshot/location keys (stdlib only).

Thread safety (M1 concern): ``store.list_readings`` toggles
``conn.row_factory`` on the passed connection, so connections are NOT
thread-safe to share. The snapshot background thread MUST open its own
``store.get_db(db_path)`` connection and close it; it never touches the
UI thread's connection.
"""
from __future__ import annotations

import copy
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from wifimap import signal as signal_mod
from wifimap import speed as speed_mod
from wifimap import store as store_mod


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def format_signal_line(sig: signal_mod.Signal) -> str:
    """One-line live readout; None fields render as UNKNOWN/-."""

    def _v(v: object) -> str:
        return "-" if v is None else str(v)

    rssi = "UNKNOWN" if sig.rssi is None else "%d dBm" % sig.rssi
    noise = "UNKNOWN" if sig.noise is None else "%d dBm" % sig.noise
    snr = "UNKNOWN" if sig.snr is None else "%d dB" % sig.snr
    return "rssi=%s noise=%s snr=%s ssid=%s bssid=%s ch=%s phy=%s tx=%s" % (
        rssi, noise, snr, _v(sig.ssid), _v(sig.bssid),
        _v(sig.channel), _v(sig.phy), _v(sig.tx_rate),
    )


def pick_location_index(keypress: str, count: int, active: Optional[int]) -> Optional[int]:
    """Map a picker keypress to a 0-based location index.

    Numbered list (``1``-``9``) selects; ``+`` selects the create row
    (returned as ``count`` sentinel); anything else (``q``/Escape
    cancel included) returns ``None``. ``active`` is unused for
    selection, kept for API parity with the picker UI prefill.
    """
    _ = active
    if keypress == "+":
        return count
    if len(keypress) == 1 and "1" <= keypress <= "9":
        idx = int(keypress) - 1
        if 0 <= idx < count:
            return idx
    return None


def parse_floor_input(s: str) -> int:
    """Parse floor int (negative = basement); raises ValueError on bad input."""
    t = s.strip()
    if not t:
        raise ValueError("floor must be an integer, got empty input")
    try:
        return int(t, 10)
    except ValueError as exc:
        raise ValueError("floor must be an integer, got %r" % (s,)) from exc


def snapshot_payload(sig: signal_mod.Signal) -> Dict[str, object]:
    """Freeze a signal copy into a store.add_reading kwargs dict."""
    return {
        "ssid": sig.ssid,
        "bssid": sig.bssid,
        "rssi": sig.rssi,
        "noise": sig.noise,
        "snr": sig.snr,
        "channel": sig.channel,
        "phy": sig.phy,
        "tx_rate": sig.tx_rate,
    }


def attempt_read(
    read_fn: Callable[[], signal_mod.Signal],
    max_retries: int = 3,
) -> signal_mod.Signal:
    """Poll once with retry; parse-fail retries ``max_retries``x then UNKNOWN.

    ``NoWiFiError`` propagates immediately (caller shows NO-WIFI).
    Any other exception is retried; after exhaustion returns an empty
    ``Signal()`` (UNKNOWN row), never raises.
    """
    last_exc: Optional[Exception] = None
    for _ in range(max_retries + 1):
        try:
            return read_fn()
        except signal_mod.NoWiFiError:
            raise
        except Exception as exc:  # noqa: BLE001 - walk must never crash
            last_exc = exc
    _ = last_exc
    return signal_mod.Signal()


# ---------------------------------------------------------------------------
# Snapshot background worker (thread-safe: own DB connection)
# ---------------------------------------------------------------------------

@dataclass
class SnapshotResult:
    ok: bool
    reading_id: Optional[int] = None
    message: str = ""


def finish_snapshot(
    db_path: str,
    location_id: int,
    sig_dict: Dict[str, object],
    run_speedtest_fn: Optional[Callable[[], speed_mod.Speed]] = None,
    no_speedtest: bool = False,
) -> SnapshotResult:
    """Run speedtest (unless skipped) + insert reading on a fresh connection.

    Always opens/closes its OWN ``store.get_db`` connection — never share
    the UI thread's connection (row_factory toggling is not thread-safe).
    Speedtest fail → NULLs + server="ERROR", signal kept (spec 4).
    Missing binary → signal-only row + warning message (same as scan).
    DB error → ok=False + error message (caller toasts, walk continues).
    """
    ping_ms = down = up = None
    server: Optional[str] = None
    notice = ""
    if not no_speedtest:
        fn = run_speedtest_fn or speed_mod.run_speedtest
        try:
            sp = fn()
            ping_ms, down, up, server = (
                sp.ping_ms, sp.down_mbps, sp.up_mbps, sp.server)
        except speed_mod.SpeedtestUnavailableError as exc:
            notice = "Warning: %s; signal-only" % (exc,)
        except speed_mod.SpeedtestFailedError:
            ping_ms, down, up, server = None, None, None, "ERROR"
            notice = "speedtest failed; signal kept"
    try:
        conn = store_mod.get_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        return SnapshotResult(ok=False, message="DB error: %s" % (exc,))
    try:
        try:
            rid = store_mod.add_reading(
                conn, location_id,
                ssid=sig_dict.get("ssid"),  # type: ignore[arg-type]
                bssid=sig_dict.get("bssid"),  # type: ignore[arg-type]
                rssi=sig_dict.get("rssi"),  # type: ignore[arg-type]
                noise=sig_dict.get("noise"),  # type: ignore[arg-type]
                snr=sig_dict.get("snr"),  # type: ignore[arg-type]
                channel=sig_dict.get("channel"),  # type: ignore[arg-type]
                phy=sig_dict.get("phy"),  # type: ignore[arg-type]
                tx_rate=sig_dict.get("tx_rate"),  # type: ignore[arg-type]
                ping_ms=ping_ms, down_mbps=down,  # type: ignore[arg-type]
                up_mbps=up,  # type: ignore[arg-type]
                server=server,
            )
        except (sqlite3.Error, OSError, ValueError) as exc:
            return SnapshotResult(ok=False, message="DB error: %s" % (exc,))
    finally:
        conn.close()
    msg = "saved #%d" % rid
    if notice:
        msg += " (%s)" % notice
    return SnapshotResult(ok=True, reading_id=rid, message=msg)


def start_snapshot_thread(
    db_path: str,
    location_id: int,
    sig: signal_mod.Signal,
    no_speedtest: bool = False,
    on_done: Optional[Callable[[SnapshotResult], None]] = None,
    run_speedtest_fn: Optional[Callable[[], speed_mod.Speed]] = None,
) -> "threading.Thread":
    """Freeze signal copy, spawn daemon thread; on done insert + callback.

    The thread opens its own DB connection via ``finish_snapshot``.
    """
    frozen = snapshot_payload(copy.deepcopy(sig))

    def _work() -> None:
        res = finish_snapshot(
            db_path, location_id, frozen,
            run_speedtest_fn=run_speedtest_fn,
            no_speedtest=no_speedtest,
        )
        if on_done is not None:
            on_done(res)

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Walk state (shared by curses + fallback loops)
# ---------------------------------------------------------------------------

class WalkState:
    """Mutable walk session: active location, last signal, toast, counters."""

    def __init__(self, db_path: str, no_speedtest: bool = False) -> None:
        self.db_path = db_path
        self.no_speedtest = no_speedtest
        self.active_id: Optional[int] = None
        self.sig: signal_mod.Signal = signal_mod.Signal()
        self.no_wifi: bool = False
        self.no_wifi_msg: str = ""
        self.toast: str = ""
        self.pending: int = 0

    def set_toast(self, msg: str) -> None:
        self.toast = msg

    def on_snapshot_done(self, res: SnapshotResult) -> None:
        self.pending = max(0, self.pending - 1)
        self.set_toast(res.message if res.message else (
            "saved #%s" % res.reading_id))

    def poll(self, read_fn: Optional[Callable[[], signal_mod.Signal]] = None) -> None:
        fn = read_fn or signal_mod.read_signal
        try:
            self.sig = attempt_read(fn)
            self.no_wifi = False
            self.no_wifi_msg = ""
        except signal_mod.NoWiFiError as exc:
            self.no_wifi = True
            self.no_wifi_msg = str(exc)

    def try_snapshot(
        self,
        run_speedtest_fn: Optional[Callable[[], speed_mod.Speed]] = None,
    ) -> Optional["threading.Thread"]:
        """Spawn snapshot thread for active location; None + hint if blocked."""
        if self.no_wifi:
            self.set_toast("NO-WIFI: `s` blocked (WiFi off/not associated)")
            return None
        if self.active_id is None:
            self.set_toast("no active location: press `l` to pick one")
            return None
        self.pending += 1
        loc_id = self.active_id
        sig_copy = copy.deepcopy(self.sig)
        result_box: List[SnapshotResult] = []

        def _cb(res: SnapshotResult) -> None:
            result_box.append(res)
            self.on_snapshot_done(res)

        t = start_snapshot_thread(
            self.db_path, loc_id, sig_copy,
            no_speedtest=self.no_speedtest,
            on_done=_cb,
            run_speedtest_fn=run_speedtest_fn,
        )
        return t

    def set_floor(self, conn: sqlite3.Connection, floor: int) -> str:
        """Update active location floor; returns toast message."""
        if self.active_id is None:
            return "no active location"
        try:
            cur = conn.execute(
                "UPDATE locations SET floor = ? WHERE id = ?",
                (floor, self.active_id),
            )
            conn.commit()
        except (sqlite3.Error, OSError) as exc:
            return "DB error: %s" % (exc,)
        if cur.rowcount == 0:
            return "unknown location id: %r" % (self.active_id,)
        return "floor set to %d" % floor


def _resolve_preset(conn: sqlite3.Connection, preset: Optional[str]) -> Optional[int]:
    if preset is None:
        return None
    try:
        return store_mod.resolve_location(conn, preset)
    except (sqlite3.Error, OSError, ValueError):
        return None


def _location_label(conn: sqlite3.Connection, loc_id: Optional[int]) -> str:
    if loc_id is None:
        return "(none)"
    try:
        row = conn.execute(
            "SELECT name, floor FROM locations WHERE id = ?",
            (loc_id,),
        ).fetchone()
    except (sqlite3.Error, OSError):
        return "#%s (db error)" % loc_id
    if row is None:
        return "#%s (deleted?)" % loc_id
    return "#%s %s (floor %s)" % (loc_id, row[0], row[1])


# ---------------------------------------------------------------------------
# Curses loop
# ---------------------------------------------------------------------------

def _picker_curses(stdscr: object, conn: sqlite3.Connection,
                   active: Optional[int]) -> Optional[int]:
    """Numbered-list picker; returns location id, 'new' sentinel, or None.

    Returns ``"new"`` string sentinel when the ``+`` create row is chosen
    (caller prompts for name/floor/outdoors). Any other cancel → None.
    """
    locs = store_mod.list_locations(conn)
    lines = ["Pick location (1-%d, + new, q cancel):" % len(locs)]
    for i, loc in enumerate(locs):
        mark = "*" if loc.id == active else " "
        lines.append("%s%d. %s (floor %d%s)" % (
            mark, i + 1, loc.name, loc.floor,
            ", outdoors" if loc.outdoors else ""))
    lines.append("  +. <new location>")
    h, w = stdscr.getmaxyx()  # type: ignore[attr-defined]
    stdscr.clear()  # type: ignore[attr-defined]
    for r, ln in enumerate(lines[: h - 1]):
        try:
            stdscr.addstr(r, 0, ln[: w - 1])  # type: ignore[attr-defined]
        except Exception:
            pass
    stdscr.refresh()  # type: ignore[attr-defined]
    stdscr.timeout(-1)  # type: ignore[attr-defined]  # blocking for picker
    try:
        while True:
            ch = stdscr.getch()  # type: ignore[attr-defined]
            if ch in (27, ord("q"), ord("Q")):
                return None
            try:
                key = chr(ch)
            except (ValueError, OverflowError):
                continue
            idx = pick_location_index(key, len(locs), active)
            if idx is None:
                continue
            if idx == len(locs):
                return "new"  # type: ignore[return-value]
            return locs[idx].id
    finally:
        stdscr.timeout(1000)  # type: ignore[attr-defined]


def _prompt_curses(stdscr: object, prompt: str) -> str:
    """One-line prompt via curses echo; falls back to '' on error."""
    import curses

    h, _w = stdscr.getmaxyx()  # type: ignore[attr-defined]
    try:
        curses.echo()  # type: ignore[attr-defined]
        stdscr.addstr(h - 1, 0, prompt[: _w - 1])  # type: ignore[attr-defined]
        stdscr.clrtoeol()  # type: ignore[attr-defined]
        stdscr.refresh()  # type: ignore[attr-defined]
        raw = stdscr.getstr(h - 1, min(len(prompt), _w - 1))  # type: ignore[attr-defined]
        return raw.decode("utf-8", "replace")
    except Exception:
        return ""
    finally:
        try:
            curses.noecho()  # type: ignore[attr-defined]
        except Exception:
            pass


def _create_location_curses(stdscr: object, conn: sqlite3.Connection) -> Optional[int]:
    name = _prompt_curses(stdscr, "name: ").strip()
    if not name:
        return None
    floor_s = _prompt_curses(stdscr, "floor (int): ").strip()
    try:
        floor = parse_floor_input(floor_s or "0")
    except ValueError:
        return None
    od_s = _prompt_curses(stdscr, "outdoors? [y/N]: ").strip().lower()
    outdoors = od_s in ("y", "yes", "1")
    try:
        return store_mod.create_location(
            conn, name, floor=floor, outdoors=outdoors)
    except (sqlite3.Error, OSError, ValueError):
        return None


def _walk_curses(stdscr: object, db_path: str, interval: float,
                 location_preset: Optional[str],
                 no_speedtest: bool) -> int:
    import curses

    try:
        conn = store_mod.get_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        try:
            stdscr.addstr(0, 0, "DB error: %s" % (exc,))
            stdscr.refresh()
            time.sleep(2)
        except Exception:
            pass
        return 3
    state = WalkState(db_path, no_speedtest=no_speedtest)
    try:
        state.active_id = _resolve_preset(conn, location_preset)
        if location_preset is not None and state.active_id is None:
            state.set_toast(
                "unknown preset %r; press `l` to pick" % (location_preset,))
        stdscr.nodelay(False)
        stdscr.timeout(max(50, int(interval * 1000)))
        try:
            curses.curs_set(0)
        except Exception:
            pass
        while True:
            state.poll()
            try:
                locs = store_mod.list_locations(conn)
            except (sqlite3.Error, OSError) as exc:
                state.set_toast("DB error: %s" % (exc,))
                locs = []
            # render
            try:
                stdscr.clear()
                h, w = stdscr.getmaxyx()
                row = 0

                def _emit(s: str) -> None:
                    nonlocal row
                    if row < h - 1:
                        try:
                            stdscr.addstr(row, 0, s[: w - 1])
                        except Exception:
                            pass
                        row += 1

                if state.no_wifi:
                    _emit("NO-WIFI: %s" % (state.no_wifi_msg,))
                    _emit("`s` blocked; fix WiFi or quit with `q`.")
                else:
                    _emit(format_signal_line(state.sig))
                _emit("loc: %s  pending: %d" % (
                    _location_label(conn, state.active_id), state.pending))
                _emit("keys: s snapshot | l switch | n new | "
                      "f floor | q quit")
                if state.toast:
                    _emit("» %s" % state.toast)
                stdscr.refresh()
            except Exception:
                pass
            ch = stdscr.getch()
            if ch == -1:
                continue
            if ch in (3,):  # Ctrl-C via getch if delivered
                return 0
            try:
                key = chr(ch)
            except (ValueError, OverflowError):
                continue
            if key in ("q", "Q"):
                return 0
            elif key == "s":
                if state.active_id is None and not state.no_wifi:
                    picked = _picker_curses(stdscr, conn, state.active_id)
                    if picked == "new":
                        picked = _create_location_curses(stdscr, conn)
                    if isinstance(picked, int):
                        state.active_id = picked
                    else:
                        state.set_toast("snapshot cancelled (no location)")
                        continue
                state.try_snapshot()
            elif key == "l":
                picked = _picker_curses(stdscr, conn, state.active_id)
                if picked == "new":
                    picked = _create_location_curses(stdscr, conn)
                if isinstance(picked, int):
                    state.active_id = picked
                    state.set_toast("active: %s" % _location_label(
                        conn, state.active_id))
                elif picked is None:
                    state.set_toast("location switch cancelled")
            elif key == "n":
                new_id = _create_location_curses(stdscr, conn)
                if new_id is not None:
                    state.active_id = new_id
                    state.set_toast("active: %s" % _location_label(
                        conn, state.active_id))
                else:
                    state.set_toast("new location cancelled/invalid")
            elif key == "f":
                if state.active_id is None:
                    state.set_toast("no active location")
                else:
                    raw = _prompt_curses(stdscr, "floor (int): ")
                    try:
                        floor = parse_floor_input(raw)
                    except ValueError as exc:
                        state.set_toast(str(exc))
                    else:
                        state.set_toast(state.set_floor(conn, floor))
            # loop; timeout already paces polling
    except KeyboardInterrupt:
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Non-curses fallback (plain ANSI; blocking-input limit documented)
# ---------------------------------------------------------------------------

def _walk_fallback(db_path: str, interval: float,
                   location_preset: Optional[str],
                   no_speedtest: bool) -> int:
    """ANSI fallback when curses/tty unavailable.

    Limit: keys are line-buffered (type a key + Enter); no live refresh
    while waiting for input. Same ``s``/``l``/``n``/``f``/``q`` keys.
    """
    import select

    try:
        conn = store_mod.get_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        print("Error: cannot open DB: %s" % (exc,), file=sys.stderr)
        return 3
    state = WalkState(db_path, no_speedtest=no_speedtest)
    try:
        state.active_id = _resolve_preset(conn, location_preset)
        print("walk fallback (no curses): keys s/l/n/f/q + Enter", flush=True)
        while True:
            state.poll()
            if state.no_wifi:
                print("NO-WIFI: %s (`s` blocked)" % state.no_wifi_msg)
            else:
                print(format_signal_line(state.sig), flush=True)
            print("loc: %s pending: %d %s" % (
                _location_label(conn, state.active_id), state.pending,
                ("» %s" % state.toast) if state.toast else ""))
            state.set_toast("")
            r, _, _ = select.select([sys.stdin], [], [], interval)
            key = ""
            if r:
                try:
                    key = sys.stdin.readline().strip().lower()
                except (OSError, ValueError):
                    key = ""
            else:
                continue
            if key in ("q", "quit", "exit"):
                return 0
            elif key == "s":
                if state.active_id is None and not state.no_wifi:
                    _fallback_pick(conn, state)
                    if state.active_id is None:
                        state.set_toast("snapshot cancelled")
                        continue
                state.try_snapshot()
                time.sleep(0.1)  # let fast mocks finish for toast ordering
            elif key == "l":
                _fallback_pick(conn, state)
            elif key == "n":
                _fallback_create(conn, state)
            elif key == "f":
                _fallback_floor(conn, state)
    except KeyboardInterrupt:
        return 0
    finally:
        conn.close()


def _fallback_pick(conn: sqlite3.Connection, state: WalkState) -> None:
    try:
        locs = store_mod.list_locations(conn)
    except (sqlite3.Error, OSError) as exc:
        state.set_toast("DB error: %s" % (exc,))
        return
    for i, loc in enumerate(locs):
        print("%d. %s (floor %d)" % (i + 1, loc.name, loc.floor))
    print("+. <new location>")
    try:
        raw = input("pick [1-%d,+]: " % len(locs)).strip()
    except (EOFError, OSError):
        state.set_toast("cancelled")
        return
    idx = pick_location_index(raw, len(locs), state.active_id)
    if idx is None:
        state.set_toast("cancelled")
    elif idx == len(locs):
        _fallback_create(conn, state)
    else:
        state.active_id = locs[idx].id


def _fallback_create(conn: sqlite3.Connection, state: WalkState) -> None:
    try:
        name = input("name: ").strip()
        floor = parse_floor_input(input("floor (int): ") or "0")
        od = input("outdoors? [y/N]: ").strip().lower() in ("y", "yes", "1")
    except (EOFError, OSError, ValueError) as exc:
        state.set_toast("cancelled/invalid: %s" % (exc,))
        return
    try:
        state.active_id = store_mod.create_location(
            conn, name, floor=floor, outdoors=outdoors)
    except (sqlite3.Error, OSError, ValueError) as exc:
        state.set_toast("DB error: %s" % (exc,))


def _fallback_floor(conn: sqlite3.Connection, state: WalkState) -> None:
    try:
        raw = input("floor (int): ")
        floor = parse_floor_input(raw)
    except (EOFError, OSError, ValueError) as exc:
        state.set_toast(str(exc))
        return
    state.set_toast(state.set_floor(conn, floor))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_walk(db_path: str, interval: float = 1.0,
             location_preset: Optional[str] = None,
             no_speedtest: bool = False) -> int:
    """Run walk loop; curses when tty available else ANSI fallback."""
    if interval <= 0:
        print("Error: --interval must be > 0", file=sys.stderr)
        return 3
    try:
        import curses  # noqa: F401
        use_curses = sys.stdin.isatty() and sys.stdout.isatty()
    except ImportError:
        use_curses = False
    if use_curses:
        import curses as _c

        def _main(stdscr: object) -> int:
            return _walk_curses(
                stdscr, db_path, interval, location_preset, no_speedtest)

        try:
            return _c.wrapper(_main)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # curses init failed → fallback
            print("curses unavailable (%s); using fallback" % (exc,),
                  file=sys.stderr)
    return _walk_fallback(db_path, interval, location_preset, no_speedtest)


__all__ = [
    "WalkState",
    "SnapshotResult",
    "attempt_read",
    "finish_snapshot",
    "format_signal_line",
    "parse_floor_input",
    "pick_location_index",
    "snapshot_payload",
    "start_snapshot_thread",
    "run_walk",
]
