"""Per-value mapping between inverter registers and Loxone virtual inputs.

Every published register has a short name (e.g. ``pv``).  The mapping decides
whether that value is sent to Loxone at all, under which name, with how many
decimals and how much it has to change before it is re-sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from loxmtec.registers import Register, RegisterMap

# Loxone command names are used verbatim in UDP payloads and in URLs, so keep
# them to a boring, unambiguous character set.
_SAFE_TARGET = re.compile(r"[^A-Za-z0-9_.\-]")
MAX_TARGET_LENGTH = 48

# Sensible decimal defaults per unit; anything else falls back to the scale.
_UNIT_DECIMALS = {
    "W": 0,
    "Wh": 0,
    "kWh": 2,
    "V": 1,
    "A": 1,
    "Hz": 2,
    "%": 1,
    "°C": 1,
    "h": 1,
}


def sanitize_target(name: str, fallback: str = "value") -> str:
    """Make ``name`` safe to use as a Loxone virtual input name."""
    cleaned = _SAFE_TARGET.sub("_", str(name or "").strip())
    cleaned = re.sub(r"_{2,}", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = _SAFE_TARGET.sub("_", fallback) or "value"
    return cleaned[:MAX_TARGET_LENGTH]


def default_decimals(register: Register) -> int:
    if not register.is_numeric:
        return 0
    if register.unit in _UNIT_DECIMALS:
        return _UNIT_DECIMALS[register.unit]
    if register.scale >= 100:
        return 2
    if register.scale >= 10:
        return 1
    return 0


@dataclass
class ValueMapping:
    """Settings of a single value."""

    short: str
    enabled: bool = True
    target: str = ""
    decimals: int = 2
    deadband: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "target": self.target,
            "decimals": int(self.decimals),
            "deadband": float(self.deadband),
        }


def build_default(register: Register) -> ValueMapping:
    return ValueMapping(
        short=register.short,
        enabled=True,
        target=sanitize_target(register.short),
        decimals=default_decimals(register),
        deadband=0.0,
    )


def ensure_defaults(values: dict[str, Any], register_map: RegisterMap) -> dict[str, Any]:
    """Add a default entry for every register that has none yet.

    Existing entries are kept (and repaired where necessary); entries whose
    register disappeared from the register map are dropped.
    """
    result: dict[str, Any] = {}
    for register in register_map.published():
        stored = values.get(register.short)
        default = build_default(register)
        if not isinstance(stored, dict):
            result[register.short] = default.as_dict()
            continue
        result[register.short] = ValueMapping(
            short=register.short,
            enabled=bool(stored.get("enabled", default.enabled)),
            target=sanitize_target(stored.get("target") or default.target, register.short),
            decimals=_as_int(stored.get("decimals"), default.decimals, 0, 6),
            deadband=_as_float(stored.get("deadband"), default.deadband),
        ).as_dict()
    return result


def load_mappings(values: dict[str, Any], register_map: RegisterMap) -> dict[str, ValueMapping]:
    """Turn the raw config section into :class:`ValueMapping` objects."""
    mappings: dict[str, ValueMapping] = {}
    for register in register_map.published():
        stored = values.get(register.short)
        if not isinstance(stored, dict):
            mappings[register.short] = build_default(register)
            continue
        mappings[register.short] = ValueMapping(
            short=register.short,
            enabled=bool(stored.get("enabled", True)),
            target=sanitize_target(stored.get("target") or register.short, register.short),
            decimals=_as_int(stored.get("decimals"), default_decimals(register), 0, 6),
            deadband=_as_float(stored.get("deadband"), 0.0),
        )
    return mappings


def duplicate_targets(mappings: dict[str, ValueMapping], prefix: str = "") -> dict[str, list[str]]:
    """Find enabled values that would write to the same Loxone input."""
    seen: dict[str, list[str]] = {}
    for short, mapping in mappings.items():
        if not mapping.enabled:
            continue
        seen.setdefault(f"{prefix}{mapping.target}", []).append(short)
    return {target: shorts for target, shorts in seen.items() if len(shorts) > 1}


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _as_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, number)
