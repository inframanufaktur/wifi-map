"""Optional WifiWand helper adapter for current SSID/BSSID identity.

WifiWand documents a signed macOS helper with a JSON ``current-network``
command.  The helper owns the Location Services permission; wifimap only
executes it locally and validates its response.

Sources:
https://github.com/keithrbennett/wifiwand/blob/main/docs/MACOS_HELPER_APP_DETAILS.md
https://github.com/keithrbennett/wifiwand/blob/main/libexec/macos/src/wifiwand-helper.swift
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


_HELPER_ENV = "WIFIMAP_WIFIWAND_HELPER"
_BSSID_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
_HIDDEN_NAMES = {"<hidden>", "<redacted>"}


@dataclass(frozen=True)
class WifiIdentity:
    ssid: str
    bssid: Optional[str]


def _clean_ssid(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.casefold() in _HIDDEN_NAMES:
        return None
    return cleaned


def _clean_bssid(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not _BSSID_RE.fullmatch(cleaned):
        return None
    return cleaned.replace("-", ":").lower()


def parse_identity(output: str) -> Optional[WifiIdentity]:
    """Validate one ``current-network`` JSON response."""
    try:
        payload = json.loads(output)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    # Current WifiWand emits ``connected``; its public helper documentation
    # still shows ``ok``. Accept both documented successful forms.
    if payload.get("status") not in ("connected", "ok"):
        return None
    ssid = _clean_ssid(payload.get("ssid"))
    if ssid is None:
        return None
    return WifiIdentity(ssid=ssid, bssid=_clean_bssid(payload.get("bssid")))


def _version_key(path: Path):
    version = path.parents[3].name
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(.*)", version)
    if match is None:
        return ((-1,), 0, version.casefold())
    numbers = tuple(int(part) for part in match.group(1).split("."))
    numbers += (0,) * max(0, 4 - len(numbers))
    suffix = match.group(2).strip(".-").casefold()
    return (numbers, 1 if not suffix else 0, suffix)


def find_helper(
    configured_path: Optional[str] = None,
    home: Optional[Path] = None,
) -> Optional[str]:
    """Find an explicit, PATH, or standard WifiWand helper executable."""
    explicit = configured_path or os.environ.get(_HELPER_ENV)
    if explicit:
        path = Path(explicit).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None

    on_path = shutil.which("wifiwand-helper")
    if on_path:
        return on_path

    root = (home or Path.home()) / "Library" / "Application Support" / "WifiWand"
    candidates = [
        path for path in root.glob(
            "*/wifiwand-helper.app/Contents/MacOS/wifiwand-helper")
        if path.is_file() and os.access(path, os.X_OK)
    ]
    if not candidates:
        return None
    return str(max(candidates, key=_version_key))


class WifiWandIdentityProvider:
    """Bounded, cached access to WifiWand's local helper process."""

    def __init__(
        self,
        helper_path: Optional[str] = None,
        timeout: float = 3.0,
        success_ttl: float = 1.0,
        failure_ttl: float = 30.0,
        run_fn: Optional[Callable[..., subprocess.CompletedProcess]] = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.helper_path = helper_path
        self.timeout = timeout
        self.success_ttl = max(0.0, success_ttl)
        self.failure_ttl = max(0.0, failure_ttl)
        self._run_fn = run_fn or subprocess.run
        self._monotonic_fn = monotonic_fn
        self._lock = threading.Lock()
        self._cached: Optional[WifiIdentity] = None
        self._has_cached = False
        self._cached_at = 0.0

    def read(
        self,
        max_age: Optional[float] = None,
        force: bool = False,
    ) -> Optional[WifiIdentity]:
        with self._lock:
            now = self._monotonic_fn()
            if self._has_cached and not force:
                age_limit = (
                    self.failure_ttl if self._cached is None
                    else self.success_ttl if max_age is None
                    else max(0.0, max_age)
                )
                if now - self._cached_at < age_limit:
                    return self._cached

            identity = self._read_uncached()
            self._cached = identity
            self._has_cached = True
            self._cached_at = now
            return identity

    def _read_uncached(self) -> Optional[WifiIdentity]:
        helper = find_helper(self.helper_path)
        if helper is None:
            return None
        try:
            proc = self._run_fn(
                [helper, "current-network"],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        if proc.returncode != 0:
            return None
        return parse_identity(proc.stdout or "")


_DEFAULT_PROVIDER = WifiWandIdentityProvider()


def read_identity(
    max_age: Optional[float] = None,
    force: bool = False,
) -> Optional[WifiIdentity]:
    """Read current identity using the process-wide cached provider."""
    return _DEFAULT_PROVIDER.read(max_age=max_age, force=force)


__all__ = [
    "WifiIdentity",
    "WifiWandIdentityProvider",
    "find_helper",
    "parse_identity",
    "read_identity",
]
