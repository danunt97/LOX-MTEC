"""Facade that formats values, decides what to send and drives the transport."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from loxmtec.const import MODE_HTTP, MODE_OFF, MODE_UDP
from loxmtec.datastore import ValueEntry
from loxmtec.loxone.base import Payload, SendResult, Transport
from loxmtec.loxone.http import HttpTransport
from loxmtec.loxone.udp import UdpTransport
from loxmtec.mapping import ValueMapping

logger = logging.getLogger(__name__)


@dataclass
class LoxoneSettings:
    mode: str = MODE_UDP
    host: str = ""
    udp_port: int = 7000
    http_scheme: str = "http"
    http_port: int = 80
    user: str = "admin"
    password: str = ""
    prefix: str = ""
    send_only_on_change: bool = True
    heartbeat: int = 300
    timeout: int = 5
    float_format: str = "{:.3f}"

    @classmethod
    def from_config(cls, section: dict[str, Any]) -> "LoxoneSettings":
        known = {f: section[f] for f in cls.__dataclass_fields__ if f in section}
        return cls(**known)


def format_value(raw: Any, mapping: ValueMapping, fallback_format: str = "{:.3f}") -> str:
    """Render a value the way Loxone should receive it, factor applied."""
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "1" if raw else "0"
    if isinstance(raw, (int, float)):
        value = mapping.scale(raw)
        try:
            return f"{float(value):.{int(mapping.decimals)}f}"
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return fallback_format.format(float(value))
    # Strings (serial number, dates, bit patterns) - keep them on one line so
    # that the UDP "<name>: <value>" framing stays intact.
    return " ".join(str(raw).split())


class LoxoneClient:
    """Owns the active transport and the change-detection state."""

    def __init__(self, settings: LoxoneSettings) -> None:
        self._settings = settings
        self._transport: Transport | None = None
        self._last_sent: dict[str, tuple[str, Any, float]] = {}
        self._lock = threading.RLock()
        self._build_transport()

    # ------------------------------------------------------------------
    @property
    def settings(self) -> LoxoneSettings:
        return self._settings

    @property
    def enabled(self) -> bool:
        return self._settings.mode != MODE_OFF

    def describe(self) -> str:
        with self._lock:
            if self._transport is None:
                return "Push deaktiviert (Loxone holt die Werte per REST ab)"
            return self._transport.describe()

    def apply_settings(self, settings: LoxoneSettings) -> None:
        with self._lock:
            if settings == self._settings:
                return
            logger.info("Loxone settings changed - rebuilding transport")
            self._settings = settings
            self._build_transport()
            self._last_sent.clear()

    def _build_transport(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        settings = self._settings
        if settings.mode == MODE_UDP:
            self._transport = UdpTransport(settings.host, settings.udp_port)
        elif settings.mode == MODE_HTTP:
            self._transport = HttpTransport(
                host=settings.host,
                port=settings.http_port,
                scheme=settings.http_scheme,
                user=settings.user,
                password=settings.password,
                timeout=settings.timeout,
            )

    def close(self) -> None:
        with self._lock:
            if self._transport is not None:
                self._transport.close()
                self._transport = None

    # ------------------------------------------------------------------
    def build_payloads(
        self,
        entries: dict[str, ValueEntry],
        mappings: dict[str, ValueMapping],
        force: bool = False,
    ) -> list[Payload]:
        """Format the values that are due to be sent."""
        settings = self._settings
        now = time.time()
        payloads: list[Payload] = []

        with self._lock:
            for short, entry in entries.items():
                mapping = mappings.get(short)
                if mapping is None or not mapping.enabled:
                    continue
                text = format_value(entry.value, mapping, settings.float_format)
                if text == "":
                    continue
                target = f"{settings.prefix}{mapping.target}"

                if not force and settings.send_only_on_change:
                    previous = self._last_sent.get(short)
                    if previous is not None:
                        last_text, last_raw, last_ts = previous
                        heartbeat_due = (
                            settings.heartbeat > 0 and now - last_ts >= settings.heartbeat
                        )
                        if not heartbeat_due and not self._changed(entry.value, last_raw, last_text, text, mapping):
                            continue
                payloads.append(Payload(short=short, target=target, text=text, raw=entry.value))
        return payloads

    @staticmethod
    def _changed(
        raw: Any, last_raw: Any, last_text: str, text: str, mapping: ValueMapping
    ) -> bool:
        if mapping.deadband > 0 and isinstance(raw, (int, float)) and isinstance(last_raw, (int, float)):
            # The deadband is expressed in the unit the user sees, i.e. after
            # the factor has been applied.
            return abs(float(mapping.scale(raw)) - float(mapping.scale(last_raw))) >= mapping.deadband
        return text != last_text

    def send(self, payloads: list[Payload]) -> SendResult:
        """Hand the payloads to the active transport and remember what went out."""
        if not payloads:
            return SendResult()
        with self._lock:
            transport = self._transport
            if transport is None:
                return SendResult()
            result = transport.send(payloads)
            now = time.time()
            for payload in result.sent:
                self._last_sent[payload.short] = (payload.text, payload.raw, now)
        return result

    def publish(
        self,
        entries: dict[str, ValueEntry],
        mappings: dict[str, ValueMapping],
        force: bool = False,
    ) -> tuple[SendResult, list[Payload]]:
        payloads = self.build_payloads(entries, mappings, force=force)
        return self.send(payloads), payloads

    def send_test(self, target: str, text: str) -> SendResult:
        """Send a single value, bypassing mapping and change detection."""
        with self._lock:
            transport = self._transport
            if transport is None:
                return SendResult(failed=1, errors=["Loxone-Push ist deaktiviert"])
            full_target = f"{self._settings.prefix}{target}"
            return transport.send(
                [Payload(short="__test__", target=full_target, text=text, raw=text)]
            )

    def forget(self, short: str | None = None) -> None:
        """Drop the change-detection cache so the next cycle resends everything."""
        with self._lock:
            if short is None:
                self._last_sent.clear()
            else:
                self._last_sent.pop(short, None)
