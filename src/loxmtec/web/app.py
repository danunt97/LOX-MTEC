"""Flask application: web GUI plus the REST API Loxone can poll."""

from __future__ import annotations

import logging
import secrets
from functools import wraps
from typing import Any, Callable

from flask import Flask, Response, has_request_context, jsonify, render_template, request

from loxmtec import __version__, logbuffer
from loxmtec.config import DEFAULTS, deep_merge, validate
from loxmtec.const import LOXONE_MODES, MODE_HTTP, MODE_UDP
from loxmtec.context import AppContext
from loxmtec.loxone.client import format_value
from loxmtec.loxone.template import (
    TEMPLATE_TYPE_HTTP,
    TEMPLATE_TYPE_UDP,
    http_template,
    udp_template,
)
from loxmtec.mapping import (
    default_decimals,
    duplicate_targets,
    ensure_defaults,
    load_mappings,
    sanitize_target,
)

logger = logging.getLogger(__name__)

# Sections the GUI may write, and the keys it may write in them.
EDITABLE_SECTIONS = ("modbus", "poll", "loxone", "web", "mqtt", "watchdog", "logging")
PASSWORD_KEYS = ("password", "auth_password")
SECRET_PLACEHOLDER = "********"


def create_app(ctx: AppContext) -> Flask:
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    app.ctx = ctx  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # authentication
    # ------------------------------------------------------------------
    def _credentials() -> tuple[str, str]:
        web = ctx.config.section("web")
        return str(web.get("auth_user") or ""), str(web.get("auth_password") or "")

    def protected(view: Callable) -> Callable:
        @wraps(view)
        def wrapper(*args, **kwargs):
            user, password = _credentials()
            if not user:
                return view(*args, **kwargs)
            auth = request.authorization
            if (
                auth
                and auth.username is not None
                and secrets.compare_digest(auth.username, user)
                and secrets.compare_digest(auth.password or "", password)
            ):
                return view(*args, **kwargs)
            return Response(
                "Anmeldung erforderlich",
                401,
                {"WWW-Authenticate": 'Basic realm="LOX-MTEC"'},
            )

        return wrapper

    # ------------------------------------------------------------------
    # pages
    # ------------------------------------------------------------------
    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {"version": ctx.version or __version__}

    @app.route("/")
    @protected
    def page_dashboard():
        return render_template("dashboard.html", page="dashboard")

    @app.route("/values")
    @protected
    def page_values():
        return render_template("values.html", page="values")

    @app.route("/settings")
    @protected
    def page_settings():
        return render_template("settings.html", page="settings", modes=LOXONE_MODES)

    @app.route("/loxone")
    @protected
    def page_loxone():
        return render_template("loxone.html", page="loxone")

    @app.route("/log")
    @protected
    def page_log():
        return render_template("log.html", page="log")

    # ------------------------------------------------------------------
    # REST API - values (this is what Loxone polls in pull mode)
    # ------------------------------------------------------------------
    @app.route("/api/v1/values")
    @protected
    def api_values():
        return jsonify(ctx.store.flat())

    @app.route("/api/v1/values/full")
    @protected
    def api_values_full():
        return jsonify(ctx.store.detailed())

    @app.route("/api/v1/value/<short>")
    @protected
    def api_value(short: str):
        entry = ctx.store.get(short)
        if entry is None:
            return Response(f"Unbekannter Wert: {short}", 404, mimetype="text/plain")
        mapping = ctx.poller.mappings.get(short)
        text = format_value(entry.value, mapping) if mapping else str(entry.value)
        return Response(text, mimetype="text/plain")

    @app.route("/api/v1/status")
    @protected
    def api_status():
        status = ctx.store.status()
        status["loxone"]["transport"] = ctx.poller.loxone.describe()
        status["version"] = ctx.version or __version__
        return jsonify(status)

    @app.route("/healthz")
    def api_health():
        stale_after = int(ctx.config.get("watchdog", "stale_after", 120))
        healthy = ctx.store.healthy(stale_after) and ctx.poller.is_alive()
        payload = {
            "status": "ok" if healthy else "unhealthy",
            "modbus": ctx.store.modbus_state,
            "poller": "alive" if ctx.poller.is_alive() else "dead",
        }
        return jsonify(payload), (200 if healthy else 503)

    # ------------------------------------------------------------------
    # REST API - configuration
    # ------------------------------------------------------------------
    @app.route("/api/v1/config", methods=["GET"])
    @protected
    def api_config_get():
        return jsonify(_redact(ctx.config.as_dict()))

    @app.route("/api/v1/config", methods=["POST"])
    @protected
    def api_config_post():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "errors": ["Ungültige Anfrage"]}), 400

        current = ctx.config.as_dict()
        incoming = {
            section: values
            for section, values in payload.items()
            if section in EDITABLE_SECTIONS and isinstance(values, dict)
        }
        if not incoming:
            return jsonify({"ok": False, "errors": ["Keine bekannten Abschnitte übergeben"]}), 400

        incoming = _restore_secrets(incoming, current)
        incoming = _coerce_types(incoming, DEFAULTS)
        candidate = deep_merge(current, incoming)

        errors = validate(candidate)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400

        for section, values in incoming.items():
            ctx.config.update_section(section, values)
        if not ctx.config.save():
            return jsonify({"ok": False, "errors": ["Konfiguration konnte nicht gespeichert werden"]}), 500

        logbuffer.set_level(str(ctx.config.get("logging", "level", "INFO")))
        ctx.poller.request_reload()
        logger.info("Configuration updated via web GUI: %s", ", ".join(sorted(incoming)))
        return jsonify({"ok": True, "config": _redact(ctx.config.as_dict())})

    # ------------------------------------------------------------------
    # REST API - value mapping
    # ------------------------------------------------------------------
    @app.route("/api/v1/mapping", methods=["GET"])
    @protected
    def api_mapping_get():
        mappings = load_mappings(ctx.config.section("values"), ctx.registers)
        store_values = ctx.store.values()
        rows = []
        for register in ctx.registers.published():
            mapping = mappings[register.short]
            entry = store_values.get(register.short)
            rows.append(
                {
                    "short": register.short,
                    "name": register.name,
                    "register": register.key,
                    "group": register.group,
                    "unit": register.unit,
                    "numeric": register.is_numeric,
                    "writable": register.writable,
                    "enabled": mapping.enabled,
                    "target": mapping.target,
                    "decimals": mapping.decimals,
                    "deadband": mapping.deadband,
                    "default_decimals": default_decimals(register),
                    "value": entry.value if entry else None,
                    "updated": entry.as_dict()["updated"] if entry else None,
                }
            )
        return jsonify(
            {
                "prefix": ctx.config.get("loxone", "prefix", ""),
                "groups": ctx.registers.groups,
                "rows": rows,
                "duplicates": duplicate_targets(mappings, ctx.config.get("loxone", "prefix", "")),
            }
        )

    @app.route("/api/v1/mapping", methods=["POST"])
    @protected
    def api_mapping_post():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("values"), dict):
            return jsonify({"ok": False, "errors": ["Ungültige Anfrage"]}), 400

        current = ctx.config.section("values")
        for short, row in payload["values"].items():
            register = ctx.registers.by_short(short)
            if register is None or not isinstance(row, dict):
                continue
            entry = dict(current.get(short) or {})
            if "enabled" in row:
                entry["enabled"] = bool(row["enabled"])
            if "target" in row:
                entry["target"] = sanitize_target(row["target"], short)
            if "decimals" in row:
                entry["decimals"] = row["decimals"]
            if "deadband" in row:
                entry["deadband"] = row["deadband"]
            current[short] = entry

        cleaned = ensure_defaults(current, ctx.registers)
        ctx.config.replace_values(cleaned)
        if not ctx.config.save():
            return jsonify({"ok": False, "errors": ["Konfiguration konnte nicht gespeichert werden"]}), 500

        ctx.poller.request_reload()
        mappings = load_mappings(cleaned, ctx.registers)
        duplicates = duplicate_targets(mappings, ctx.config.get("loxone", "prefix", ""))
        logger.info("Value mapping updated via web GUI (%d Werte)", len(payload["values"]))
        return jsonify({"ok": True, "duplicates": duplicates})

    # ------------------------------------------------------------------
    # REST API - actions
    # ------------------------------------------------------------------
    @app.route("/api/v1/actions/reconnect", methods=["POST"])
    @protected
    def api_reconnect():
        ok = ctx.poller.reconnect()
        ctx.poller.trigger()
        return jsonify({"ok": ok, "message": "Verbunden" if ok else "Verbindung fehlgeschlagen"})

    @app.route("/api/v1/actions/poll", methods=["POST"])
    @protected
    def api_poll():
        ctx.poller.trigger()
        return jsonify({"ok": True, "message": "Abfrage angestoßen"})

    @app.route("/api/v1/actions/resend", methods=["POST"])
    @protected
    def api_resend():
        ctx.poller.request_resend()
        return jsonify({"ok": True, "message": "Alle Werte werden erneut gesendet"})

    @app.route("/api/v1/actions/test", methods=["POST"])
    @protected
    def api_test():
        payload = request.get_json(silent=True) or {}
        target = sanitize_target(payload.get("target") or "loxmtec_test")
        text = str(payload.get("value", "1"))
        result = ctx.poller.loxone.send_test(target, text)
        if result.failed:
            return jsonify({"ok": False, "message": result.error or "Senden fehlgeschlagen"}), 200
        return jsonify(
            {
                "ok": True,
                "message": f"'{target}: {text}' via {ctx.poller.loxone.describe()} gesendet",
            }
        )

    # ------------------------------------------------------------------
    # REST API - logs
    # ------------------------------------------------------------------
    @app.route("/api/v1/log")
    @protected
    def api_log():
        buffer = ctx.logbuffer or logbuffer.get_buffer()
        if buffer is None:
            return jsonify({"entries": []})
        try:
            since = int(request.args.get("since", 0))
        except ValueError:
            since = 0
        return jsonify({"entries": buffer.entries(since=since)})

    @app.route("/api/v1/log", methods=["DELETE"])
    @protected
    def api_log_clear():
        buffer = ctx.logbuffer or logbuffer.get_buffer()
        if buffer is not None:
            buffer.clear()
        return jsonify({"ok": True})

    # ------------------------------------------------------------------
    # Loxone Config templates
    # ------------------------------------------------------------------
    @app.route("/api/v1/loxone-template/<kind>")
    @protected
    def api_template(kind: str):
        mappings = load_mappings(ctx.config.section("values"), ctx.registers)
        loxone = ctx.config.section("loxone")
        prefix = str(loxone.get("prefix") or "")
        only_enabled = request.args.get("all", "0") not in ("1", "true", "yes")

        if kind == MODE_UDP:
            xml = udp_template(
                ctx.registers,
                mappings,
                prefix=prefix,
                port=int(loxone.get("udp_port", 7000)),
                only_enabled=only_enabled,
                template_type=_template_type(TEMPLATE_TYPE_UDP),
            )
            filename = "loxmtec-udp-eingang.xml"
        elif kind == MODE_HTTP:
            url = request.args.get("url") or _default_pull_url(ctx)
            xml = http_template(
                ctx.registers,
                mappings,
                url=url,
                prefix=prefix,
                polling_time=int(ctx.config.get("poll", "now", 10)),
                only_enabled=only_enabled,
                template_type=_template_type(TEMPLATE_TYPE_HTTP),
            )
            filename = "loxmtec-http-eingang.xml"
        else:
            return Response(f"Unbekannte Vorlage: {kind}", 404, mimetype="text/plain")

        return Response(
            xml,
            mimetype="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _template_type(default: int) -> int:
    """Allow ?type=N so a different Loxone Config version can be tried without
    waiting for a new release."""
    raw = request.args.get("type")
    if raw is None:
        return default
    try:
        return max(0, min(99, int(raw)))
    except ValueError:
        return default


def _default_pull_url(ctx: AppContext) -> str:
    """Best guess for the URL Loxone should poll - the host the GUI was opened on."""
    port = ctx.config.get("web", "port", 8080)
    host = request.host.split(":")[0] if has_request_context() else "loxmtec"
    return f"http://{host}:{port}/api/v1/values"


def _redact(data: dict[str, Any]) -> dict[str, Any]:
    """Never hand real passwords to the browser."""
    result: dict[str, Any] = {}
    for section, values in data.items():
        if not isinstance(values, dict):
            result[section] = values
            continue
        copied = dict(values)
        for key in PASSWORD_KEYS:
            if copied.get(key):
                copied[key] = SECRET_PLACEHOLDER
        result[section] = copied
    return result


def _restore_secrets(incoming: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Keep the stored password when the GUI sends back the placeholder."""
    for section, values in incoming.items():
        for key in PASSWORD_KEYS:
            if values.get(key) == SECRET_PLACEHOLDER:
                values[key] = current.get(section, {}).get(key, "")
    return incoming


def _coerce_types(incoming: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """HTML forms send strings - coerce them to the type of the default value."""
    result: dict[str, Any] = {}
    for section, values in incoming.items():
        reference = defaults.get(section, {})
        cleaned: dict[str, Any] = {}
        for key, value in values.items():
            if key not in reference:
                continue  # ignore unknown keys instead of polluting the config
            default = reference[key]
            cleaned[key] = _coerce(value, default)
        result[section] = cleaned
    return result


def _coerce(value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, str):
        return str(value).strip()
    return value


__all__ = ["create_app"]
