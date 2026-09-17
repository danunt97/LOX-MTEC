"""Calculated pseudo registers (consumption, autarky, own consumption rate).

These are not read from the inverter but derived from other registers - the
formulas are taken over from the original ``mtecmqtt`` project.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from loxmtec.modbus import Value

logger = logging.getLogger(__name__)

# key -> the Modbus registers that have to be present to calculate it
DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "consumption": ("11016", "11000"),
    "consumption-day": ("31005", "31001", "31004", "31000", "31003"),
    "autarky-day": ("31001",),
    "ownconsumption-day": ("31000", "31005"),
    "consumption-total": ("31112", "31104", "31110", "31102", "31108"),
    "autarky-total": ("31104",),
    "ownconsumption-total": ("31102", "31112"),
    "api-date": (),
}


def _num(data: dict[str, Value], key: str) -> float | None:
    entry = data.get(key)
    if entry is None or not isinstance(entry.value, (int, float)):
        return None
    return float(entry.value)


def calculate(key: str, data: dict[str, Value]) -> Any:
    """Return the value of a pseudo register, or ``None`` if data is missing."""
    if key == "api-date":
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    needed = DEPENDENCIES.get(key)
    if needed is None:
        logger.warning("Unknown calculated pseudo-register: %s", key)
        return None

    values = {reg: _num(data, reg) for reg in needed}
    if any(value is None for value in values.values()):
        return None

    if key == "consumption":
        result = values["11016"] - values["11000"]
    elif key == "consumption-day":
        result = (
            values["31005"] + values["31001"] + values["31004"] - values["31000"] - values["31003"]
        )
    elif key == "autarky-day":
        consumption = calculate("consumption-day", data)
        if consumption is None:
            return None
        result = 100 * (1 - values["31001"] / consumption) if consumption > 0 else 0
    elif key == "ownconsumption-day":
        result = 100 * (1 - values["31000"] / values["31005"]) if values["31005"] > 0 else 0
    elif key == "consumption-total":
        result = (
            values["31112"] + values["31104"] + values["31110"] - values["31102"] - values["31108"]
        )
    elif key == "autarky-total":
        consumption = calculate("consumption-total", data)
        if consumption is None:
            return None
        result = 100 * (1 - values["31104"] / consumption) if consumption > 0 else 0
    elif key == "ownconsumption-total":
        result = 100 * (1 - values["31102"] / values["31112"]) if values["31112"] > 0 else 0
    else:  # pragma: no cover - DEPENDENCIES and this branch are kept in sync
        logger.warning("No formula implemented for pseudo-register: %s", key)
        return None

    # Rounding and metering edge cases can produce small negative numbers.
    return max(0.0, float(result))
