"""Loading and normalising the M-TEC register map.

The register definitions are shared with the original ``mtecmqtt`` package so
there is exactly one source of truth.  The location can be overridden with the
``LOXMTEC_REGISTERS`` environment variable.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml

logger = logging.getLogger(__name__)

# Optional keys and their defaults, mirroring mtecmqtt.config.init_register_map()
_OPTIONAL = {
    "length": None,
    "type": None,
    "unit": "",
    "scale": 1,
    "writable": False,
    "mqtt": None,
    "group": None,
}


def _register_file() -> Path:
    override = os.environ.get("LOXMTEC_REGISTERS")
    if override:
        return Path(override)
    # Shipped with the mtecmqtt package which lives next to this one.
    return Path(__file__).resolve().parent.parent / "mtecmqtt" / "registers.yaml"


@dataclass(frozen=True)
class Register:
    """One entry of the register map."""

    key: str
    name: str
    short: str  # stable identifier used for Loxone targets and the REST API
    unit: str = ""
    scale: int = 1
    length: int | None = None
    type: str | None = None
    group: str | None = None
    writable: bool = False
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_modbus(self) -> bool:
        """False for calculated pseudo registers such as ``consumption``."""
        return self.key.isnumeric()

    @property
    def is_numeric(self) -> bool:
        """True if the decoded value is a number (and thus Loxone-analog).

        Calculated pseudo registers carry no Modbus type; they are numeric
        exactly when they have a unit (``api-date`` is the odd one out).
        """
        if self.type is None:
            return bool(self.unit)
        return self.type in ("U16", "I16", "U32", "I32")


class RegisterMap:
    """Normalised view on ``registers.yaml``."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self._by_key: dict[str, Register] = {}
        self._by_short: dict[str, Register] = {}
        self.groups: list[str] = []

        for key, value in (raw or {}).items():
            if not isinstance(value, dict):
                logger.warning("Skipping malformed register entry: %s", key)
                continue
            if not value.get("name"):
                logger.warning("Skipping register %s: mandatory parameter 'name' missing", key)
                continue

            item = dict(value)
            for opt, default in _OPTIONAL.items():
                if not item.get(opt):
                    item[opt] = default

            short = item["mqtt"]
            register = Register(
                key=str(key),
                name=str(item["name"]).strip(),
                short=str(short).strip() if short else "",
                unit=str(item["unit"] or ""),
                scale=int(item["scale"] or 1),
                length=item["length"],
                type=item["type"],
                group=item["group"],
                writable=bool(item["writable"]),
                extra={k: v for k, v in item.items() if k.startswith("hass_")},
            )
            self._by_key[register.key] = register
            if register.short:
                if register.short in self._by_short:
                    logger.warning(
                        "Duplicate short name '%s' (registers %s and %s) - keeping the first",
                        register.short,
                        self._by_short[register.short].key,
                        register.key,
                    )
                else:
                    self._by_short[register.short] = register
            if register.group and register.group not in self.groups:
                self.groups.append(register.group)

    # ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._by_key)

    def __iter__(self) -> Iterator[Register]:
        return iter(self._by_key.values())

    def __contains__(self, key: object) -> bool:
        return key in self._by_key

    def get(self, key: str) -> Register | None:
        return self._by_key.get(str(key))

    def by_short(self, short: str) -> Register | None:
        return self._by_short.get(short)

    @property
    def shorts(self) -> list[str]:
        """All published value names, in register-map order."""
        return list(self._by_short)

    def published(self) -> list[Register]:
        """Registers that carry a short name, i.e. that end up as a value."""
        return list(self._by_short.values())

    def group_keys(self, group: str) -> list[str]:
        """Register keys belonging to ``group`` (includes pseudo registers)."""
        return [reg.key for reg in self._by_key.values() if reg.group == group]


def load_register_map(path: Path | str | None = None) -> RegisterMap:
    """Read and normalise the register map."""
    fname = Path(path) if path else _register_file()
    with fname.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    register_map = RegisterMap(raw)
    logger.info(
        "Loaded %d registers (%d published values) from %s",
        len(register_map),
        len(register_map.shorts),
        fname,
    )
    return register_map
