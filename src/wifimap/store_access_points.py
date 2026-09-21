"""User-assigned names for physical Wi-Fi access points."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import List, Optional


_BSSID_RE = re.compile(r"^[0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5}$")


@dataclass(frozen=True)
class AccessPoint:
    bssid: str
    name: str


def normalize_bssid(value: str) -> str:
    """Return a canonical lower-case BSSID or raise ``ValueError``."""
    bssid = str(value).strip()
    if not _BSSID_RE.fullmatch(bssid):
        raise ValueError("invalid BSSID: %r" % value)
    return bssid.replace("-", ":").lower()


def set_access_point_name(
        conn: sqlite3.Connection, bssid: str, name: str) -> AccessPoint:
    """Create or replace the local display name for a BSSID."""
    canonical = normalize_bssid(bssid)
    label = str(name).strip()
    if not label:
        raise ValueError("access-point name must not be blank")
    conn.execute(
        "INSERT INTO access_points(bssid, name) VALUES (?, ?) "
        "ON CONFLICT(bssid) DO UPDATE SET name = excluded.name",
        (canonical, label),
    )
    conn.commit()
    return AccessPoint(canonical, label)


def get_access_point_name(
        conn: sqlite3.Connection, bssid: str) -> Optional[str]:
    canonical = normalize_bssid(bssid)
    row = conn.execute(
        "SELECT name FROM access_points WHERE bssid = ?", (canonical,)
    ).fetchone()
    return None if row is None else str(row[0])


def list_access_points(conn: sqlite3.Connection) -> List[AccessPoint]:
    rows = conn.execute(
        "SELECT bssid, name FROM access_points ORDER BY name, bssid"
    ).fetchall()
    return [AccessPoint(str(row[0]), str(row[1])) for row in rows]
