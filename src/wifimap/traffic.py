"""Live interface traffic counters (macOS ``netstat``/``route``, stdlib only).

Throughput sampling for the walk TUI: the default-route interface carries
the internet path (physical enX or a VPN utun), so its byte counters give
live down/up Mbps. Best-effort throughout: missing binaries or unparseable
output yield ``None`` samples (blank graph), never exceptions.
"""
from __future__ import annotations

import re
import subprocess
import time
from typing import Callable, Optional, Tuple

RouteOutput = Optional[str]
Rates = Optional[Tuple[float, float]]


def default_iface() -> Optional[str]:
    """Default-route interface name via ``route -n get default``; None on fail."""
    try:
        out = subprocess.run(
            ["route", "-n", "get", "default"],
            capture_output=True, text=True, timeout=2.0,
        ).stdout
    except Exception:  # noqa: BLE001 - best-effort
        return None
    m = re.search(r"interface:\s*(\S+)", out)
    if m is None:
        return None
    return m.group(1)


def parse_netstat_ib(text: str, iface: str) -> Optional[Tuple[int, int]]:
    """``(ibytes, obytes)`` from ``netstat -I <iface> -b`` <Link> row.

    The Address field can be blank (utun rows), so whitespace-split
    columns cannot be mapped from the header. Instead the trailing
    7 tokens of a link row are always numeric (field order: Name Mtu
    Network [Address] Ipkts Ierrs Ibytes Opkts Oerrs Obytes Coll), so
    ``ibytes`` = token[-5] and ``obytes`` = token[-2], counted from the
    right end of the line. Returns None when absent/malformed.
    """
    for line in text.splitlines():
        tok = line.split()
        if not tok or tok[0] != iface:
            continue
        if "<Link" not in line:
            continue
        if len(tok) < 8:
            return None
        try:
            ib = int(tok[-5])
            ob = int(tok[-2])
        except ValueError:
            return None
        return (ib, ob)
    return None


def read_iface_counters(iface: str) -> Optional[Tuple[int, int]]:
    """Byte counters for ``iface`` via netstat; None on any failure."""
    try:
        out = subprocess.run(
            ["netstat", "-I", iface, "-b"],
            capture_output=True, text=True, timeout=2.0,
        ).stdout
    except Exception:  # noqa: BLE001 - best-effort
        return None
    return parse_netstat_ib(out, iface)


class TrafficSampler:
    """Delta sampler: byte counters between calls → (down, up) Mbps.

    First call returns None (needs a baseline). Missing counters, a
    non-monotonic counter reset, or a non-positive interval also return
    None and re-baseline.
    """

    def __init__(
        self,
        counters_fn: Optional[Callable[[str], Optional[Tuple[int, int]]]] = None,
        iface_fn: Optional[Callable[[], Optional[str]]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._counters_fn = counters_fn or read_iface_counters
        self._iface_fn = iface_fn or default_iface
        self._clock = clock
        self._iface: Optional[str] = None
        self._last: Optional[Tuple[int, int]] = None
        self._last_t: Optional[float] = None

    def sample(self) -> Rates:
        """One (down_mbps, up_mbps) sample, or None when unavailable."""
        if self._iface is None:
            self._iface = self._iface_fn()
            if self._iface is None:
                self._last = None
                return None
        cur = self._counters_fn(self._iface)
        now = self._clock()
        if cur is None:
            self._iface = None  # re-resolve interface next round
            self._last = None
            return None
        prev, prev_t = self._last, self._last_t
        self._last, self._last_t = cur, now
        if prev is None or prev_t is None:
            return None
        dt = now - prev_t
        if dt <= 0:
            return None
        d_in = cur[0] - prev[0]
        d_out = cur[1] - prev[1]
        if d_in < 0 or d_out < 0:
            return None  # counter reset; re-baseline only
        return (d_in * 8.0 / dt / 1e6, d_out * 8.0 / dt / 1e6)
