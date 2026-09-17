"""Full path: fake inverter -> Modbus -> data store -> Loxone UDP -> REST API."""

from __future__ import annotations

import base64
import json
import time

import pytest

from loxmtec.context import AppContext
from loxmtec.datastore import DataStore
from loxmtec.modbus import ModbusError, ModbusSettings, MTECModbus
from loxmtec.poller import Poller
from loxmtec.web import create_app


@pytest.fixture
def wired(config, register_map, inverter, udp_listener):
    """A poller connected to the fake inverter, pushing to the UDP listener."""
    fake, modbus_port = inverter
    udp_port, drain = udp_listener
    config.update_section(
        "modbus",
        {
            "host": "127.0.0.1",
            "port": modbus_port,
            "port_fallback": modbus_port,
            "slave": fake.slave,
            "timeout": 2,
        },
    )
    config.update_section(
        "loxone", {"mode": "udp", "host": "127.0.0.1", "udp_port": udp_port, "prefix": "mtec_"}
    )
    config.update_section("poll", {"now": 2})

    store = DataStore()
    poller = Poller(config, register_map, store)
    yield poller, store, fake, drain
    poller.stop()
    if poller.is_alive():
        poller.join(timeout=5)
    poller.modbus.disconnect()


# ----------------------------------------------------------------------
# Modbus layer
# ----------------------------------------------------------------------
def test_read_group_decodes_and_scales(config, register_map, inverter):
    fake, port = inverter
    modbus = MTECModbus(
        ModbusSettings(host="127.0.0.1", port=port, port_fallback=port, slave=fake.slave),
        register_map,
    )
    assert modbus.connect()

    data = modbus.read_group("now-base")
    assert data["11028"].value == 4321  # U32, no scaling
    assert data["33000"].value == 77.5  # U16 scaled by 100
    assert data["30254"].value == 230.0  # U16 scaled by 10
    modbus.disconnect()


def test_strings_are_decoded_without_padding(config, register_map, inverter):
    fake, port = inverter
    modbus = MTECModbus(
        ModbusSettings(host="127.0.0.1", port=port, port_fallback=port, slave=fake.slave),
        register_map,
    )
    modbus.connect()
    assert modbus.read_group("config")["10000"].value == "SIM12345"
    modbus.disconnect()


def test_reading_without_connection_raises(config, register_map):
    modbus = MTECModbus(ModbusSettings(host="127.0.0.1", port=1), register_map)
    with pytest.raises(ModbusError):
        modbus.read_group("now-base")


def test_connect_falls_back_to_the_second_port(config, register_map, inverter):
    fake, port = inverter
    modbus = MTECModbus(
        ModbusSettings(host="127.0.0.1", port=1, port_fallback=port, slave=fake.slave),
        register_map,
    )
    assert modbus.connect() is True
    modbus.disconnect()


def test_unknown_group_raises(config, register_map):
    modbus = MTECModbus(ModbusSettings(), register_map)
    with pytest.raises(ModbusError):
        modbus.read_group("no-such-group")


# ----------------------------------------------------------------------
# Poller
# ----------------------------------------------------------------------
def test_one_cycle_fills_the_store_and_pushes_udp(wired):
    poller, store, _fake, drain = wired
    assert poller.modbus.connect()
    poller.cycle()

    assert store.get("pv").value == 4321
    assert store.get("consumption").value == 1800.0  # calculated pseudo register
    assert store.get("battery_soc").value == 77.5
    assert store.modbus_state == "ok"

    sent = drain(expected=5)
    assert "mtec_pv: 4321" in sent
    assert "mtec_consumption: 1800" in sent


def test_only_changed_values_are_sent_again(wired):
    poller, store, fake, drain = wired
    poller.modbus.connect()
    # The extended "now-*" groups are read round-robin, so it takes a few
    # cycles until every value has been seen once.
    for _ in range(7):
        poller.cycle()
    drain(expected=5)

    poller.cycle()
    # Nothing moved, so at most the timestamp (which is regenerated on every
    # read) goes out again.
    assert set(line.split(":")[0] for line in drain(timeout=1)) <= {"mtec_api_date"}

    fake.set_u32(11028, 5000)
    poller.cycle()
    assert "mtec_pv: 5000" in drain(expected=2)


def test_resend_pushes_everything_again(wired):
    poller, store, _fake, drain = wired
    poller.modbus.connect()
    poller.cycle()
    first = len(drain(expected=5))

    poller.cycle(force_send=True)
    assert len(drain(expected=first)) >= first


def test_serial_number_is_tracked(wired):
    poller, store, _fake, _drain = wired
    poller.modbus.connect()
    poller.cycle()
    assert store.serial_no == "SIM12345"


def test_poller_thread_runs_and_stops_cleanly(wired):
    poller, store, _fake, _drain = wired
    poller.start()
    deadline = time.time() + 10
    while time.time() < deadline and store.modbus_reads_ok == 0:
        time.sleep(0.2)

    assert store.modbus_reads_ok > 0
    assert store.healthy(stale_after=60)
    poller.stop()
    poller.join(timeout=5)
    assert not poller.is_alive()


def test_inverter_going_away_is_reported(wired):
    poller, store, fake, _drain = wired
    poller.modbus.connect()
    poller.cycle()
    assert store.modbus_state == "ok"

    fake.stop()
    poller.modbus.disconnect()
    poller.cycle()
    assert store.modbus_state == "error"
    assert store.healthy(stale_after=0) is False


# ----------------------------------------------------------------------
# Web / REST API
# ----------------------------------------------------------------------
@pytest.fixture
def web(wired, config, register_map):
    poller, store, fake, drain = wired
    poller.modbus.connect()
    poller.cycle()
    ctx = AppContext(
        config=config, registers=register_map, store=store, poller=poller, version="test"
    )
    return create_app(ctx).test_client(), config, store, poller


def test_pages_render(web):
    client, *_ = web
    for path in ("/", "/values", "/settings", "/loxone", "/log"):
        assert client.get(path).status_code == 200, path


def test_flat_values_endpoint_is_what_loxone_polls(web):
    client, *_ = web
    payload = client.get("/api/v1/values").get_json()
    assert payload["pv"] == 4321
    assert payload["serial_no"] == "SIM12345"


def test_single_value_endpoint_returns_plain_text(web):
    client, *_ = web
    response = client.get("/api/v1/value/battery_soc")
    assert response.get_data(as_text=True) == "77.5"
    assert client.get("/api/v1/value/nope").status_code == 404


def test_status_and_health(web):
    client, *_ = web
    status = client.get("/api/v1/status").get_json()
    assert status["modbus"]["state"] == "ok"
    assert status["loxone"]["transport"].startswith("UDP an")
    # The poller thread is not started in this fixture, so health must be red.
    assert client.get("/healthz").status_code == 503


def test_config_endpoint_redacts_and_restores_passwords(web):
    client, config, *_ = web
    config.update_section("loxone", {"password": "geheim"})

    shown = client.get("/api/v1/config").get_json()
    assert shown["loxone"]["password"] == "********"

    client.post("/api/v1/config", json={"loxone": {"password": "********", "prefix": "neu_"}})
    assert config.get("loxone", "password") == "geheim"  # placeholder did not overwrite it
    assert config.get("loxone", "prefix") == "neu_"


def test_config_endpoint_coerces_form_strings(web):
    client, config, *_ = web
    client.post(
        "/api/v1/config",
        json={"poll": {"now": "15"}, "loxone": {"send_only_on_change": "false"}},
    )
    assert config.get("poll", "now") == 15
    assert config.get("loxone", "send_only_on_change") is False


def test_config_endpoint_rejects_invalid_values(web):
    client, config, *_ = web
    response = client.post("/api/v1/config", json={"modbus": {"host": ""}})
    assert response.status_code == 400
    assert "Host/IP" in json.dumps(response.get_json()["errors"])
    assert config.get("modbus", "host") == "127.0.0.1"  # nothing was written


def test_config_endpoint_ignores_unknown_sections_and_keys(web):
    client, config, *_ = web
    assert client.post("/api/v1/config", json={"nope": {"a": 1}}).status_code == 400
    client.post("/api/v1/config", json={"poll": {"now": 11, "smuggled": "x"}})
    assert "smuggled" not in config.section("poll")


def test_mapping_endpoint_round_trip(web):
    client, config, _store, poller = web
    listing = client.get("/api/v1/mapping").get_json()
    assert len(listing["rows"]) == 85
    assert any(row["short"] == "pv" and row["value"] == 4321 for row in listing["rows"])

    result = client.post(
        "/api/v1/mapping",
        json={"values": {"pv": {"target": "PV Leistung", "enabled": True, "decimals": 1}}},
    ).get_json()
    assert result["ok"] is True
    assert config.section("values")["pv"]["target"] == "PV_Leistung"


def test_mapping_endpoint_reports_duplicates(web):
    client, *_ = web
    client.post("/api/v1/mapping", json={"values": {"pv": {"target": "shared"}}})
    result = client.post(
        "/api/v1/mapping", json={"values": {"consumption": {"target": "shared"}}}
    ).get_json()
    # The configured prefix is part of the Loxone name, so it is part of the clash.
    assert result["duplicates"] == {"mtec_shared": ["consumption", "pv"]}


def test_templates_are_downloadable_xml(web):
    client, *_ = web
    response = client.get("/api/v1/loxone-template/udp")
    assert response.mimetype == "application/xml"
    assert "attachment" in response.headers["Content-Disposition"]
    assert b"VirtualInUdpCmd" in response.data
    assert client.get("/api/v1/loxone-template/http").status_code == 200
    assert client.get("/api/v1/loxone-template/nonsense").status_code == 404


def test_test_action_sends_a_datagram(wired, config, register_map):
    poller, store, _fake, drain = wired
    ctx = AppContext(config=config, registers=register_map, store=store, poller=poller)
    client = create_app(ctx).test_client()

    result = client.post("/api/v1/actions/test", json={"target": "probe", "value": "7"}).get_json()
    assert result["ok"] is True
    assert drain(expected=1) == ["mtec_probe: 7"]


def test_basic_auth_protects_the_api_but_not_the_health_check(web):
    client, config, *_ = web
    config.update_section("web", {"auth_user": "loxone", "auth_password": "secret"})

    assert client.get("/api/v1/values").status_code == 401
    assert client.get("/healthz").status_code in (200, 503)  # never 401

    token = base64.b64encode(b"loxone:secret").decode()
    assert client.get("/api/v1/values", headers={"Authorization": f"Basic {token}"}).status_code == 200

    wrong = base64.b64encode(b"loxone:nope").decode()
    assert client.get("/api/v1/values", headers={"Authorization": f"Basic {wrong}"}).status_code == 401
