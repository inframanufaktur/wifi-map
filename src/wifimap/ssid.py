"""Location-scoped SSID selection shared by scan and walk startup."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, List, Optional

from wifimap import store as store_mod


class SSIDSelectionError(ValueError):
    """Raised when startup cannot produce a selected SSID."""


class SSIDSelectionCancelled(SSIDSelectionError):
    """Raised when the user cancels interactive SSID setup."""


@dataclass(frozen=True)
class SSIDOption:
    kind: str
    name: Optional[str]
    ssid_id: Optional[int]


def selection_options(
    conn,
    location_id: int,
    detected_name: Optional[str],
) -> List[SSIDOption]:
    """Return existing, optional detected-create, and manual choices."""
    detected = detected_name.strip() if detected_name else None
    existing = store_mod.list_ssids(conn, location_id)
    options = [
        SSIDOption(kind="existing", name=ssid.name, ssid_id=ssid.id)
        for ssid in existing
    ]
    if detected and not any(ssid.name == detected for ssid in existing):
        options.append(
            SSIDOption(kind="detected", name=detected, ssid_id=None)
        )
    options.append(SSIDOption(kind="manual", name=None, ssid_id=None))
    return options


def preferred_option(
    options: List[SSIDOption],
    detected_name: Optional[str],
) -> Optional[SSIDOption]:
    """Prefer the existing/detected option matching automatic detection."""
    detected = detected_name.strip() if detected_name else None
    if detected:
        for option in options:
            if option.name == detected:
                return option
    return None


def _option_label(option: SSIDOption) -> str:
    if option.kind == "existing":
        return option.name or ""
    if option.kind == "detected":
        return "Create detected SSID: %s" % option.name
    return "Create SSID manually"


def _resolve_option(
    conn,
    location_id: int,
    option: SSIDOption,
    input_fn: Callable[[str], str],
) -> Optional[store_mod.SSID]:
    if option.kind == "existing" and option.ssid_id is not None:
        return store_mod.get_ssid(conn, option.ssid_id)
    if option.kind == "detected" and option.name:
        ssid_id = store_mod.resolve_ssid(conn, location_id, option.name)
        return store_mod.get_ssid(conn, ssid_id)
    try:
        name = input_fn("SSID name: ").strip()
    except (EOFError, OSError):
        raise SSIDSelectionCancelled("SSID selection cancelled")
    if not name:
        return None
    ssid_id = store_mod.resolve_ssid(conn, location_id, name)
    return store_mod.get_ssid(conn, ssid_id)


def select_ssid_line(
    conn,
    location_id: int,
    detected_name: Optional[str] = None,
    requested: Optional[str] = None,
    interactive: Optional[bool] = None,
    input_fn: Callable[[str], str] = input,
    write_fn: Callable[[str], None] = print,
) -> store_mod.SSID:
    """Resolve a flag or interactively select/create one location SSID."""
    if requested is not None:
        requested = requested.strip()
        if not requested:
            raise SSIDSelectionError("--ssid must not be blank")
        ssid_id = store_mod.resolve_ssid(conn, location_id, requested)
        selected = store_mod.get_ssid(conn, ssid_id)
        if selected is None:  # defensive: resolve_ssid guarantees a row
            raise SSIDSelectionError("SSID selection failed")
        return selected
    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        raise SSIDSelectionError(
            "SSID selection requires a terminal; pass --ssid NAME"
        )

    options = selection_options(conn, location_id, detected_name)
    preferred = preferred_option(options, detected_name)
    while True:
        write_fn("Select SSID:")
        for index, option in enumerate(options, start=1):
            marker = " [detected]" if option == preferred else ""
            write_fn("%d. %s%s" % (index, _option_label(option), marker))
        default = options.index(preferred) + 1 if preferred is not None else None
        suffix = ", Enter=%d" % default if default is not None else ""
        try:
            raw = input_fn("SSID [number%s, q cancel]: " % suffix).strip()
        except (EOFError, OSError):
            raise SSIDSelectionCancelled("SSID selection cancelled")
        if raw.lower() in ("q", "quit"):
            raise SSIDSelectionCancelled("SSID selection cancelled")
        if not raw and default is not None:
            index = default - 1
        else:
            try:
                index = int(raw) - 1
            except ValueError:
                continue
        if not 0 <= index < len(options):
            continue
        selected = _resolve_option(conn, location_id, options[index], input_fn)
        if selected is not None:
            return selected


__all__ = [
    "SSIDOption",
    "SSIDSelectionCancelled",
    "SSIDSelectionError",
    "preferred_option",
    "select_ssid_line",
    "selection_options",
]
