"""Calculated pseudo registers."""

from __future__ import annotations

import pytest

from loxmtec import calc
from loxmtec.modbus import Value


def data(**registers) -> dict[str, Value]:
    return {key: Value("x", value) for key, value in registers.items()}


def test_consumption_is_load_minus_grid():
    assert calc.calculate("consumption", data(**{"11016": 3000, "11000": 1200})) == 1800.0


def test_day_statistics():
    day = data(**{"31005": 20.0, "31001": 5.0, "31004": 1.0, "31000": 4.0, "31003": 2.0})
    assert calc.calculate("consumption-day", day) == 20.0
    assert calc.calculate("autarky-day", day) == pytest.approx(75.0)
    assert calc.calculate("ownconsumption-day", day) == pytest.approx(80.0)


def test_total_statistics():
    total = data(
        **{"31112": 50.0, "31104": 10.0, "31110": 2.0, "31102": 8.0, "31108": 4.0}
    )
    assert calc.calculate("consumption-total", total) == 50.0
    assert calc.calculate("autarky-total", total) == pytest.approx(80.0)
    assert calc.calculate("ownconsumption-total", total) == pytest.approx(84.0)


def test_missing_dependency_returns_none():
    assert calc.calculate("consumption", data(**{"11016": 3000})) is None
    assert calc.calculate("consumption-day", {}) is None


def test_division_by_zero_is_reported_as_zero():
    zero = data(**{"31005": 0.0, "31001": 0.0, "31004": 0.0, "31000": 0.0, "31003": 0.0})
    assert calc.calculate("autarky-day", zero) == 0
    assert calc.calculate("ownconsumption-day", zero) == 0


def test_negative_results_are_clamped():
    # More grid feed-in than production can make the raw difference negative.
    negative = data(**{"11016": 100, "11000": 500})
    assert calc.calculate("consumption", negative) == 0.0


def test_api_date_is_a_timestamp():
    value = calc.calculate("api-date", {})
    assert len(value) == 19 and value[4] == "-"


def test_unknown_pseudo_register():
    assert calc.calculate("does-not-exist", {}) is None
