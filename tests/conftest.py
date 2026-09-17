"""Shared fixtures."""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from loxmtec.config import Config  # noqa: E402
from loxmtec.datastore import DataStore  # noqa: E402
from loxmtec.mapping import ensure_defaults  # noqa: E402
from loxmtec.registers import load_register_map  # noqa: E402

from tests.fake_inverter import FakeInverter  # noqa: E402


@pytest.fixture(scope="session")
def register_map():
    return load_register_map()


@pytest.fixture
def config(tmp_path, register_map):
    cfg = Config(tmp_path / "config.yaml").load()
    cfg.replace_values(ensure_defaults({}, register_map))
    return cfg


@pytest.fixture
def store():
    return DataStore()


@pytest.fixture
def inverter():
    """A fake inverter pre-filled with plausible values."""
    fake = FakeInverter()
    fake.set_u32(11028, 4321)  # PV power             -> 4321 W
    fake.set_u32(11016, 3000)  # load power           -> 3000 W
    fake.set_i32(11000, 1200)  # grid power           -> 1200 W
    fake.set_u16(33000, 7750)  # battery SOC, scale 100 -> 77.5 %
    fake.set_u16(30254, 2300)  # AC voltage A, scale 10 -> 230.0 V
    fake.set_string(10000, "SIM12345", 8)  # serial number
    fake.set_u16(31005, 200)  # PV day, scale 10       -> 20.0 kWh
    fake.set_u16(31001, 50)  # grid purchase day       -> 5.0 kWh
    fake.set_u16(31004, 10)
    fake.set_u16(31000, 40)
    fake.set_u16(31003, 20)
    port = fake.start()
    yield fake, port
    fake.stop()


@pytest.fixture
def udp_listener():
    """A UDP socket that collects everything the Loxone transport sends."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.5)
    port = sock.getsockname()[1]

    def drain(expected: int = 0, timeout: float = 3.0) -> list[str]:
        import time

        received: list[str] = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                received.append(sock.recvfrom(512)[0].decode())
            except socket.timeout:
                if expected and len(received) < expected:
                    continue
                break
        return received

    yield port, drain
    sock.close()
