"""Composition root shared by the service entry point and the web application."""

from __future__ import annotations

from dataclasses import dataclass

from loxmtec.config import Config
from loxmtec.datastore import DataStore
from loxmtec.logbuffer import RingBufferHandler
from loxmtec.poller import Poller
from loxmtec.registers import RegisterMap


@dataclass
class AppContext:
    config: Config
    registers: RegisterMap
    store: DataStore
    poller: Poller
    logbuffer: RingBufferHandler | None = None
    version: str = ""
