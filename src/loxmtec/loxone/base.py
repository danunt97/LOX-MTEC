"""Common types for the Loxone transports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Payload:
    """One value on its way to the Miniserver."""

    short: str
    target: str  # virtual input name, prefix already applied
    text: str  # formatted value as it goes over the wire
    raw: Any  # original value, used for change detection


@dataclass
class SendResult:
    """Outcome of one send, per payload.

    ``sent`` lists the payloads the transport accepted - guessing from the
    counters would be wrong as soon as failures are interleaved.
    """

    sent: list[Payload] = field(default_factory=list)
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> int:
        return len(self.sent)

    @property
    def error(self) -> str | None:
        return self.errors[0] if self.errors else None

    def merge(self, other: "SendResult") -> "SendResult":
        self.sent.extend(other.sent)
        self.failed += other.failed
        self.errors.extend(other.errors)
        return self


class Transport:
    """Interface implemented by the UDP and HTTP senders."""

    name = "none"

    def send(self, payloads: list[Payload]) -> SendResult:  # pragma: no cover - interface
        raise NotImplementedError

    def describe(self) -> str:  # pragma: no cover - interface
        return self.name

    def close(self) -> None:
        """Release sockets/sessions. Safe to call more than once."""
