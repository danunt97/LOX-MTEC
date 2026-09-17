"""Ready-made value configurations for Loxone blocks.

A Loxone block expects its own units and sign conventions. The Energiemonitor
for example wants kW where the inverter reports W, and counts grid power
positive on import while the inverter counts it positive on export. Rather than
wiring correction blocks in Loxone Config, the container can deliver the values
in exactly the shape the block wants - a preset sets the factor, the decimals
and the unit label for every value the block needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Watt -> Kilowatt, and the same with the sign flipped.
W_TO_KW = 0.001
W_TO_KW_INVERTED = -0.001


@dataclass(frozen=True)
class PresetEntry:
    """How one value has to look for the target block."""

    factor: float
    decimals: int
    unit: str
    note: str = ""  # which input of the block this feeds

    def apply_to(self, entry: dict[str, Any]) -> dict[str, Any]:
        updated = dict(entry)
        updated.update(
            {
                "enabled": True,
                "factor": self.factor,
                "decimals": self.decimals,
                "unit": self.unit,
            }
        )
        return updated


@dataclass(frozen=True)
class Preset:
    key: str
    title: str
    description: str
    values: dict[str, PresetEntry]

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "description": self.description,
            "values": {
                short: {
                    "factor": entry.factor,
                    "decimals": entry.decimals,
                    "unit": entry.unit,
                    "note": entry.note,
                }
                for short, entry in self.values.items()
            },
        }


# Decimals the raw values deserve when the conversion is switched off again.
_RAW_DECIMALS = {
    "pv": 0,
    "grid_power": 0,
    "battery": 0,
    "battery_soc": 1,
    "pv_total": 1,
    "grid_purchase_total": 1,
    "grid_feed_total": 1,
}

ENERGIEMONITOR = Preset(
    key="energiemonitor",
    title="Loxone Energiemonitor (Emo)",
    description=(
        "Liefert die sieben Werte, die der Energiemonitor-Baustein erwartet, in kW "
        "bzw. kWh und mit den Vorzeichen des Bausteins. Am Baustein zusätzlich "
        "Datenquelle 'Objekteingänge', Parameter Abs = 1 und die Speicherkapazität setzen."
    ),
    values={
        # Leistungen: W -> kW
        "pv": PresetEntry(W_TO_KW, 3, "kW", "Ppwr - Produktionsleistung"),
        # Der Wechselrichter zählt Einspeisung positiv, Emo den Bezug positiv.
        "grid_power": PresetEntry(W_TO_KW_INVERTED, 3, "kW", "Gpwr - Netzleistung"),
        # Entladen ist bei beiden positiv, hier reicht die Umrechnung.
        "battery": PresetEntry(W_TO_KW, 3, "kW", "Spwr - Speicherleistung"),
        "battery_soc": PresetEntry(1.0, 1, "%", "SoC - Ladezustand"),
        # Zählerstände sind bereits kWh und werden absolut geliefert (Abs = 1).
        "pv_total": PresetEntry(1.0, 1, "kWh", "Ptot - Produktion gesamt"),
        "grid_purchase_total": PresetEntry(1.0, 1, "kWh", "Gi - Netz Energie Import"),
        "grid_feed_total": PresetEntry(1.0, 1, "kWh", "Ge - Netz Energie Export"),
    },
)

RAW = Preset(
    key="raw",
    title="Rohwerte wie vom Wechselrichter",
    description=(
        "Hebt die Umrechnung wieder auf: Leistungen kommen dann in W statt kW und mit "
        "dem Vorzeichen des Wechselrichters. Sinnvoll, wenn du die Werte nicht für den "
        "Energiemonitor, sondern für eigene Bausteine verwendest."
    ),
    values={
        short: PresetEntry(1.0, _RAW_DECIMALS.get(short, 0), "", entry.note)
        for short, entry in ENERGIEMONITOR.values.items()
    },
)

PRESETS: dict[str, Preset] = {
    ENERGIEMONITOR.key: ENERGIEMONITOR,
    RAW.key: RAW,
}

# Applied to a freshly created configuration so the container delivers
# Energiemonitor-ready values without anyone having to configure anything.
DEFAULT_PRESET_KEY = ENERGIEMONITOR.key


def apply_preset(key: str, values: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return the value config with the preset applied, plus the names it touched.

    Values the preset does not mention are left alone - it enables and
    configures what the block needs, it does not disable anything else.
    """
    preset = PRESETS.get(key)
    if preset is None:
        raise KeyError(key)

    result = dict(values)
    applied: list[str] = []
    for short, entry in preset.values.items():
        current = result.get(short)
        if not isinstance(current, dict):
            continue  # register not in this register map
        result[short] = entry.apply_to(current)
        applied.append(short)
    return result, applied


def apply_default(values: dict[str, Any]) -> dict[str, Any]:
    """Shape a fresh configuration for the Energiemonitor.

    New installations should work with the Loxone block straight away, so the
    default preset is applied once, when the value list is first created. It is
    never applied again - afterwards the configuration belongs to the user.
    """
    result, _applied = apply_preset(DEFAULT_PRESET_KEY, values)
    return result
