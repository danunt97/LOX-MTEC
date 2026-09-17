"""Generate Loxone Config templates for the virtual inputs.

Instead of creating 80+ virtual inputs by hand, the generated XML can be
imported in Loxone Config (right click on the Miniserver -> "Vorlage
einfügen" / import) so every value shows up with the correct command
recognition and unit.

Only numeric values are exported: a Loxone virtual input command is either
analog or digital, text values (serial number, dates, bit patterns) have no
equivalent and stay available through the REST API and the web GUI.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Iterable

from loxmtec.mapping import ValueMapping
from loxmtec.registers import Register, RegisterMap

XML_HEADER = '<?xml version="1.0" encoding="utf-8"?>\n'

# Shared attributes of a virtual input command. 1:1 scaling, generous limits -
# the container already delivers the value in its final unit.
_COMMON_CMD_ATTRS = {
    "Comment": "",
    "Signed": "true",
    "Analog": "true",
    "SourceValLow": "0",
    "SourceValHigh": "0",
    "DestValLow": "0",
    "DestValHigh": "0",
    "DefVal": "0",
    "MinVal": "-1000000000",
    "MaxVal": "1000000000",
}


def unit_string(register: Register, mapping: ValueMapping) -> str:
    """Loxone unit format, e.g. ``<v.1> W``."""
    placeholder = f"<v.{mapping.decimals}>" if mapping.decimals > 0 else "<v>"
    unit = (register.unit or "").strip()
    return f"{placeholder} {unit}".strip()


def _exportable(
    register_map: RegisterMap, mappings: dict[str, ValueMapping], only_enabled: bool = True
) -> Iterable[tuple[Register, ValueMapping]]:
    for register in register_map.published():
        mapping = mappings.get(register.short)
        if mapping is None:
            continue
        if only_enabled and not mapping.enabled:
            continue
        if not register.is_numeric:
            continue
        yield register, mapping


def _serialize(root: ET.Element) -> str:
    ET.indent(root, space="\t")
    return XML_HEADER + ET.tostring(root, encoding="unicode") + "\n"


def udp_template(
    register_map: RegisterMap,
    mappings: dict[str, ValueMapping],
    prefix: str = "",
    port: int = 7000,
    title: str = "LOX-MTEC",
    only_enabled: bool = True,
) -> str:
    """Template for a 'Virtueller UDP Eingang' receiving the pushed values."""
    root = ET.Element(
        "VirtualInUdp",
        {
            "Title": title,
            "Comment": "M-TEC Energybutler via LOX-MTEC",
            "Address": "",
            "Port": str(port),
        },
    )
    for register, mapping in _exportable(register_map, mappings, only_enabled):
        target = f"{prefix}{mapping.target}"
        attrs = dict(_COMMON_CMD_ATTRS)
        attrs.update(
            {
                "Title": target,
                "Comment": register.name,
                "Check": f"{target}: \\v",
                "Unit": unit_string(register, mapping),
            }
        )
        ET.SubElement(root, "VirtualInUdpCmd", _ordered(attrs))
    return _serialize(root)


def http_template(
    register_map: RegisterMap,
    mappings: dict[str, ValueMapping],
    url: str,
    prefix: str = "",
    polling_time: int = 10,
    title: str = "LOX-MTEC",
    only_enabled: bool = True,
) -> str:
    """Template for a 'Virtueller HTTP Eingang' polling the container's REST API."""
    root = ET.Element(
        "VirtualInHttp",
        {
            "Title": title,
            "Comment": "M-TEC Energybutler via LOX-MTEC",
            "Address": url,
            "PollingTime": str(max(1, int(polling_time))),
        },
    )
    for register, mapping in _exportable(register_map, mappings, only_enabled):
        target = f"{prefix}{mapping.target}"
        attrs = dict(_COMMON_CMD_ATTRS)
        attrs.update(
            {
                "Title": target,
                "Comment": register.name,
                # The REST API answers with flat JSON: {"pv": 1234.0, ...}
                "Check": f'"{target}":\\v',
                "Unit": unit_string(register, mapping),
            }
        )
        ET.SubElement(root, "VirtualInHttpCmd", _ordered(attrs))
    return _serialize(root)


def _ordered(attrs: dict[str, str]) -> dict[str, str]:
    """Keep the attribute order Loxone Config uses in its own exports."""
    order = [
        "Title",
        "Comment",
        "Check",
        "Signed",
        "Analog",
        "SourceValLow",
        "SourceValHigh",
        "DestValLow",
        "DestValHigh",
        "DefVal",
        "MinVal",
        "MaxVal",
        "Unit",
    ]
    return {key: attrs[key] for key in order if key in attrs}
