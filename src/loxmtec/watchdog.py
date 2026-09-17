"""Watchdog thread.

Three escalation levels:

1. the poll cycles keep succeeding  -> everything is fine
2. no successful read for ``stale_after`` seconds -> force a Modbus reconnect
3. still nothing after ``exit_after`` seconds -> terminate the process so the
   container runtime (``restart: unless-stopped``) gives us a clean start

It also notices if the poller thread died and restarts the process in that case,
because a container that is up but no longer polling is the worst failure mode.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

from loxmtec.config import Config
from loxmtec.const import STATE_ERROR, STATE_OK, STATE_WARN
from loxmtec.datastore import DataStore
from loxmtec.poller import Poller

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 10
HARD_EXIT_GRACE = 15


class Watchdog(threading.Thread):
    daemon = True

    def __init__(
        self,
        config: Config,
        store: DataStore,
        poller: Poller,
        on_fatal: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(name="watchdog")
        self.config = config
        self.store = store
        self.poller = poller
        self.on_fatal = on_fatal
        self._stopping = threading.Event()

    def stop(self) -> None:
        self._stopping.set()

    # ------------------------------------------------------------------
    def run(self) -> None:  # pragma: no cover - thread entry point
        logger.info("Watchdog started")
        while not self._stopping.wait(CHECK_INTERVAL):
            try:
                self.check()
            except Exception as err:
                logger.exception("Watchdog check failed: %s", err)

    def check(self) -> None:
        settings = self.config.section("watchdog")
        if not settings.get("enabled", True):
            self.store.set_watchdog(STATE_OK, "deaktiviert")
            return

        stale_after = max(10, int(settings.get("stale_after", 120)))
        exit_after = int(settings.get("exit_after", 900))

        if not self.poller.is_alive():
            self._fatal("Poller-Thread ist gestorben")
            return

        last_ok = self.store.modbus_last_ok
        reference = last_ok if last_ok is not None else self.store.started
        age = time.time() - reference

        if age <= stale_after:
            self.store.set_watchdog(STATE_OK, f"letzte Daten vor {int(age)}s")
            return

        if exit_after and age > exit_after:
            self._fatal(f"Seit {int(age)}s keine Daten vom Wechselrichter")
            return

        self.store.set_watchdog(
            STATE_WARN, f"Seit {int(age)}s keine Daten - versuche Reconnect"
        )
        logger.warning("Watchdog: no data for %ds - forcing a Modbus reconnect", int(age))
        self.poller.reconnect()
        self.poller.trigger()

    # ------------------------------------------------------------------
    def _fatal(self, reason: str) -> None:
        self.store.set_watchdog(STATE_ERROR, f"{reason} - Neustart")
        logger.fatal("Watchdog: %s - terminating so the container restarts", reason)
        self._stopping.set()
        if self.on_fatal is not None:
            try:
                self.on_fatal(reason)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Fatal callback failed")
            # Graceful shutdown gets a short grace period, then we pull the plug.
            threading.Timer(HARD_EXIT_GRACE, lambda: os._exit(1)).start()
        else:
            os._exit(1)
