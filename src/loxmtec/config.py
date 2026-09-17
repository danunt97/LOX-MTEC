"""Configuration handling for LOX-MTEC.

The configuration lives in a single YAML file (default ``/config/config.yaml``
inside the container).  It can be edited through the web GUI, which is why all
access goes through the thread-safe :class:`Config` wrapper instead of a module
level global.  Environment variables are applied on top of the file so that a
container can be bootstrapped without ever touching the GUI.
"""

from __future__ import annotations

import copy
import logging
import os
import threading
from pathlib import Path
from typing import Any

import yaml

from loxmtec.const import LOXONE_MODES, MODE_UDP

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(os.environ.get("LOXMTEC_CONFIG", "/config/config.yaml"))

DEFAULTS: dict[str, Any] = {
    "modbus": {
        "host": "espressif",
        "port": 5743,  # firmware < V27.52.4.0
        "port_fallback": 502,  # firmware > V27.52.4.0
        "slave": 252,
        "timeout": 5,
        "retries": 3,
        "framer": "rtu",
    },
    "poll": {
        "now": 10,
        "day": 300,
        "total": 310,
        "config": 3605,
    },
    "loxone": {
        "mode": MODE_UDP,
        "host": "",
        "udp_port": 7000,
        "http_scheme": "http",
        "http_port": 80,
        "user": "admin",
        "password": "",
        "prefix": "",
        "send_only_on_change": True,
        "heartbeat": 300,  # resend unchanged values after N seconds (0 = never)
        "timeout": 5,
        "float_format": "{:.3f}",
    },
    "web": {
        "host": "0.0.0.0",
        "port": 8080,
        "auth_user": "",
        "auth_password": "",
    },
    "mqtt": {
        "enabled": False,
        "host": "localhost",
        "port": 1883,
        "user": "",
        "password": "",
        "topic": "MTEC",
    },
    "watchdog": {
        "enabled": True,
        "stale_after": 120,  # no successful Modbus read for N s -> reconnect
        "exit_after": 900,  # still broken after N s -> exit(1), let Docker restart
    },
    "logging": {
        "level": "INFO",
        "buffer_size": 500,
    },
    # Per-value settings, keyed by the register's stable short name.
    # Filled with defaults on first start by mapping.ensure_defaults().
    "values": {},
}

# Flat environment overrides: ENV name -> (section, key, caster)
ENV_OVERRIDES: dict[str, tuple[str, str, Any]] = {
    "LOXMTEC_MODBUS_HOST": ("modbus", "host", str),
    "LOXMTEC_MODBUS_PORT": ("modbus", "port", int),
    "LOXMTEC_MODBUS_PORT_FALLBACK": ("modbus", "port_fallback", int),
    "LOXMTEC_MODBUS_SLAVE": ("modbus", "slave", int),
    "LOXMTEC_MODBUS_FRAMER": ("modbus", "framer", str),
    "LOXMTEC_POLL_NOW": ("poll", "now", int),
    "LOXMTEC_LOXONE_MODE": ("loxone", "mode", str),
    "LOXMTEC_LOXONE_HOST": ("loxone", "host", str),
    "LOXMTEC_LOXONE_UDP_PORT": ("loxone", "udp_port", int),
    "LOXMTEC_LOXONE_HTTP_PORT": ("loxone", "http_port", int),
    "LOXMTEC_LOXONE_USER": ("loxone", "user", str),
    "LOXMTEC_LOXONE_PASSWORD": ("loxone", "password", str),
    "LOXMTEC_LOXONE_PREFIX": ("loxone", "prefix", str),
    "LOXMTEC_WEB_HOST": ("web", "host", str),
    "LOXMTEC_WEB_PORT": ("web", "port", int),
    "LOXMTEC_WEB_USER": ("web", "auth_user", str),
    "LOXMTEC_WEB_PASSWORD": ("web", "auth_password", str),
    "LOXMTEC_MQTT_ENABLED": ("mqtt", "enabled", bool),
    "LOXMTEC_MQTT_HOST": ("mqtt", "host", str),
    "LOXMTEC_MQTT_PORT": ("mqtt", "port", int),
    "LOXMTEC_MQTT_USER": ("mqtt", "user", str),
    "LOXMTEC_MQTT_PASSWORD": ("mqtt", "password", str),
    "LOXMTEC_MQTT_TOPIC": ("mqtt", "topic", str),
    "LOXMTEC_LOG_LEVEL": ("logging", "level", str),
}

_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n"}


def _cast(raw: str, caster: Any) -> Any:
    if caster is bool:
        low = raw.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError(f"'{raw}' is not a boolean")
    return caster(raw)


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into a copy of ``base``."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class Config:
    """Thread-safe, file-backed configuration."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_CONFIG_PATH
        self._lock = threading.RLock()
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)

    # ------------------------------------------------------------------
    def load(self) -> "Config":
        """Load the YAML file (if present) and apply environment overrides."""
        file_data: dict[str, Any] = {}
        if self.path.is_file():
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    file_data = yaml.safe_load(handle) or {}
                logger.info("Loaded configuration from %s", self.path)
            except (OSError, yaml.YAMLError) as err:
                logger.error("Couldn't read config %s: %s", self.path, err)
                file_data = {}
        else:
            logger.info("No config at %s - starting from defaults", self.path)

        if not isinstance(file_data, dict):
            logger.error("Config %s is not a YAML mapping - ignoring it", self.path)
            file_data = {}

        with self._lock:
            self._data = deep_merge(DEFAULTS, file_data)
            self._apply_env()
        return self

    def _apply_env(self) -> None:
        for env_name, (section, key, caster) in ENV_OVERRIDES.items():
            raw = os.environ.get(env_name)
            if raw is None:
                continue
            try:
                self._data.setdefault(section, {})[key] = _cast(raw, caster)
                logger.info("Config override from env: %s.%s", section, key)
            except (TypeError, ValueError) as err:
                logger.error("Ignoring invalid env value %s=%r: %s", env_name, raw, err)

    # ------------------------------------------------------------------
    def save(self) -> bool:
        """Write the current configuration atomically."""
        with self._lock:
            data = copy.deepcopy(self._data)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)
            tmp.replace(self.path)
            logger.info("Configuration saved to %s", self.path)
            return True
        except OSError as err:
            logger.error("Couldn't write config %s: %s", self.path, err)
            tmp.unlink(missing_ok=True)
            return False

    # ------------------------------------------------------------------
    def section(self, name: str) -> dict[str, Any]:
        """Return a snapshot copy of one section."""
        with self._lock:
            return copy.deepcopy(self._data.get(name, {}))

    def get(self, section: str, key: str, default: Any = None) -> Any:
        with self._lock:
            return copy.deepcopy(self._data.get(section, {}).get(key, default))

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def update_section(self, name: str, values: dict[str, Any]) -> None:
        """Merge ``values`` into one section (used by the GUI)."""
        with self._lock:
            current = self._data.setdefault(name, {})
            current.update(copy.deepcopy(values))

    def replace_values(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._data["values"] = copy.deepcopy(values)


def validate(data: dict[str, Any]) -> list[str]:
    """Return a list of human readable problems - empty means the config is sane."""
    problems: list[str] = []

    modbus = data.get("modbus", {})
    if not str(modbus.get("host", "")).strip():
        problems.append("Modbus: Host/IP darf nicht leer sein")
    for key in ("port", "port_fallback", "slave"):
        value = modbus.get(key)
        if not isinstance(value, int) or not 0 < value < 65536:
            problems.append(f"Modbus: '{key}' muss eine Zahl zwischen 1 und 65535 sein")

    poll = data.get("poll", {})
    for key, minimum in (("now", 2), ("day", 10), ("total", 10), ("config", 10)):
        value = poll.get(key)
        if not isinstance(value, int) or value < minimum:
            problems.append(f"Polling: '{key}' muss >= {minimum} Sekunden sein")

    loxone = data.get("loxone", {})
    mode = loxone.get("mode")
    if mode not in LOXONE_MODES:
        problems.append(f"Loxone: Modus muss einer von {', '.join(LOXONE_MODES)} sein")
    elif mode != "off" and not str(loxone.get("host", "")).strip():
        problems.append("Loxone: Miniserver IP/Host wird für Push-Betrieb benötigt")
    for key in ("udp_port", "http_port"):
        value = loxone.get(key)
        if not isinstance(value, int) or not 0 < value < 65536:
            problems.append(f"Loxone: '{key}' muss eine Zahl zwischen 1 und 65535 sein")
    try:
        str(loxone.get("float_format", "{:.3f}")).format(1.0)
    except (IndexError, KeyError, ValueError):
        problems.append("Loxone: 'float_format' ist kein gültiges Python-Format")

    web = data.get("web", {})
    port = web.get("port")
    if not isinstance(port, int) or not 0 < port < 65536:
        problems.append("Web: Port muss eine Zahl zwischen 1 und 65535 sein")

    watchdog = data.get("watchdog", {})
    stale = watchdog.get("stale_after")
    exit_after = watchdog.get("exit_after")
    if not isinstance(stale, int) or stale < 10:
        problems.append("Watchdog: 'stale_after' muss >= 10 Sekunden sein")
    if not isinstance(exit_after, int) or (exit_after != 0 and exit_after < 60):
        problems.append("Watchdog: 'exit_after' muss 0 (aus) oder >= 60 Sekunden sein")
    if isinstance(stale, int) and isinstance(exit_after, int) and 0 < exit_after <= stale:
        problems.append("Watchdog: 'exit_after' muss größer als 'stale_after' sein")

    return problems
