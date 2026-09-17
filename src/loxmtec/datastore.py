"""Thread-safe store for the current inverter values and the runtime status."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loxmtec.const import (
    STATE_DISABLED,
    STATE_ERROR,
    STATE_OK,
    STATE_UNKNOWN,
    STATE_WARN,
)


@dataclass
class ValueEntry:
    """One published value plus its metadata."""

    short: str
    name: str
    value: Any
    unit: str = ""
    group: str = ""
    register: str = ""
    updated: float = field(default_factory=time.time)
    sent: float | None = None
    sent_value: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "short": self.short,
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "group": self.group,
            "register": self.register,
            "updated": datetime.fromtimestamp(self.updated).isoformat(timespec="seconds"),
            "age": round(time.time() - self.updated, 1),
            "sent": (
                datetime.fromtimestamp(self.sent).isoformat(timespec="seconds")
                if self.sent
                else None
            ),
        }


class DataStore:
    """Holds the latest values and the health counters of every component."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._values: dict[str, ValueEntry] = {}
        self._group_reads: dict[str, float] = {}

        self.started = time.time()
        self.serial_no: str = ""

        # Modbus health
        self.modbus_state: str = STATE_UNKNOWN
        self.modbus_message: str = "noch nicht verbunden"
        self.modbus_last_ok: float | None = None
        self.modbus_reads_ok: int = 0
        self.modbus_reads_failed: int = 0
        self.modbus_reconnects: int = 0

        # Loxone health
        self.loxone_state: str = STATE_UNKNOWN
        self.loxone_message: str = "noch nichts gesendet"
        self.loxone_last_ok: float | None = None
        self.loxone_sent: int = 0
        self.loxone_failed: int = 0

        # MQTT health (optional secondary output)
        self.mqtt_state: str = STATE_DISABLED
        self.mqtt_message: str = "deaktiviert"

        # Watchdog
        self.watchdog_state: str = STATE_UNKNOWN
        self.watchdog_message: str = "startet"
        self.watchdog_restarts: int = 0

    # ------------------------------------------------------------------
    # values
    # ------------------------------------------------------------------
    def update_value(
        self,
        short: str,
        name: str,
        value: Any,
        unit: str = "",
        group: str = "",
        register: str = "",
    ) -> ValueEntry:
        with self._lock:
            entry = self._values.get(short)
            if entry is None:
                entry = ValueEntry(
                    short=short, name=name, value=value, unit=unit, group=group, register=register
                )
                self._values[short] = entry
            else:
                entry.name = name
                entry.value = value
                entry.unit = unit
                entry.group = group
                entry.register = register
                entry.updated = time.time()
            return entry

    def mark_sent(self, short: str, value: Any) -> None:
        with self._lock:
            entry = self._values.get(short)
            if entry is not None:
                entry.sent = time.time()
                entry.sent_value = value

    def get(self, short: str) -> ValueEntry | None:
        with self._lock:
            return self._values.get(short)

    def values(self) -> dict[str, ValueEntry]:
        with self._lock:
            return dict(self._values)

    def flat(self) -> dict[str, Any]:
        """``{short: value}`` - the payload Loxone polls in pull mode."""
        with self._lock:
            return {short: entry.value for short, entry in self._values.items()}

    def detailed(self) -> list[dict[str, Any]]:
        with self._lock:
            return [entry.as_dict() for entry in self._values.values()]

    def note_group_read(self, group: str) -> None:
        with self._lock:
            self._group_reads[group] = time.time()

    def group_reads(self) -> dict[str, str]:
        with self._lock:
            return {
                group: datetime.fromtimestamp(ts).isoformat(timespec="seconds")
                for group, ts in self._group_reads.items()
            }

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------
    def modbus_ok(self, message: str = "verbunden") -> None:
        with self._lock:
            self.modbus_state = STATE_OK
            self.modbus_message = message
            self.modbus_last_ok = time.time()
            self.modbus_reads_ok += 1

    def modbus_error(self, message: str, fatal: bool = False) -> None:
        with self._lock:
            self.modbus_state = STATE_ERROR if fatal else STATE_WARN
            self.modbus_message = message
            self.modbus_reads_failed += 1

    def note_reconnect(self) -> None:
        with self._lock:
            self.modbus_reconnects += 1

    def loxone_ok(self, count: int, message: str = "") -> None:
        with self._lock:
            self.loxone_state = STATE_OK
            self.loxone_last_ok = time.time()
            self.loxone_sent += count
            self.loxone_message = message or f"{count} Werte gesendet"

    def loxone_error(self, message: str, count: int = 1) -> None:
        with self._lock:
            self.loxone_state = STATE_ERROR
            self.loxone_failed += count
            self.loxone_message = message

    def loxone_idle(self, message: str = "keine Änderungen zu senden") -> None:
        """Nothing to send is not a failure - but it is no proof of life either,
        so the 'last ok' timestamp stays where it was."""
        with self._lock:
            self.loxone_message = message

    def loxone_disabled(self, message: str = "Push deaktiviert (nur Pull/REST)") -> None:
        with self._lock:
            self.loxone_state = STATE_DISABLED
            self.loxone_message = message

    def set_watchdog(self, state: str, message: str) -> None:
        with self._lock:
            self.watchdog_state = state
            self.watchdog_message = message

    def set_mqtt(self, state: str, message: str) -> None:
        with self._lock:
            self.mqtt_state = state
            self.mqtt_message = message

    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """Snapshot for the GUI, the REST API and the health check."""
        with self._lock:
            now = time.time()
            return {
                "uptime": int(now - self.started),
                "serial_no": self.serial_no,
                "values": len(self._values),
                "modbus": {
                    "state": self.modbus_state,
                    "message": self.modbus_message,
                    "last_ok": _iso(self.modbus_last_ok),
                    "age": round(now - self.modbus_last_ok, 1) if self.modbus_last_ok else None,
                    "reads_ok": self.modbus_reads_ok,
                    "reads_failed": self.modbus_reads_failed,
                    "reconnects": self.modbus_reconnects,
                },
                "loxone": {
                    "state": self.loxone_state,
                    "message": self.loxone_message,
                    "last_ok": _iso(self.loxone_last_ok),
                    "age": round(now - self.loxone_last_ok, 1) if self.loxone_last_ok else None,
                    "sent": self.loxone_sent,
                    "failed": self.loxone_failed,
                },
                "mqtt": {"state": self.mqtt_state, "message": self.mqtt_message},
                "watchdog": {
                    "state": self.watchdog_state,
                    "message": self.watchdog_message,
                    "restarts": self.watchdog_restarts,
                },
                "groups": self.group_reads(),
            }

    def healthy(self, stale_after: int) -> bool:
        """True if a recent Modbus read succeeded - used by the Docker healthcheck."""
        with self._lock:
            if self.modbus_last_ok is None:
                # Grant a grace period after startup so a slow inverter doesn't
                # make the container flap.
                return time.time() - self.started < max(stale_after, 60)
            return time.time() - self.modbus_last_ok <= stale_after


def _iso(timestamp: float | None) -> str | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")
