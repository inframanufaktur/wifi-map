"""Shared constants and helpers for command-line handlers."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from wifimap import evaluation as evaluation_mod
from wifimap import store as store_mod


EXIT_OK = 0
EXIT_NOWIFI = 2
EXIT_STORAGE = 3
# 4 reserved: speedtest binary missing when required.

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = str(_PROJECT_ROOT / "db" / "wifi-map.db")

_EXPORT_FIELDS = list(
    evaluation_mod.REQUIRED_FIELDS + evaluation_mod.OPTIONAL_AP_FIELDS
    + evaluation_mod.OPTIONAL_PATH_FIELDS
    + evaluation_mod.OPTIONAL_WALK_FIELDS)

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


def _open_db(path: str) -> sqlite3.Connection:
    return store_mod.get_db(path)
