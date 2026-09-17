"""A minimal Modbus-RTU-over-TCP responder standing in for a real inverter.

It only implements function code 3 (read holding registers), which is all the
LOX-MTEC poller uses.
"""

from __future__ import annotations

import socketserver
import struct
import threading


def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


class FakeInverter:
    """Serves a register dictionary over Modbus RTU-over-TCP."""

    def __init__(self, slave: int = 252) -> None:
        self.slave = slave
        self.registers: dict[int, int] = {}
        self.offline = False
        self._server: socketserver.ThreadingTCPServer | None = None

    # -- register helpers ------------------------------------------------
    def set_u16(self, address: int, value: int) -> None:
        self.registers[address] = value & 0xFFFF

    def set_u32(self, address: int, value: int) -> None:
        self.registers[address] = (value >> 16) & 0xFFFF
        self.registers[address + 1] = value & 0xFFFF

    def set_i32(self, address: int, value: int) -> None:
        self.set_u32(address, value & 0xFFFFFFFF)

    def set_string(self, address: int, text: str, words: int) -> None:
        padded = text.ljust(words * 2, "\x00")
        for index in range(words):
            self.registers[address + index] = (
                ord(padded[2 * index]) << 8
            ) | ord(padded[2 * index + 1])

    # -- server ----------------------------------------------------------
    def start(self, port: int = 0) -> int:
        inverter = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                buffer = b""
                while True:
                    try:
                        chunk = self.request.recv(256)
                    except OSError:
                        return
                    if not chunk:
                        return
                    if inverter.offline:
                        self.request.close()
                        return
                    buffer += chunk
                    while len(buffer) >= 8:
                        frame, buffer = buffer[:8], buffer[8:]
                        slave, function, address, count = struct.unpack(">BBHH", frame[:6])
                        if slave != inverter.slave or function != 3:
                            continue
                        payload = b"".join(
                            struct.pack(">H", inverter.registers.get(address + i, 0))
                            for i in range(count)
                        )
                        body = struct.pack(">BBB", slave, function, len(payload)) + payload
                        try:
                            self.request.sendall(body + crc16(body))
                        except OSError:
                            return

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = Server(("127.0.0.1", port), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self._server.server_address[1]

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
