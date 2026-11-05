from datetime import datetime, timedelta

import pytest

from stockwidget.kline import Bar, PERIOD_DAY, PERIOD_1M
from stockwidget.market_hours import SHANGHAI_TZ
from stockwidget.tdw import calculate


START = datetime(2026, 10, 23, 9, 31, tzinfo=SHANGHAI_TZ)


def bars(closes):
    return [Bar((START + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M"), c, 14., 10., c)
            for i, c in enumerate(closes)]


def test_power_and_all_four_crossings_and_closed_bar_cutoff():
    data = bars([10.] * 25 + [14.] * 12 + [10.] * 8)
    points = calculate(data, PERIOD_1M, START + timedelta(minutes=len(data)))
    assert len(points) == len(data) - 24
    assert points[data[25].time].power == pytest.approx(1.6)
    assert points[data[25].time].events == ("B1", "B2")
    assert points[data[37].time].events == ("S1", "S2")
    cutoff = START + timedelta(minutes=30)
    assert calculate(data, PERIOD_1M, cutoff) == calculate(data[:31], PERIOD_1M, cutoff)
    assert list(calculate(data, PERIOD_1M, cutoff))[-1] == data[30].time


def test_filters_are_independent_and_suppress_twenty_following_bars():
    data = bars([10.] * 25 + [14.] + [10.] * 4 + [14.] + [10.] * 15 + [14.])
    p = calculate(data, PERIOD_1M, START + timedelta(hours=5))
    assert p[data[25].time].events == ("B1", "B2")
    assert p[data[30].time].events == ()
    assert p[data[46].time].events == ("B1", "B2")


def test_daily_warmup_and_flat_market_do_not_create_entries():
    flat = [Bar((START + timedelta(days=i)).strftime("%Y-%m-%d"), 10, 10, 10, 10) for i in range(30)]
    assert not calculate(flat, PERIOD_DAY, START + timedelta(days=40))
    day = flat[-1].time
    flat[-1] = Bar(day, 10, 12, 10, 11)
    assert day not in calculate(flat, PERIOD_DAY, datetime.fromisoformat(day+" 14:59").replace(tzinfo=SHANGHAI_TZ))
    assert calculate(flat, PERIOD_DAY, datetime.fromisoformat(day+" 15:00").replace(tzinfo=SHANGHAI_TZ))[day].power == 2
