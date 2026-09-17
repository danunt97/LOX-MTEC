"""Modbus TCP client for the M-TEC Energybutler.

This is a refactored version of ``mtecmqtt.MTECmodbusAPI``: the settings are
injected instead of read from a module level global, so the web GUI can change
host, port or slave id at runtime without restarting the process.  It also
copes with the ``slave`` -> ``device_id`` keyword rename in pymodbus >= 3.9.
"""

from __future__ import annotations

import inspect
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pymodbus.client import ModbusTcpClient

from loxmtec.registers import Register, RegisterMap

logger = logging.getLogger(__name__)

RECONNECT_THROTTLE = timedelta(seconds=30)


def _device_id_kwarg() -> str:
    """pymodbus renamed the ``slave`` keyword to ``device_id`` in 3.9."""
    try:
        params = inspect.signature(ModbusTcpClient.read_holding_registers).parameters
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return "device_id"
    return "device_id" if "device_id" in params else "slave"


DEVICE_KWARG = _device_id_kwarg()


@dataclass
class ModbusSettings:
    host: str = "espressif"
    port: int = 5743
    port_fallback: int = 502
    slave: int = 252
    timeout: int = 5
    retries: int = 3
    framer: str = "rtu"

    @classmethod
    def from_config(cls, section: dict[str, Any]) -> "ModbusSettings":
        known = {f: section[f] for f in cls.__dataclass_fields__ if f in section}
        return cls(**known)


@dataclass
class Value:
    """One decoded register value."""

    name: str
    value: Any
    unit: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "unit": self.unit}


class ModbusError(Exception):
    """Raised when a read fails in a way the caller should react to."""


class MTECModbus:
    """Reads clustered register blocks from the inverter."""

    def __init__(self, settings: ModbusSettings, register_map: RegisterMap) -> None:
        self._settings = settings
        self._registers = register_map
        self._client: ModbusTcpClient | None = None
        self._cluster_cache: dict[str, list[dict[str, Any]]] = {}
        self._last_reconnect: datetime | None = None
        self._lock = threading.RLock()
        self.last_error: str | None = None

    # ------------------------------------------------------------------
    @property
    def settings(self) -> ModbusSettings:
        return self._settings

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._client is not None

    def apply_settings(self, settings: ModbusSettings) -> bool:
        """Swap the connection settings. Returns True if a reconnect is needed."""
        with self._lock:
            if settings == self._settings:
                return False
            logger.info("Modbus settings changed - reconnecting")
            self._settings = settings
            self.disconnect()
            self._last_reconnect = None
            return True

    # ------------------------------------------------------------------
    def connect(self) -> bool:
        """Connect, trying the configured port first and the fallback second."""
        with self._lock:
            settings = self._settings
            for port in (settings.port, settings.port_fallback):
                if not port:
                    continue
                if self._connect(port):
                    return True
            self.last_error = f"Keine Verbindung zum Modbus-Server {settings.host}"
            logger.error(
                "Can't connect to Modbus server %s (ports %s, %s)",
                settings.host,
                settings.port,
                settings.port_fallback,
            )
            return False

    def _connect(self, port: int) -> bool:
        settings = self._settings
        logger.debug("Connecting to %s:%s (framer=%s)", settings.host, port, settings.framer)
        try:
            client = ModbusTcpClient(
                settings.host,
                port=int(port),
                framer=settings.framer,
                timeout=settings.timeout,
                retries=settings.retries,
            )
            if client.connect():
                self._client = client
                self.last_error = None
                logger.info("Connected to Modbus server %s:%s", settings.host, port)
                return True
            client.close()
        except Exception as err:  # pymodbus raises a wide range of exceptions
            logger.error("Error while connecting to %s:%s: %s", settings.host, port, err)
            self.last_error = str(err)
            return False
        logger.warning("Couldn't connect to Modbus server %s:%s", settings.host, port)
        return False

    def disconnect(self) -> None:
        with self._lock:
            if not self._client:
                return
            try:
                self._client.close()
            except Exception as err:  # pragma: no cover - best effort cleanup
                logger.debug("Exception while disconnecting: %s", err)
            self._client = None
            logger.info("Disconnected from Modbus server")

    def reconnect(self, force: bool = False) -> bool:
        """Throttled reconnect - at most one attempt per 30 s unless forced."""
        with self._lock:
            now = datetime.now()
            if not force and self._last_reconnect and now < self._last_reconnect + RECONNECT_THROTTLE:
                return False
            self._last_reconnect = now
            self.disconnect()
            if self.connect():
                logger.info("Successfully re-connected to Modbus server")
                return True
            logger.error("Couldn't re-connect to Modbus server")
            return False

    # ------------------------------------------------------------------
    def read_group(self, group: str) -> dict[str, Value]:
        """Read every Modbus register of one group. Raises :class:`ModbusError`."""
        keys = [key for key in self._registers.group_keys(group) if key.isnumeric()]
        if not keys:
            raise ModbusError(f"Unknown or empty register group: {group}")
        return self.read_registers(keys)

    def read_registers(self, keys: list[str]) -> dict[str, Value]:
        """Read the given register keys, clustered into as few requests as possible."""
        with self._lock:
            if not self._client:
                raise ModbusError("Not connected to the Modbus server")

            data: dict[str, Value] = {}
            for cluster in self._clusters(keys):
                raw = self._read_block(cluster["start"], cluster["length"])
                offset = 0
                for item in cluster["items"]:
                    if item.type:  # type None marks a padding/dummy entry
                        decoded = self._decode(raw, offset, item)
                        if decoded is not None:
                            data[str(cluster["start"] + offset)] = decoded
                        else:
                            logger.error("Decoding error for register %s", item.key)
                    offset += item.length or 0
            return data

    def write_register(self, key: str, value: Any) -> bool:
        """Write a single writable register."""
        register = self._registers.get(key)
        if not register:
            logger.error("Can't write unknown register: %s", key)
            return False
        if not register.writable:
            logger.error("Can't write read-only register: %s", key)
            return False

        try:
            number = float(value) if "." in str(value) else int(value)
        except (TypeError, ValueError):
            logger.error("Invalid numeric value for register %s: %r", key, value)
            return False

        if register.scale > 1:
            number *= register.scale

        with self._lock:
            if not self._client:
                logger.error("Can't write register %s: not connected", key)
                return False
            try:
                result = self._client.write_register(
                    address=int(key), value=int(number), **{DEVICE_KWARG: self._settings.slave}
                )
            except Exception as err:
                logger.error("Exception while writing register %s: %s", key, err)
                return False
        if result.isError():
            logger.error("Modbus error while writing register %s", key)
            return False
        logger.info("Wrote register %s (%s) = %s", key, register.name, number)
        return True

    # ------------------------------------------------------------------
    def _read_block(self, start: int, length: int):
        try:
            result = self._client.read_holding_registers(
                address=int(start), count=length, **{DEVICE_KWARG: self._settings.slave}
            )
        except Exception as err:
            self.last_error = str(err)
            raise ModbusError(f"Exception while reading register {start}: {err}") from err
        if result.isError():
            self.last_error = f"Modbus-Fehler bei Register {start}"
            raise ModbusError(f"Modbus error while reading register {start}, length {length}")
        if len(result.registers) != length:
            self.last_error = f"Unvollständige Antwort bei Register {start}"
            raise ModbusError(
                f"Short read at register {start}: expected {length}, got {len(result.registers)}"
            )
        return result

    def _clusters(self, keys: list[str]) -> list[dict[str, Any]]:
        cache_key = ",".join(sorted(keys))
        if cache_key not in self._cluster_cache:
            self._cluster_cache[cache_key] = self._build_clusters(keys)
        return self._cluster_cache[cache_key]

    def _build_clusters(self, keys: list[str]) -> list[dict[str, Any]]:
        """Group consecutive registers so they can be fetched in one request."""
        clusters: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None

        for key in sorted(keys, key=lambda k: int(k) if k.isnumeric() else -1):
            if not key.isnumeric():
                continue
            register = self._registers.get(key)
            if not register:
                logger.warning("Unknown register %s - skipped", key)
                continue
            address = int(key)
            if current is None or address > current["start"] + current["length"]:
                if current is not None:
                    clusters.append(current)
                current = {"start": address, "length": 0, "items": []}
            current["length"] += register.length or 0
            current["items"].append(register)

        if current is not None:
            clusters.append(current)
        return clusters

    def _decode(self, raw, offset: int, item: Register) -> Value | None:
        convert = self._client.convert_from_registers
        datatype = self._client.DATATYPE
        registers = raw.registers
        try:
            value: Any
            if item.type == "U16":
                value = convert(registers[offset : offset + 1], datatype.UINT16)
            elif item.type == "I16":
                value = convert(registers[offset : offset + 1], datatype.INT16)
            elif item.type == "U32":
                value = convert(registers[offset : offset + 2], datatype.UINT32)
            elif item.type == "I32":
                value = convert(registers[offset : offset + 2], datatype.INT32)
            elif item.type == "STR":
                length = (item.length or 1) * 2 + 1
                value = convert(registers[offset : offset + length], datatype.STRING)
                value = str(value).split("\x00")[0].strip()
            elif item.type == "BYTE":
                words = registers[offset : offset + (item.length or 1)]
                value = "  ".join(f"{w >> 8:02d} {w & 0xFF:02d}" for w in words)
            elif item.type == "BIT":
                words = registers[offset : offset + (item.length or 1)]
                value = " ".join(f"{w:08b}" for w in words)
            elif item.type == "DAT":
                w1, w2, w3 = registers[offset], registers[offset + 1], registers[offset + 2]
                value = (
                    f"{w1 >> 8:02d}-{w1 & 0xFF:02d}-{w2 >> 8:02d} "
                    f"{w2 & 0xFF:02d}:{w3 >> 8:02d}:{w3 & 0xFF:02d}"
                )
            else:
                logger.error("Unknown register type '%s' for %s", item.type, item.key)
                return None

            if isinstance(value, (int, float)) and item.scale > 1:
                value /= item.scale
            return Value(name=item.name, value=value, unit=item.unit)
        except Exception as err:
            logger.error("Exception while decoding register %s: %s", item.key, err)
            return None
