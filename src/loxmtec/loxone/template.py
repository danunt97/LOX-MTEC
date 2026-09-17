"""Generate Loxone Config templates for the virtual inputs.

Instead of creating 80+ virtual inputs by hand, the generated XML can be put
into the Loxone Config template folder and inserted in one go.

The structure follows a template exported by Loxone Config itself, which is
stricter than it looks:

* an ``<Info templateType=... minVersion=.../>`` element has to be the first
  child - without it Loxone rejects the file as "falscher Vorlagetyp"
* the file starts with a UTF-8 BOM
* ``HintText`` is expected on the root element and on every command
* the scaling attributes are ordered Source/Dest low, then Source/Dest high,
  and describe an identity mapping (0->0, 100->100)

Only numeric values are exported: a Loxone virtual input command is either
analog or digital, text values (serial number, dates, bit patterns) have no
equivalent and stay available through the REST API and the web GUI.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Iterable

from loxmtec.mapping import ValueMapping
from loxmtec.registers import Register, RegisterMap

BOM = "﻿"
XML_HEADER = '<?xml version="1.0" encoding="utf-8"?>\n'

# templateType identifies the kind of template. 2 = virtueller HTTP-Eingang,
# taken from a template exported by Loxone Config. The UDP value is not
# confirmed yet, which is why it can be overridden per request.
TEMPLATE_TYPE_HTTP = 2
TEMPLATE_TYPE_UDP = 3
MIN_VERSION = "17010630"

# Shared attributes of a virtual input command, in the order Loxone writes them.
# The scaling is an identity mapping - the container already delivers the value
# in its final unit.
_COMMON_CMD_ATTRS = {
    "Comment": "",
    "Signed": "true",
    "Analog": "true",
    "SourceValLow": "0",
    "DestValLow": "0",
    "SourceValHigh": "100",
    "DestValHigh": "100",
    "DefVal": "0",
    "MinVal": "-1000000",
    "MaxVal": "1000000",
    "HintText": "",
}

_CMD_ORDER = [
    "Title",
    "Comment",
    "Check",
    "Signed",
    "Analog",
    "SourceValLow",
    "DestValLow",
    "SourceValHigh",
    "DestValHigh",
    "DefVal",
    "MinVal",
    "MaxVal",
    "Unit",
    "HintText",
]


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


def _add_info(root: ET.Element, template_type: int) -> None:
    """Loxone identifies the template kind by this element - it must come first."""
    ET.SubElement(
        root, "Info", {"templateType": str(template_type), "minVersion": MIN_VERSION}
    )


def _serialize(root: ET.Element) -> str:
    ET.indent(root, space="\t")
    xml = ET.tostring(root, encoding="unicode")
    # Loxone's own exports write "<Tag .../>" without the space ElementTree adds.
    xml = xml.replace(" />", "/>")
    return BOM + XML_HEADER + xml + "\n"


def udp_template(
    register_map: RegisterMap,
    mappings: dict[str, ValueMapping],
    prefix: str = "",
    port: int = 7000,
    title: str = "LOX-MTEC",
    only_enabled: bool = True,
    template_type: int = TEMPLATE_TYPE_UDP,
) -> str:
    """Template for a 'Virtueller UDP Eingang' receiving the pushed values."""
    root = ET.Element(
        "VirtualInUdp",
        {
            "HintText": "",
            "Title": title,
            "Comment": "M-TEC Energybutler via LOX-MTEC",
            "Address": "",
            "Port": str(port),
        },
    )
    _add_info(root, template_type)
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
    template_type: int = TEMPLATE_TYPE_HTTP,
) -> str:
    """Template for a 'Virtueller HTTP Eingang' polling the container's REST API."""
    root = ET.Element(
        "VirtualInHttp",
        {
            "HintText": "",
            "Title": title,
            "Comment": "M-TEC Energybutler via LOX-MTEC",
            "Address": url,
            "PollingTime": str(max(1, int(polling_time))),
        },
    )
    _add_info(root, template_type)
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
    return {key: attrs[key] for key in _CMD_ORDER if key in attrs}
