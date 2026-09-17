"""Push values into a Loxone 'Virtueller UDP Eingang'.

One datagram per value, formatted as ``<name>: <value>``.  That is exactly what
the Loxone command recognition (``<name>: \\v``) expects, and it keeps a single
lost packet from taking the rest of the values with it.
"""

from __future__ import annotations

import logging
import socket
import threading

from loxmtec.loxone.base import Payload, SendResult, Transport

logger = logging.getLogger(__name__)


class UdpTransport(Transport):
    name = "udp"

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = int(port)
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()

    def _get_socket(self) -> socket.socket:
        if self._socket is None:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.settimeout(2)
        return self._socket

    def send(self, payloads: list[Payload]) -> SendResult:
        result = SendResult()
        if not payloads:
            return result
        if not self.host:
            result.failed = len(payloads)
            result.errors.append("Keine Miniserver-Adresse konfiguriert")
            return result

        with self._lock:
            for payload in payloads:
                message = f"{payload.target}: {payload.text}"
                try:
                    sock = self._get_socket()
                    sock.sendto(message.encode("utf-8"), (self.host, self.port))
                except OSError as err:
                    result.failed += 1
                    if len(result.errors) < 3:
                        result.errors.append(f"UDP-Fehler für '{payload.target}': {err}")
                    # A failed datagram usually means the socket needs recreating.
                    self.close()
                else:
                    result.sent.append(payload)
                    logger.debug("UDP -> %s:%s %s", self.host, self.port, message)
        return result

    def describe(self) -> str:
        return f"UDP an {self.host}:{self.port}"

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:  # pragma: no cover - best effort
                pass
            self._socket = None
