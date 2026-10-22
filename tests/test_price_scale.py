"""验证平盘/一分钱、窗口边缘及跨日切换时的缩放行为。"""

from datetime import datetime, timedelta

import pytest

from stockwidget.ui.price_scale import PriceScale


@pytest.mark.parametrize("center", [None, 100.0])
def test_flat_and_one_tick_do_not_switch_scale(center):
    scale = PriceScale()
    initial = scale.bounds(100.0, 100.0, center=center)
    for high in [100.01, 100.0] * 30:
        assert scale.bounds(100.0, high, center=center) == initial


@pytest.mark.parametrize("center", [None, 100.0])
def test_boundary_oscillation_only_expands_once(center):
    scale = PriceScale()
    _low, edge = scale.bounds(99.0, 101.0, center=center)
    expanded = scale.bounds(99.0, edge + 0.01, center=center)
    assert expanded[1] > edge + 0.01
    for high in [edge - 0.01, edge + 0.01] * 30:
        assert scale.bounds(99.0, high, center=center) == expanded


@pytest.mark.parametrize("center", [None, 100.0])
def test_real_breakout_stays_inside_and_large_contraction_refits(center):
    scale = PriceScale()
    scale.bounds(99.0, 101.0, center=center)
    wide = scale.bounds(95.0, 107.0, center=center)
    assert wide[0] < 95.0 < 107.0 < wide[1]
    narrow = scale.bounds(99.9, 100.1, center=center)
    assert narrow[1] - narrow[0] < (wide[1] - wide[0]) * 0.1
    assert narrow[0] < 99.9 < 100.1 < narrow[1]


def test_small_center_changes_keep_scale_and_exact_current_price_center():
    scale = PriceScale()
    low, high = scale.bounds(99.0, 101.0, center=100.0)
    for center in [100.01, 99.99, 100.0] * 30:
        moved_low, moved_high = scale.bounds(99.0, 101.0, center=center)
        assert moved_high - moved_low == pytest.approx(high - low)
        assert (moved_high + moved_low) / 2.0 == pytest.approx(center)


def test_reset_refits_instead_of_retaining_padding():
    scale = PriceScale()
    scale.bounds(99.0, 101.0)
    expanded = scale.bounds(99.0, 101.11)
    scale.reset()
    assert scale.bounds(99.0, 101.0) != expanded


def test_new_shanghai_day_refits(monkeypatch):
    import stockwidget.ui.price_scale as module

    now = datetime(2026, 10, 22, 15, tzinfo=module.SHANGHAI_TZ)

    class Clock:
        @staticmethod
        def now(tz):
            return now.astimezone(tz)

    monkeypatch.setattr(module, "datetime", Clock)
    scale = PriceScale()
    initial = scale.bounds(99.0, 101.0)
    expanded = scale.bounds(99.0, 101.11)
    assert scale.bounds(99.0, 101.0) == expanded
    now += timedelta(days=1)
    assert scale.bounds(99.0, 101.0) == initial
