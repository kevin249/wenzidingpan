from dataclasses import replace
from datetime import datetime, timedelta
import json

import pytest
import requests

from stockwidget import mcp_bs, tdw
from stockwidget.config import Config
from stockwidget.intraday import Trend
from stockwidget.kline import Bar
from stockwidget.market_hours import SHANGHAI_TZ
from stockwidget.poller import Poller
from stockwidget.realtime import EASTMONEY, TENCENT, RealtimeFeed, second_bars, with_price_history
from stockwidget.ui.time_axis import intraday_fraction

DAY = "2026-11-05"
START = datetime.fromisoformat(DAY + " 09:31:00").replace(tzinfo=SHANGHAI_TZ)
CODE = "603986"


class Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    @property
    def text(self):
        return self.payload

    def raise_for_status(self):
        pass


class Session:
    def __init__(self, respond):
        self.respond = respond
        self.calls = []

    def get(self, url, params, **kwargs):
        self.calls.append((url, params))
        return Response(self.respond(url, params))


def em_data(lines, code=CODE, market=1):
    return {"data": {"code": code, "market": market, "details": lines}}


def test_eastmoney_groups_only_real_trades_and_polling_twice_does_not_add_volume():
    rows = ["09:25:00,9,10,0,4", "09:30:02,12,2,1,2", "09:30:02,10,3,1,1",
            "09:30:02,11,4,1,2", "09:30:04,11,3,1,1", "09:30:04,inf,3,1,1"]
    def respond(url, params):
        assert params["secid"] == "1.603986" and url.startswith(EASTMONEY)
        if url.endswith("/get") and "fields" in params:
            return {"data": {"f57": CODE, "f86": int(START.timestamp())}}
        return em_data(rows)

    session = Session(respond)
    feed = RealtimeFeed(CODE, "eastmoney", session)
    first = feed.fetch(now=START)
    assert first.source == "eastmoney" and first.minutes == ["09:30:02", "09:30:04"]
    assert feed._bars[0] == Bar(DAY+" 09:30:02", 12, 12, 10, 11, 9)
    assert feed.fetch(now=START) is first
    rows.append("09:30:04,13,5,1,2")
    second = feed.fetch(now=START)
    assert second.prices == [11, 13] and second.volumes == [9, 8]
    assert first.volumes == [9, 3]
    assert len([p for u, p in session.calls if "fields" in p]) == 1


def test_tencent_uses_dated_index_and_handles_equal_second_across_two_pages():
    rows = [["0/09:31:01/10/0/2/0/M", "1/09:31:03/11/0/3/0/B"],
            ["2/09:31:03/9/0/4/0/S", "3/09:31:05/12/0/1/0/B"]]
    def respond(url, params):
        assert url == TENCENT and params["c"] == "sh"+CODE
        if params["action"] == "t":
            spans = "|".join(f"{r[0].split('/')[1]}~{r[-1].split('/')[1]}" for r in rows)
            return "v_detail_time_sh603986="+json.dumps([20261105, spans])+";"
        n = params["p"]
        return "v_detail_data_sh603986="+json.dumps([n, "|".join(rows[n])])+";"
    session = Session(respond)
    feed = RealtimeFeed(CODE, "tencent", session)
    at = START + timedelta(seconds=10)
    first = feed.fetch(now=at)
    assert first.minutes == ["09:31:01", "09:31:03", "09:31:05"]
    assert feed._bars[1] == Bar(DAY+" 09:31:03", 11, 11, 9, 9, 7)
    assert feed.fetch(now=at) is first
    assert [p["p"] for u, p in session.calls if "p" in p] == [0, 1, 1]
    assert feed.fetch(now=at+timedelta(days=1)) is None


def test_realtime_formula_advances_each_distinct_second_and_revises_last_in_place(monkeypatch):
    feed = RealtimeFeed(CODE, "eastmoney")
    at = START + timedelta(seconds=30)
    def bars(closes):
        return [Bar((START+timedelta(seconds=i)).strftime("%Y-%m-%d %H:%M:%S"), 10, 14, 10, c)
                for i, c in enumerate(closes)]
    original = bars([10] * 25 + [14])
    packets = [original, bars([10] * 26)[-3:], original[-3:], bars([10]*25+[14,14])[-3:]]
    monkeypatch.setattr(feed, "_eastmoney", lambda *args: packets.pop(0))
    for value, events in [(1.6, ("B1", "B2")), (0, ()), (1.6, ("B1", "B2"))]:
        current = feed.fetch(now=at)
        point = current.tdw_points[DAY+" 09:31:25"]
        assert point.power == pytest.approx(value) and point.events == events and point.provisional
        assert len(current.tdw_points) == 2
    current = feed.fetch(now=at)
    first, second = (current.tdw_points[DAY+clock] for clock in (" 09:31:25", " 09:31:26"))
    assert not first.provisional and second.provisional and second.power == pytest.approx(2.56)
    assert intraday_fraction(25, 27, current.minutes) < intraday_fraction(26, 27, current.minutes)
    assert tdw.align(current.tdw_points, (DAY+" "+m for m in current.minutes))[1] == [(25, "B1/B2")]


@pytest.mark.parametrize("configured", ["auto", "sina"])
def test_fallback_only_uses_eastmoney_and_tencent_and_rewarms_on_switch(monkeypatch, configured):
    feed = RealtimeFeed(CODE, configured)
    at = START + timedelta(minutes=2)
    em = second_bars(DAY, [("09:30:03", 8, 3), ("09:31:03", 10, 2)])
    tx = second_bars(DAY, [("09:31:03", 9, 1), ("09:31:06", 12, 3)])
    requests_made = []
    def eastmoney(_, warm):
        requests_made.append(("eastmoney", warm))
        if len(requests_made) == 1:
            return em
        raise requests.Timeout()
    def tencent(_, warm):
        requests_made.append(("tencent", warm))
        return tx
    monkeypatch.setattr(feed, "_eastmoney", eastmoney)
    monkeypatch.setattr(feed, "_tencent", tencent)
    assert feed.fetch(now=at).source == "eastmoney"
    result = feed.fetch(now=at)
    assert result.source == "tencent" and result.prices == [9, 12]
    assert requests_made == [("eastmoney", True), ("eastmoney", False), ("tencent", True)]


def test_gap_rewarms_without_splicing_and_delayed_packets_cannot_rewind(monkeypatch):
    feed = RealtimeFeed(CODE, "tencent")
    at = START + timedelta(minutes=3)
    def make(*clocks):
        return second_bars(DAY, [(c, 10+i, 1) for i,c in enumerate(clocks)])
    first = make("09:31:01", "09:31:02")
    tail = make("09:32:30", "09:32:31")
    warm = make("09:32:01", "09:32:30", "09:32:31")
    results = iter([first, tail, warm, first])
    calls = []
    def fetch(_, warm):
        calls.append(warm)
        return next(results)
    monkeypatch.setattr(feed, "_tencent", fetch)
    feed.fetch(now=at)
    result = feed.fetch(now=at)
    assert result.minutes == ["09:32:01", "09:32:30", "09:32:31"]
    assert feed.fetch(now=at) is None and feed._trend is result
    assert calls == [True, False, True, False]


def test_mismatched_vendor_responses_never_produce_tdw():
    def respond(url, params):
        if "fields" in params:
            return {"data": {"f57": CODE, "f86": int(START.timestamp())}}
        if url.startswith(EASTMONEY):
            return em_data(["09:30:00,20,1,1,2"], market=0)
        return 'v_detail_time_sz000001=[20261105,"09:30:01~09:30:04"];'
    assert RealtimeFeed(CODE, "auto", Session(respond)).fetch(now=START) is None


@pytest.mark.parametrize("chart_first", [False, True])
def test_both_workers_publish_by_trade_date_without_feeding_minutes_into_tdw(monkeypatch, chart_first):
    monkeypatch.setattr(mcp_bs, "get_source", lambda: "local")
    cfg = Config(symbols=[CODE])
    worker = Poller(cfg)
    live = Trend(prices=[10,11], volumes=[1,3], minutes=["09:31:03", "09:31:06"],
                 trade_date=DAY, tdw_points={DAY+" 09:31:06": tdw.Point(.6,11,("B2",))})
    history = Trend(prices=[8,18,19], volumes=[5,6,7], minutes=["09:30", "09:31", "09:32"], trade_date=DAY)
    jobs = [("chart", history), ("tdw", live)]
    for kind, value in (jobs if chart_first else reversed(jobs)):
        worker._accept((kind, CODE), value, cfg, 0)
    trend = worker.take_snapshot().trends[CODE]
    assert trend.minutes == ["09:30", "09:31:03", "09:31:06"] and trend.prices == [8,10,11]
    assert list(trend.tdw_points) == [DAY+" 09:31:06"] and not history.tdw_points
    assert with_price_history(live, replace(history, trade_date="2026-11-04")) is live
    cfg = replace(cfg, intraday_chart=False)
    for theme in ("theme1", "theme2"):
        selected = replace(cfg, display_theme=theme)
        assert ("tdw", CODE) in worker._jobs(selected) and ("chart", CODE) not in worker._jobs(selected)
    assert ("tdw", CODE) not in worker._jobs(replace(cfg, show_bs_points=False))
    monkeypatch.setattr(mcp_bs, "get_source", lambda: "mcp")
    assert ("tdw", CODE) not in worker._jobs(cfg)


def test_quiet_auto_feed_uses_newer_vendor_but_never_invents_elapsed_bars(monkeypatch):
    feed = RealtimeFeed(CODE, "auto")
    old = second_bars(DAY, [("09:31:00", 10, 1), ("09:31:01", 11, 1)])
    fresh = second_bars(DAY, [("09:35:58", 12, 1), ("09:35:59", 13, 1)])
    at = START + timedelta(minutes=5)
    monkeypatch.setattr(feed, "_eastmoney", lambda *args: old)
    monkeypatch.setattr(feed, "_tencent", lambda *args: fresh)
    result = feed.fetch(now=at)
    assert result.source == "tencent" and result.minutes == ["09:35:58", "09:35:59"]
    assert result.tdw_points == {}


def test_partial_second_cannot_rewind_from_delayed_response_or_advance_at_lunch():
    feed = RealtimeFeed(CODE, "eastmoney")
    def packet(volume):
        return second_bars(DAY, [("11:29:58", 10, 2), ("11:30:01", 11, volume)])
    noon = START.replace(hour=12)
    complete = feed._merge("eastmoney", packet(4), noon)
    assert feed._merge("eastmoney", packet(3), noon) is None
    afternoon = feed._merge("eastmoney", packet(4), noon.replace(hour=13))
    assert afternoon is complete and feed._forming == ""
    assert afternoon.minutes[-1] == "11:30:01"


def test_offline_profile_performs_no_realtime_or_minute_market_requests(monkeypatch):
    monkeypatch.setattr(mcp_bs, "get_source", lambda: "local")
    cfg = Config(symbols=[CODE], display_theme="theme2", provider="mock", intraday_chart=False)
    assert not any(kind in ("chart", "tdw") for kind, _ in Poller._jobs(cfg))
