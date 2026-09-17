"""Optional MQTT output.

LOX-MTEC talks to Loxone directly, so MQTT is off by default.  It is kept as an
optional secondary output for anyone who also runs Home Assistant, evcc or an
ioBroker instance next to the Miniserver.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from loxmtec.const import STATE_DISABLED, STATE_ERROR, STATE_OK
from loxmtec.datastore import ValueEntry

logger = logging.getLogger(__name__)

try:  # paho is an optional dependency
    import paho.mqtt.client as mqtt_client

    MQTT_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without paho installed
    mqtt_client = None
    MQTT_AVAILABLE = False


@dataclass
class MqttSettings:
    enabled: bool = False
    host: str = "localhost"
    port: int = 1883
    user: str = ""
    password: str = ""
    topic: str = "MTEC"

    @classmethod
    def from_config(cls, section: dict[str, Any]) -> "MqttSettings":
        known = {f: section[f] for f in cls.__dataclass_fields__ if f in section}
        return cls(**known)


class MqttPublisher:
    """Thin wrapper around paho-mqtt with automatic reconnect."""

    def __init__(self, settings: MqttSettings) -> None:
        self._settings = settings
        self._client = None
        self._lock = threading.RLock()
        self.state = STATE_DISABLED
        self.message = "deaktiviert"

    @property
    def settings(self) -> MqttSettings:
        return self._settings

    def apply_settings(self, settings: MqttSettings) -> None:
        with self._lock:
            if settings == self._settings:
                return
            logger.info("MQTT settings changed - reconnecting")
            self._settings = settings
            self.close()

    # ------------------------------------------------------------------
    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not MQTT_AVAILABLE:
            self.state = STATE_ERROR
            self.message = "paho-mqtt ist nicht installiert"
            return None

        settings = self._settings
        try:
            client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
            if settings.user:
                client.username_pw_set(settings.user, settings.password)
            client.connect(settings.host, int(settings.port), keepalive=60)
            client.loop_start()
            self._client = client
            self.state = STATE_OK
            self.message = f"verbunden mit {settings.host}:{settings.port}"
            logger.info("Connected to MQTT broker %s:%s", settings.host, settings.port)
        except Exception as err:
            self.state = STATE_ERROR
            self.message = f"Verbindung fehlgeschlagen: {err}"
            logger.error("Couldn't connect to MQTT broker: %s", err)
            return None
        return self._client

    def publish(self, entries: dict[str, ValueEntry], serial_no: str = "") -> int:
        """Publish every value as ``<topic>/<serial>/<group>/<short>``."""
        with self._lock:
            if not self._settings.enabled:
                self.state = STATE_DISABLED
                self.message = "deaktiviert"
                return 0
            client = self._ensure_client()
            if client is None:
                return 0

            base = self._settings.topic.strip("/")
            if serial_no:
                base = f"{base}/{serial_no}"
            published = 0
            for short, entry in entries.items():
                topic = f"{base}/{entry.group or 'misc'}/{short}"
                try:
                    client.publish(topic, payload=str(entry.value))
                    published += 1
                except Exception as err:
                    self.state = STATE_ERROR
                    self.message = f"Publish fehlgeschlagen: {err}"
                    logger.error("Couldn't publish to MQTT: %s", err)
                    return published
            self.state = STATE_OK
            self.message = f"{published} Werte veröffentlicht"
            return published

    def close(self) -> None:
        with self._lock:
            if self._client is None:
                return
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as err:  # pragma: no cover - best effort
                logger.debug("Exception while stopping MQTT client: %s", err)
            self._client = None
            self.state = STATE_DISABLED
            self.message = "getrennt"
