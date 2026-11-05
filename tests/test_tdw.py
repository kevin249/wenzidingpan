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


@pytest.mark.parametrize("chart_first", [True, False])
def test_minute_worker_joins_by_stamp_in_either_arrival_order(chart_first):
    from stockwidget.config import Config
    from stockwidget.intraday import Trend
    from stockwidget.poller import Poller
    from stockwidget.tdw import Point, align

    symbol = "603986"
    config = Config(symbols=[symbol])
    worker = Poller(config)
    point = Point(.6, 11., ("B2",))
    points = {"2026-10-23 09:31": point, "2026-10-22 09:33": point}
    trend = Trend(prices=[11., 12.], minutes=["09:31", "09:33"], trade_date="2026-10-23")
    jobs = [("chart", trend), ("tdw", points)]
    for kind, value in (jobs if chart_first else reversed(jobs)):
        worker._accept((kind, symbol), value, config, 0)
    worker._accept(("tdw", symbol), None, config, 0)  # A failed retry retains the closed bars.
    current = worker._trend_cache[symbol]
    assert align(current.tdw_points, (f"{current.trade_date} {m}" for m in current.minutes)) == (
        [.6, None], [(0, "B2")])
    assert not trend.tdw_points
    assert align(points, ["2026-10-24 09:31"]) == ([], [])


def test_only_local_enabled_intraday_mode_schedules_ohlc(monkeypatch):
    from dataclasses import replace
    from stockwidget import mcp_bs
    from stockwidget.config import Config
    from stockwidget.poller import Poller

    cfg = Config(symbols=["603986"], show_bs_points=True)
    monkeypatch.setattr(mcp_bs, "get_source", lambda: "local")
    assert ("tdw", "603986") in Poller._jobs(cfg)
    for other in (replace(cfg, intraday_chart=False), replace(cfg, show_bs_points=False)):
        assert ("tdw", "603986") not in Poller._jobs(other)
    monkeypatch.setattr(mcp_bs, "get_source", lambda: "mcp")
    assert ("tdw", "603986") not in Poller._jobs(cfg)
