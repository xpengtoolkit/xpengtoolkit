"""Translate the AC current-limit wire index to and from whole amperes.

The official app's charging slider sends 1..27 and displays that value + 5 A.
Both cmd_type 7 and electric_current_limit use the wire index, not amperes.
"""
from __future__ import annotations

from typing import Any


def _whole_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(number) if number.is_integer() else None


def current_limit_amperes(raw: Any) -> int | None:
    """Decode a reported 1..27 index; missing/invalid readings stay unknown."""
    value = _whole_number(raw)
    return value + 5 if value is not None and 1 <= value <= 27 else None


def current_limit_index(amperes: Any) -> int:
    """Encode a whole-amp 6..32 A request for service 4, cmd_type 7."""
    value = _whole_number(amperes)
    if value is None or not 6 <= value <= 32:
        raise ValueError("AC charge current must be a whole number from 6 to 32 A")
    return value - 5
