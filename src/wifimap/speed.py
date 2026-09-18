"""Internet throughput via the Ookla ``speedtest`` binary (stdlib only).

On failure the caller stores NULLs for down/up with ``server="ERROR"``
(per spec section 4) and keeps the signal row — see ``scan``/``walk``.
Use ``--no-speedtest`` to skip entirely. No live network is used in tests
(``subprocess.run`` is mocked).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, Optional


# Ookla machine-readable JSON reports throughput as BYTES/sec in
# download/upload.bandwidth (man speedtest). Mbps = bytes/sec / 125_000.
_BYTES_PER_SEC_PER_MBPS = 125_000.0

# First run of Ookla speedtest 1.2 prints a EULA/GDPR prompt and blocks on
# stdin, which hangs/fails non-interactive scans. These documented flags
# auto-accept so every invocation is non-interactive.
_OOKLA_ARGV = [
    "speedtest", "--format=json", "--accept-license", "--accept-gdpr",
]


class SpeedtestUnavailableError(Exception):
    """Raised when the Ookla binary is missing (scan exits 4 when required)."""


class SpeedtestFailedError(Exception):
    """Raised on timeout / non-zero exit / unparseable output (caller stores NULLs)."""


@dataclass
class Speed:
    ping_ms: Optional[float] = None
    down_mbps: Optional[float] = None
    up_mbps: Optional[float] = None
    server: Optional[str] = None


def _server_name(server: Any) -> Optional[str]:
    if not isinstance(server, dict):
        return None
    name = server.get("name")
    sid = server.get("id")
    if name and sid is not None:
        return "%s (%s)" % (name, sid)
    if name:
        return str(name)
    if sid is not None:
        return "id:%s" % (sid,)
    return None


def run_speedtest(timeout: float = 120.0) -> Speed:
    """Run Ookla ``speedtest --format=json`` and parse down/up Mbps + ping ms.

    ``--accept-license --accept-gdpr`` keep first runs non-interactive
    (Ookla 1.2 otherwise prompts on stdin and the scan stores NULLs).
    """
    try:
        proc = subprocess.run(
            _OOKLA_ARGV,
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise SpeedtestUnavailableError(
            "Ookla speedtest binary not found: install it "
            "(brew install --cask speedtest-cli, see "
            "https://www.speedtest.net/apps/cli) or use --no-speedtest"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SpeedtestFailedError(
            "speedtest timed out after %ss" % (timeout,)) from exc
    if proc.returncode != 0:
        raise SpeedtestFailedError(
            "speedtest failed (rc=%s): %s" % (
                proc.returncode, (proc.stderr or "").strip()))
    try:
        data: Dict[str, Any] = json.loads(proc.stdout or "")
    except ValueError as exc:
        raise SpeedtestFailedError(
            "speedtest returned invalid JSON") from exc
    try:
        ping_ms = float(data["ping"]["latency"])
        down_mbps = float(data["download"]["bandwidth"]) / _BYTES_PER_SEC_PER_MBPS
        up_mbps = float(data["upload"]["bandwidth"]) / _BYTES_PER_SEC_PER_MBPS
    except (KeyError, TypeError, ValueError) as exc:
        raise SpeedtestFailedError(
            "unexpected speedtest JSON shape") from exc
    return Speed(
        ping_ms=ping_ms,
        down_mbps=down_mbps,
        up_mbps=up_mbps,
        server=_server_name(data.get("server")),
    )
