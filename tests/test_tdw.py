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
    current = worker.take_snapshot().trends[symbol]
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


def test_live_bar_can_trigger_withdraw_and_confirm_on_both_sides(monkeypatch):
    from stockwidget.kline import Kline
    from stockwidget.tdw import MinuteFeed, align

    feed = MinuteFeed("603986", "tencent")
    at = START + timedelta(minutes=24, seconds=10)
    initial = bars([10.] * 25 + [14.])
    stamp = initial[-1].time
    calls = []
    answers = [initial, bars([10.] * 26)[-5:], initial[-5:], initial[-5:]]
    def fetch(code, period, count, **kwargs):
        calls.append((count, kwargs))
        return Kline(symbol=code, period=period, source="tencent", bars=answers.pop(0))
    monkeypatch.setattr(feed.client, "fetch", fetch)
    for seconds, value, tags in [(0, 1.6, ("B1", "B2")), (15, 0., ()), (23, 1.6, ("B1", "B2"))]:
        point = feed.fetch(now=at + timedelta(seconds=seconds))[stamp]
        assert point.provisional and point.power == pytest.approx(value) and point.events == tags
    p = feed.fetch(now=at + timedelta(minutes=1))[stamp]
    assert not p.provisional and p.power == pytest.approx(1.6)
    assert align({stamp: p}, [stamp])[1] == [(0, "B1/B2")]
    assert [c for c, kw in calls] == [500, 5, 5, 5]
    assert all(kw == {"fresh": True, "adjust": False} for _, kw in calls)


@pytest.mark.parametrize("clock, expected", [
    ("09:29:59", ""), ("09:30:00", "09:31"), ("11:29:59", "11:30"),
    ("11:30:00", ""), ("12:59:59", ""), ("13:00:00", "13:01"),
    ("14:59:59", "15:00"), ("15:00:00", ""),
])
def test_forming_minute_uses_shanghai_session(clock, expected):
    from stockwidget.tdw import forming_minute
    now = datetime.fromisoformat(f"2026-10-23 {clock}").replace(tzinfo=SHANGHAI_TZ)
    assert forming_minute(now) == ("2026-10-23 " + expected if expected else "")


def test_intraminute_ohlc_reuses_fresh_chart_and_aligns_the_live_tail(monkeypatch):
    from stockwidget.intraday import Trend
    from stockwidget.kline import Kline
    from stockwidget.tdw import MinuteFeed, align

    feed = MinuteFeed("603986", "auto")
    at = START + timedelta(minutes=24, seconds=10)
    day = at.date().isoformat()
    history = bars([10.] * 26)
    stamp = history[-1].time
    monkeypatch.setattr(feed.client, "fetch", lambda *args, **kw: Kline(bars=history, source="eastmoney"))
    feed.fetch(now=at)
    monkeypatch.setattr(feed.client, "fetch", lambda *args, **kw: pytest.fail("duplicate network request"))
    chart = Trend(trade_date=day, minutes=["09:54", "09:55"], prices=[10., 10.], volumes=[1., 2.],
                  source="eastmoney", fetched_at=at.timestamp()+1,
                  last_minute_bar=Bar(stamp, 10., 14., 10., 14., 28.))
    points = feed.fetch(chart, now=at + timedelta(seconds=2))
    displayed = chart.with_tdw(points, now=at)
    assert displayed.minutes[-1] == "09:56" and displayed.prices[-1] == 14. and displayed.volumes[-1] == 28.
    assert align(points, (f"{day} {m}" for m in displayed.minutes))[1] == [(2, "B1/B2*")]
    assert chart.minutes == ["09:54", "09:55"]
    assert stamp not in chart.with_tdw(points, now=at + timedelta(minutes=1)).tdw_points


def test_gap_backfills_warm_history_and_lunch_does_not_create_a_bar(monkeypatch):
    from stockwidget.kline import Kline
    from stockwidget.tdw import MinuteFeed

    feed = MinuteFeed("603986", "tencent")
    data = bars([11.] * 120)
    calls = []
    packets = [data[:30], data[-5:], data]
    def fetch(symbol, period, count, **kwargs):
        calls.append(count)
        return Kline(symbol=symbol, source="tencent", bars=packets.pop(0))
    monkeypatch.setattr(feed.client, "fetch", fetch)
    feed.fetch(now=START + timedelta(minutes=30))
    noon = START.replace(hour=12, minute=0)
    points = feed.fetch(now=noon)
    assert calls == [500, 5, 500]
    assert max(points).endswith("11:30")
    assert not any(point.provisional for point in points.values())


def test_numeric_input_preserves_confirmed_history_after_live_revisions():
    data = bars([10.] * 25 + [14.] * 10)
    at = START + timedelta(minutes=30, seconds=23)
    confirmed = calculate(data, PERIOD_1M, at)
    live = calculate(data, PERIOD_1M, at, live=True)
    assert {k: p for k, p in live.items() if not p.provisional} == confirmed
    assert len(live) == len(confirmed) + 1
    assert max(live) == data[31].time
