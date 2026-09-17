"""Formatting, change detection, transports and Loxone Config templates."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import pytest

from loxmtec.datastore import ValueEntry
from loxmtec.loxone import LoxoneClient, LoxoneSettings
from loxmtec.loxone.client import format_value
from loxmtec.loxone.template import http_template, udp_template
from loxmtec.mapping import ValueMapping, ensure_defaults, load_mappings


def entry(short: str, value, unit: str = "W") -> ValueEntry:
    return ValueEntry(short=short, name=short, value=value, unit=unit, group="now-base")


def mapping(short: str = "pv", **kwargs) -> ValueMapping:
    return ValueMapping(short=short, target=kwargs.pop("target", short), **kwargs)


# ----------------------------------------------------------------------
# formatting
# ----------------------------------------------------------------------
def test_format_value_honours_decimals():
    assert format_value(1234.567, mapping(decimals=1)) == "1234.6"
    assert format_value(1234.567, mapping(decimals=0)) == "1235"
    assert format_value(42, mapping(decimals=2)) == "42.00"


def test_format_value_booleans_and_text():
    assert format_value(True, mapping()) == "1"
    assert format_value(False, mapping()) == "0"
    # Newlines would break the "<name>: <value>" UDP framing.
    assert format_value("AB\nCD  EF", mapping()) == "AB CD EF"
    assert format_value(None, mapping()) == ""


# ----------------------------------------------------------------------
# change detection
# ----------------------------------------------------------------------
def client(udp_port: int, **overrides) -> LoxoneClient:
    settings = LoxoneSettings(mode="udp", host="127.0.0.1", udp_port=udp_port, **overrides)
    return LoxoneClient(settings)


def test_unchanged_values_are_not_resent(udp_listener):
    port, _ = udp_listener
    lox = client(port)
    values, mappings = {"pv": entry("pv", 100.0)}, {"pv": mapping(decimals=0)}

    assert len(lox.build_payloads(values, mappings)) == 1
    lox.send(lox.build_payloads(values, mappings))
    assert lox.build_payloads(values, mappings) == []


def test_changed_values_are_sent_again(udp_listener):
    port, _ = udp_listener
    lox = client(port)
    values, mappings = {"pv": entry("pv", 100.0)}, {"pv": mapping(decimals=0)}
    lox.send(lox.build_payloads(values, mappings))

    values["pv"].value = 101.0
    assert [payload.text for payload in lox.build_payloads(values, mappings)] == ["101"]


def test_deadband_suppresses_small_changes(udp_listener):
    port, _ = udp_listener
    lox = client(port)
    values = {"pv": entry("pv", 100.0)}
    mappings = {"pv": mapping(decimals=0, deadband=50)}
    lox.send(lox.build_payloads(values, mappings))

    values["pv"].value = 120.0
    assert lox.build_payloads(values, mappings) == []
    values["pv"].value = 170.0
    assert len(lox.build_payloads(values, mappings)) == 1


def test_heartbeat_resends_unchanged_values(udp_listener):
    port, _ = udp_listener
    lox = client(port, heartbeat=1)
    values, mappings = {"pv": entry("pv", 100.0)}, {"pv": mapping(decimals=0)}
    lox.send(lox.build_payloads(values, mappings))

    assert lox.build_payloads(values, mappings) == []
    time.sleep(1.1)
    assert len(lox.build_payloads(values, mappings)) == 1


def test_force_ignores_change_detection(udp_listener):
    port, _ = udp_listener
    lox = client(port)
    values, mappings = {"pv": entry("pv", 100.0)}, {"pv": mapping(decimals=0)}
    lox.send(lox.build_payloads(values, mappings))
    assert len(lox.build_payloads(values, mappings, force=True)) == 1


def test_disabled_values_are_skipped(udp_listener):
    port, _ = udp_listener
    lox = client(port)
    values = {"pv": entry("pv", 100.0), "grid_power": entry("grid_power", 5.0)}
    mappings = {"pv": mapping(decimals=0), "grid_power": mapping("grid_power", enabled=False)}
    assert [payload.short for payload in lox.build_payloads(values, mappings)] == ["pv"]


def test_send_only_on_change_off_resends_every_cycle(udp_listener):
    port, _ = udp_listener
    lox = client(port, send_only_on_change=False)
    values, mappings = {"pv": entry("pv", 100.0)}, {"pv": mapping(decimals=0)}
    lox.send(lox.build_payloads(values, mappings))
    assert len(lox.build_payloads(values, mappings)) == 1


# ----------------------------------------------------------------------
# transport
# ----------------------------------------------------------------------
def test_udp_datagrams_reach_the_miniserver(udp_listener):
    port, drain = udp_listener
    lox = client(port, prefix="mtec_")
    values = {"pv": entry("pv", 4321.0), "battery_soc": entry("battery_soc", 77.5, "%")}
    mappings = {"pv": mapping(decimals=0), "battery_soc": mapping("battery_soc", decimals=1)}

    result, _ = lox.publish(values, mappings)
    assert (result.ok, result.failed) == (2, 0)
    assert sorted(drain(expected=2)) == ["mtec_battery_soc: 77.5", "mtec_pv: 4321"]


def test_test_value_bypasses_the_mapping(udp_listener):
    port, drain = udp_listener
    lox = client(port, prefix="mtec_")
    assert lox.send_test("probe", "42").ok == 1
    assert drain(expected=1) == ["mtec_probe: 42"]


def test_push_can_be_disabled():
    lox = LoxoneClient(LoxoneSettings(mode="off"))
    assert lox.enabled is False
    assert lox.send_test("probe", "1").failed == 1


def test_switching_transport_rebuilds_the_client(udp_listener):
    port, _ = udp_listener
    lox = client(port)
    assert "UDP" in lox.describe()
    lox.apply_settings(LoxoneSettings(mode="http", host="1.2.3.4", http_port=80))
    assert lox.describe().startswith("HTTP an http://1.2.3.4/dev/sps/io/")


# ----------------------------------------------------------------------
# Loxone Config templates
# ----------------------------------------------------------------------
def test_udp_template_structure(register_map):
    mappings = load_mappings(ensure_defaults({}, register_map), register_map)
    xml = udp_template(register_map, mappings, prefix="mtec_", port=7001)
    root = ET.fromstring(xml)

    assert root.tag == "VirtualInUdp"
    assert root.attrib["Port"] == "7001"
    commands = {node.attrib["Title"]: node for node in root if node.tag.endswith("Cmd")}
    assert commands["mtec_pv"].attrib["Check"] == "mtec_pv: \\v"
    assert commands["mtec_pv"].attrib["Unit"] == "<v> W"
    assert commands["mtec_battery_soc"].attrib["Unit"] == "<v.1> %"
    # Text values have no analog equivalent in Loxone.
    assert "mtec_serial_no" not in commands


# Loxone Config rejects a template that does not look exactly like its own
# exports ("Ungültiges Format oder falscher Vorlagetyp"), so pin the details
# against a template exported by Loxone Config itself.
REFERENCE_CMD_ORDER = [
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


@pytest.mark.parametrize("kind", ["udp", "http"])
def test_template_matches_the_format_loxone_exports(register_map, kind):
    mappings = load_mappings(ensure_defaults({}, register_map), register_map)
    if kind == "udp":
        xml = udp_template(register_map, mappings)
    else:
        xml = http_template(register_map, mappings, url="http://nas:8080/api/v1/values")

    # Loxone writes a UTF-8 BOM in front of the declaration.
    assert xml.startswith("\ufeff<?xml version=\"1.0\" encoding=\"utf-8\"?>")

    root = ET.fromstring(xml)
    assert root.attrib["HintText"] == ""

    # The Info element identifies the template kind and must come first.
    info = root[0]
    assert info.tag == "Info"
    assert info.attrib["templateType"].isdigit()
    assert info.attrib["minVersion"]

    command = root[1]
    assert list(command.attrib) == REFERENCE_CMD_ORDER
    # Identity scaling, not the 0/0 that an earlier version emitted.
    assert command.attrib["SourceValHigh"] == "100"
    assert command.attrib["DestValHigh"] == "100"


def test_template_type_is_overridable(register_map):
    mappings = load_mappings(ensure_defaults({}, register_map), register_map)
    root = ET.fromstring(udp_template(register_map, mappings, template_type=7))
    assert root[0].attrib["templateType"] == "7"


def test_http_template_uses_the_json_keys(register_map):
    mappings = load_mappings(ensure_defaults({}, register_map), register_map)
    url = "http://nas:8080/api/v1/values"
    root = ET.fromstring(http_template(register_map, mappings, url=url, polling_time=15))

    assert root.tag == "VirtualInHttp"
    assert root.attrib["Address"] == url
    assert root.attrib["PollingTime"] == "15"
    command = root[1]  # root[0] is the Info element
    assert command.attrib["Check"].startswith('"')
    assert command.attrib["Check"].endswith(':\\v')


def test_template_can_skip_disabled_values(register_map):
    values = ensure_defaults({}, register_map)
    for short in values:
        values[short]["enabled"] = short == "pv"
    mappings = load_mappings(values, register_map)

    only_enabled = ET.fromstring(udp_template(register_map, mappings))
    everything = ET.fromstring(udp_template(register_map, mappings, only_enabled=False))
    # +1 for the Info element
    assert len(only_enabled) == 2
    assert len(everything) > 50


# ----------------------------------------------------------------------
# partial failures
# ----------------------------------------------------------------------
class FlakyTransport:
    """Accepts everything except the targets listed in ``reject``."""

    name = "flaky"

    def __init__(self, reject: set[str]) -> None:
        self.reject = reject
        self.attempts: list[str] = []

    def send(self, payloads):
        from loxmtec.loxone.base import SendResult

        result = SendResult()
        for payload in payloads:
            self.attempts.append(payload.short)
            if payload.short in self.reject:
                result.failed += 1
                result.errors.append(f"nope: {payload.short}")
            else:
                result.sent.append(payload)
        return result

    def describe(self):
        return "flaky"

    def close(self):
        pass


def test_failed_values_are_retried_next_cycle(udp_listener):
    port, _ = udp_listener
    lox = client(port)
    transport = FlakyTransport(reject={"grid_power"})
    lox._transport = transport

    values = {"pv": entry("pv", 100.0), "grid_power": entry("grid_power", 7.0)}
    mappings = {"pv": mapping(decimals=0), "grid_power": mapping("grid_power", decimals=0)}

    result, _ = lox.publish(values, mappings)
    assert result.ok == 1 and result.failed == 1

    # pv was accepted and is unchanged -> not resent.
    # grid_power was rejected -> it must be offered again.
    transport.attempts.clear()
    lox.publish(values, mappings)
    assert transport.attempts == ["grid_power"]
