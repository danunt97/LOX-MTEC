"""Value mapping: names, defaults and duplicate detection."""

from __future__ import annotations

import pytest

from loxmtec.mapping import (
    MAX_TARGET_LENGTH,
    duplicate_targets,
    ensure_defaults,
    load_mappings,
    sanitize_target,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("pv_power", "pv_power"),
        ("  PV Leistung  ", "PV_Leistung"),
        ("a/b\\c:d", "a_b_c_d"),
        ("###", "value"),
        ("", "value"),
        ("a###b", "a_b"),
    ],
)
def test_sanitize_target(raw, expected):
    assert sanitize_target(raw) == expected


def test_sanitize_target_is_length_capped():
    assert len(sanitize_target("x" * 200)) == MAX_TARGET_LENGTH


def test_ensure_defaults_covers_every_published_register(register_map):
    values = ensure_defaults({}, register_map)
    assert set(values) == set(register_map.shorts)
    assert all(entry["enabled"] for entry in values.values())
    assert values["pv"]["target"] == "pv"


def test_ensure_defaults_keeps_and_repairs_existing_entries(register_map):
    values = ensure_defaults(
        {
            "pv": {"enabled": False, "target": "PV Leistung", "decimals": "3", "deadband": "-5"},
            "gone_register": {"enabled": True},
        },
        register_map,
    )
    assert values["pv"] == {
        "enabled": False,
        "target": "PV_Leistung",
        "decimals": 3,
        "deadband": 0.0,  # negative deadbands are clamped
    }
    assert "gone_register" not in values  # stale entries are dropped


def test_decimals_default_follows_the_unit(register_map):
    values = ensure_defaults({}, register_map)
    assert values["pv"]["decimals"] == 0  # W
    assert values["pv_day"]["decimals"] == 2  # kWh
    assert values["battery_soc"]["decimals"] == 1  # %


def test_duplicate_targets_only_reports_enabled_collisions(register_map):
    values = ensure_defaults({}, register_map)
    values["pv"]["target"] = "same"
    values["consumption"]["target"] = "same"
    values["grid_power"]["target"] = "same"
    values["grid_power"]["enabled"] = False
    mappings = load_mappings(values, register_map)

    duplicates = duplicate_targets(mappings, prefix="mtec_")
    assert duplicates == {"mtec_same": ["consumption", "pv"]}
