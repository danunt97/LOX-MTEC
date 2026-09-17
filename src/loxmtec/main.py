#!/usr/bin/env python3
"""LOX-MTEC service entry point.

Starts three components in one process:

* the poller thread (Modbus -> data store -> Loxone push)
* the watchdog thread
* the web server (GUI + REST API Loxone can poll)
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from loxmtec import __version__, logbuffer
from loxmtec.config import DEFAULT_CONFIG_PATH, Config, validate
from loxmtec.context import AppContext
from loxmtec.datastore import DataStore
from loxmtec.mapping import ensure_defaults
from loxmtec.presets import DEFAULT_PRESET_KEY, apply_default
from loxmtec.poller import Poller
from loxmtec.registers import RegisterMap, load_register_map
from loxmtec.watchdog import Watchdog

logger = logging.getLogger("loxmtec")

_shutdown = threading.Event()
_exit_code = 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="loxmtec",
        description="M-TEC Energybutler (Modbus) -> Loxone Miniserver bridge",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"Pfad zur config.yaml (Default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument("--host", help="Bind-Adresse des Webservers (überschreibt die config)")
    parser.add_argument("--port", type=int, help="Port des Webservers (überschreibt die config)")
    parser.add_argument(
        "--no-web", action="store_true", help="Nur pollen und pushen, ohne Webserver"
    )
    parser.add_argument("--version", action="version", version=f"LOX-MTEC {__version__}")
    return parser.parse_args(argv)


def request_shutdown(reason: str = "", code: int = 0) -> None:
    global _exit_code
    if code:
        _exit_code = code
    if reason:
        logger.warning("Shutdown requested: %s", reason)
    _shutdown.set()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    config = Config(Path(args.config)).load()
    buffer = logbuffer.setup_logging(
        level=str(config.get("logging", "level", "INFO")),
        capacity=int(config.get("logging", "buffer_size", 500)),
    )
    logger.info("LOX-MTEC %s starting", __version__)
    _check_config_writable(config.path)

    register_map = load_register_map()

    # First start (or a new register map): fill in the per-value defaults and
    # persist them, so the GUI has something to show right away.
    bootstrap_values(config, register_map)

    problems = validate(config.as_dict())
    for problem in problems:
        logger.warning("Konfiguration: %s", problem)

    store = DataStore()
    poller = Poller(config, register_map, store)
    ctx = AppContext(
        config=config,
        registers=register_map,
        store=store,
        poller=poller,
        logbuffer=buffer,
        version=__version__,
    )

    poller.start()

    watchdog = Watchdog(
        config,
        store,
        poller,
        on_fatal=lambda reason: request_shutdown(reason, code=1),
    )
    watchdog.start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda number, frame: request_shutdown(f"Signal {number}"))

    if not args.no_web:
        _start_web(ctx, host=args.host, port=args.port)
    else:
        logger.info("Web server disabled (--no-web)")

    _shutdown.wait()

    logger.info("Stopping LOX-MTEC")
    watchdog.stop()
    poller.stop()
    poller.join(timeout=10)
    logging.shutdown()
    return _exit_code


def bootstrap_values(config: Config, register_map: RegisterMap) -> bool:
    """Fill in the per-value defaults, and shape a brand new config for Loxone.

    On a fresh installation the Energiemonitor preset is applied once, so the
    container delivers kW with the block's sign convention without anyone
    having to configure anything. An existing configuration is only completed
    with missing entries - it is never re-shaped, because from then on it
    belongs to the user.

    Returns True if something was written.
    """
    values = config.section("values")
    first_run = not values
    completed = ensure_defaults(values, register_map)

    if first_run:
        completed = apply_default(completed)
        logger.info(
            "Fresh configuration - applying the '%s' preset so the values fit "
            "the Loxone Energiemonitor out of the box",
            DEFAULT_PRESET_KEY,
        )

    if completed == values:
        return False
    config.replace_values(completed)
    config.save()
    logger.info("Value mapping initialised with %d entries", len(completed))
    return True


def _check_config_writable(path: Path) -> None:
    """Warn early instead of failing later when the GUI tries to save."""
    import os

    directory = path.parent
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        logger.error("Config-Verzeichnis %s konnte nicht angelegt werden: %s", directory, err)
        return
    target = path if path.exists() else directory
    if not os.access(target, os.W_OK):
        logger.warning(
            "%s ist nicht beschreibbar (UID %s) - Einstellungen aus der Weboberfläche können "
            "nicht gespeichert werden. Im Docker-Betrieb hilft: chown -R 1000:1000 <config-Ordner>",
            target,
            os.getuid() if hasattr(os, "getuid") else "?",
        )


def _start_web(ctx: AppContext, host: str | None = None, port: int | None = None) -> None:
    """Run the web server in a daemon thread so signals stay with the main thread."""
    from loxmtec.web import create_app

    web_cfg = ctx.config.section("web")
    bind_host = host or str(web_cfg.get("host", "0.0.0.0"))
    bind_port = int(port or web_cfg.get("port", 8080))
    app = create_app(ctx)

    def serve() -> None:
        try:
            from waitress import serve as waitress_serve

            logger.info("Web GUI on http://%s:%s", bind_host, bind_port)
            waitress_serve(app, host=bind_host, port=bind_port, threads=8, ident="LOX-MTEC")
        except ImportError:
            logger.warning("waitress is not installed - falling back to the Flask dev server")
            app.run(host=bind_host, port=bind_port, threaded=True, use_reloader=False)
        except OSError as err:
            logger.fatal("Web server couldn't start on %s:%s: %s", bind_host, bind_port, err)
            request_shutdown("Webserver konnte nicht starten", code=1)

    thread = threading.Thread(target=serve, name="web", daemon=True)
    thread.start()


if __name__ == "__main__":
    sys.exit(main())
