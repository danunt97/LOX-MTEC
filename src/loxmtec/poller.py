"""Background worker: read Modbus, calculate, store, push to Loxone."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from loxmtec import calc
from loxmtec.config import Config
from loxmtec.const import (
    GROUP_CONFIG,
    GROUP_DAY,
    GROUP_NOW_BASE,
    GROUP_TOTAL,
    NOW_EXTENDED_GROUPS,
)
from loxmtec.datastore import DataStore, ValueEntry
from loxmtec.loxone import LoxoneClient, LoxoneSettings
from loxmtec.mapping import load_mappings
from loxmtec.modbus import ModbusError, ModbusSettings, MTECModbus, Value
from loxmtec.mqttout import MqttPublisher, MqttSettings
from loxmtec.registers import RegisterMap

logger = logging.getLogger(__name__)

# Groups that are polled on their own timer -> the matching config/poll key.
SCHEDULED_GROUPS = {GROUP_DAY: "day", GROUP_TOTAL: "total", GROUP_CONFIG: "config"}


class Poller(threading.Thread):
    """Owns the Modbus connection and the outbound data flow."""

    daemon = True

    def __init__(self, config: Config, register_map: RegisterMap, store: DataStore) -> None:
        super().__init__(name="poller")
        self.config = config
        self.registers = register_map
        self.store = store

        self.modbus = MTECModbus(ModbusSettings.from_config(config.section("modbus")), register_map)
        self.loxone = LoxoneClient(LoxoneSettings.from_config(config.section("loxone")))
        self.mqtt = MqttPublisher(MqttSettings.from_config(config.section("mqtt")))
        self.mappings = load_mappings(config.section("values"), register_map)

        self._stopping = threading.Event()
        self._wake = threading.Event()
        self._reload = threading.Event()
        self._force_send = threading.Event()
        self._now_index = 0
        self._next: dict[str, float] = {group: 0.0 for group in SCHEDULED_GROUPS}
        self.last_cycle: float | None = None

    # ------------------------------------------------------------------
    # control
    # ------------------------------------------------------------------
    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()

    def trigger(self) -> None:
        """Run the next cycle immediately."""
        self._wake.set()

    def request_reload(self) -> None:
        """Pick up changed configuration on the next cycle."""
        self._reload.set()
        self._wake.set()

    def request_resend(self) -> None:
        """Send every enabled value on the next cycle, regardless of changes."""
        self._force_send.set()
        self._wake.set()

    def reconnect(self) -> bool:
        """Force a Modbus reconnect (used by the GUI and the watchdog)."""
        self.store.note_reconnect()
        ok = self.modbus.reconnect(force=True)
        if ok:
            self.store.modbus_ok("neu verbunden")
        else:
            self.store.modbus_error(self.modbus.last_error or "Reconnect fehlgeschlagen", fatal=True)
        return ok

    # ------------------------------------------------------------------
    def _apply_config(self) -> None:
        self.mappings = load_mappings(self.config.section("values"), self.registers)
        self.loxone.apply_settings(LoxoneSettings.from_config(self.config.section("loxone")))
        self.mqtt.apply_settings(MqttSettings.from_config(self.config.section("mqtt")))
        if self.modbus.apply_settings(ModbusSettings.from_config(self.config.section("modbus"))):
            if not self.modbus.connect():
                self.store.modbus_error(
                    self.modbus.last_error or "Verbindung fehlgeschlagen", fatal=True
                )
        if not self.loxone.enabled:
            self.store.loxone_disabled()
        # Force a full resend so Loxone immediately reflects the new mapping.
        self.loxone.forget()

    # ------------------------------------------------------------------
    def run(self) -> None:  # pragma: no cover - thread entry point
        logger.info("Poller started")
        if not self.modbus.connect():
            self.store.modbus_error(self.modbus.last_error or "Verbindung fehlgeschlagen", fatal=True)

        while not self._stopping.is_set():
            started = time.time()
            if self._reload.is_set():
                self._reload.clear()
                self._apply_config()
            try:
                self.cycle(force_send=self._force_send.is_set())
            except Exception as err:  # never let the thread die
                logger.exception("Unhandled error in poll cycle: %s", err)
                self.store.modbus_error(f"Interner Fehler: {err}", fatal=True)
            finally:
                self._force_send.clear()
                self.last_cycle = time.time()

            interval = max(2, int(self.config.get("poll", "now", 10)))
            elapsed = time.time() - started
            self._wake.wait(max(0.5, interval - elapsed))
            self._wake.clear()

        self._shutdown()

    def _shutdown(self) -> None:
        logger.info("Poller stopping")
        self.modbus.disconnect()
        self.loxone.close()
        self.mqtt.close()

    # ------------------------------------------------------------------
    # one poll cycle
    # ------------------------------------------------------------------
    def cycle(self, force_send: bool = False) -> None:
        if not self.modbus.connected and not self.modbus.reconnect():
            self.store.modbus_error(
                self.modbus.last_error or "Keine Modbus-Verbindung", fatal=True
            )
            return

        now = time.time()
        groups = [GROUP_NOW_BASE, NOW_EXTENDED_GROUPS[self._now_index % len(NOW_EXTENDED_GROUPS)]]
        self._now_index += 1

        poll_cfg = self.config.section("poll")
        groups.extend(group for group, due in self._next.items() if due <= now)

        failures = 0
        for group in groups:
            if not self._read_group(group):
                failures += 1
            elif group in self._next:
                interval = max(10, int(poll_cfg.get(SCHEDULED_GROUPS[group], 300)))
                self._next[group] = time.time() + interval

        if failures == len(groups):
            # Every group failed - the connection is most likely gone.
            self.modbus.reconnect()
            return

        self._publish(force=force_send)

    def _read_group(self, group: str) -> bool:
        try:
            raw = self.modbus.read_group(group)
        except ModbusError as err:
            logger.warning("Reading group '%s' failed: %s", group, err)
            self.store.modbus_error(str(err))
            return False

        self._store_group(group, raw)
        self.store.note_group_read(group)
        self.store.modbus_ok(f"Gruppe '{group}' gelesen")
        return True

    def _store_group(self, group: str, raw: dict[str, Value]) -> None:
        """Map raw register values plus pseudo registers into the data store."""
        for key in self.registers.group_keys(group):
            register = self.registers.get(key)
            if register is None or not register.short:
                continue

            if register.is_modbus:
                value = raw.get(key)
                if value is None:
                    continue
                result: Any = value.value
            else:
                result = calc.calculate(key, raw)
                if result is None:
                    continue

            self.store.update_value(
                short=register.short,
                name=register.name,
                value=result,
                unit=register.unit,
                group=group,
                register=key,
            )
            if register.short == "serial_no" and isinstance(result, str) and result:
                self.store.serial_no = result

    # ------------------------------------------------------------------
    def _publish(self, force: bool = False) -> None:
        entries: dict[str, ValueEntry] = self.store.values()
        if not entries:
            return

        if self.loxone.enabled:
            result, _payloads = self.loxone.publish(entries, self.mappings, force=force)
            for payload in result.sent:
                self.store.mark_sent(payload.short, payload.text)
            if result.failed:
                self.store.loxone_error(result.error or "Senden fehlgeschlagen", result.failed)
                logger.warning("Loxone: %d Werte fehlgeschlagen (%s)", result.failed, result.error)
            elif result.ok:
                self.store.loxone_ok(result.ok, f"{result.ok} Werte via {self.loxone.describe()}")
            else:
                self.store.loxone_idle()
        else:
            self.store.loxone_disabled()

        if self.mqtt.settings.enabled:
            self.mqtt.publish(entries, self.store.serial_no)
        self.store.set_mqtt(self.mqtt.state, self.mqtt.message)
