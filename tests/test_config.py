"""Configuration loading, validation and persistence."""

from __future__ import annotations

import yaml

from loxmtec.config import Config, deep_merge, validate


def test_defaults_are_valid_except_missing_miniserver(tmp_path):
    cfg = Config(tmp_path / "c.yaml").load()
    problems = validate(cfg.as_dict())
    assert problems == ["Loxone: Miniserver IP/Host wird für Push-Betrieb benötigt"]


def test_file_values_win_over_defaults(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"modbus": {"host": "1.2.3.4"}}), encoding="utf-8")
    cfg = Config(path).load()
    assert cfg.get("modbus", "host") == "1.2.3.4"
    assert cfg.get("modbus", "port") == 5743  # untouched default survives


def test_env_overrides_file(tmp_path, monkeypatch):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"modbus": {"host": "from-file"}}), encoding="utf-8")
    monkeypatch.setenv("LOXMTEC_MODBUS_HOST", "from-env")
    monkeypatch.setenv("LOXMTEC_WEB_PORT", "9999")
    monkeypatch.setenv("LOXMTEC_MQTT_ENABLED", "yes")
    cfg = Config(path).load()
    assert cfg.get("modbus", "host") == "from-env"
    assert cfg.get("web", "port") == 9999
    assert cfg.get("mqtt", "enabled") is True


def test_invalid_env_value_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("LOXMTEC_WEB_PORT", "not-a-number")
    cfg = Config(tmp_path / "c.yaml").load()
    assert cfg.get("web", "port") == 8080


def test_save_and_reload_round_trip(tmp_path):
    path = tmp_path / "c.yaml"
    cfg = Config(path).load()
    cfg.update_section("loxone", {"host": "192.168.1.10", "prefix": "mtec_"})
    assert cfg.save()
    reloaded = Config(path).load()
    assert reloaded.get("loxone", "host") == "192.168.1.10"
    assert reloaded.get("loxone", "prefix") == "mtec_"


def test_broken_yaml_falls_back_to_defaults(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("this: [is: broken", encoding="utf-8")
    cfg = Config(path).load()
    assert cfg.get("modbus", "port") == 5743


def test_deep_merge_keeps_untouched_keys():
    merged = deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 3}})
    assert merged == {"a": {"x": 1, "y": 3}}


def test_validate_reports_every_problem():
    broken = Config().load().as_dict()
    broken["modbus"]["host"] = ""
    broken["poll"]["now"] = 0
    broken["loxone"]["mode"] = "carrier-pigeon"
    broken["loxone"]["float_format"] = "{:.3q}"
    broken["watchdog"]["exit_after"] = 30
    problems = validate(broken)
    assert any("Host/IP" in problem for problem in problems)
    assert any("'now'" in problem for problem in problems)
    assert any("Modus" in problem for problem in problems)
    assert any("float_format" in problem for problem in problems)
    assert any("exit_after" in problem for problem in problems)
