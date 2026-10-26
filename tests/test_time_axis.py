"""分时坐标的时间含义：全天固定、午休压缩、缺分钟和按数据铺满。"""

import pytest

from stockwidget.config import Store, sanitize
from stockwidget.ui.time_axis import intraday_fraction, intraday_grid_fractions


def test_full_session_uses_actual_time_even_with_missing_minutes():
    minutes = ["09:30", "10:30", "11:30", "13:00", "13:30", "15:00"]
    positions = [intraday_fraction(i, len(minutes), minutes) for i in range(len(minutes))]
    assert positions == pytest.approx([0, 0.25, 0.5, 0.5, 0.625, 1])


def test_fill_width_expands_only_the_available_time_range_and_grid():
    minutes = ["09:31", "09:45", "10:30"]
    assert intraday_fraction(0, 3, minutes) == pytest.approx(1 / 240)
    assert intraday_fraction(2, 3, minutes) == pytest.approx(0.25)
    filled = [intraday_fraction(i, 3, minutes, fill_width=True) for i in range(3)]
    assert filled == pytest.approx([0, 14 / 59, 1])
    assert intraday_grid_fractions(3, minutes, fill_width=True) == pytest.approx([0, 29 / 59, 1])
    assert intraday_grid_fractions(3, minutes) == pytest.approx([i / 8 for i in range(9)])


def test_missing_or_invalid_times_fall_back_without_division_by_zero():
    for minutes in ([], ["bad", "bad"], ["09:30"]):
        assert intraday_fraction(1, 2, minutes) == pytest.approx(1 / 240)
        assert intraday_fraction(1, 2, minutes, fill_width=True) == 1
    assert intraday_fraction(0, 0) == 0
    assert intraday_fraction(0, 1, fill_width=True) == 0
    assert intraday_grid_fractions(1, fill_width=True) == []


def test_fill_width_defaults_off_and_round_trips_per_theme(tmp_path):
    assert sanitize({}).chart_fill_width is False
    assert sanitize({"chart_fill_width": "false"}).chart_fill_width is False
    store = Store(tmp_path / "config.json")
    store.update({"chart_fill_width": True})
    assert Store(tmp_path / "config.json").get().chart_fill_width is True
    assert store.update({"display_theme": "theme2"}).chart_fill_width is False
    assert store.update({"display_theme": "theme1"}).chart_fill_width is True
