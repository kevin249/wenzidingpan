"""交易日由行情确定；收盘后的尾部成交不能覆盖完整分钟 OHLC。"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import requests
from stockwidget import realtime, tdw
from stockwidget.kline import Bar, PERIOD_1M
from stockwidget.market_hours import SHANGHAI_TZ

DAY = '2026-11-11'
HISTORY = [Bar(f'{DAY} 14:{i:02}', 10, 20, 8, 12+i/100, 100) for i in range(30, 60)]
HISTORY += [Bar(f'{DAY} 15:00', 12, 16, 11, 15, 200)]


@pytest.mark.parametrize('clock', ['2026-11-11 23:50', '2026-11-12 00:10', '2026-11-14 10:00'])
def test_off_hours_uses_record_trade_day_and_full_minute_ohlc(monkeypatch, clock):
    monkeypatch.setattr(realtime, 'fetch_kline', lambda *args, **kw: SimpleNamespace(bars=HISTORY, source='tencent'))
    feed = realtime.RealtimeFeed('603986', 'tencent', minute_fallback=True)
    ticks = realtime.second_bars(DAY, [('14:44:32', 13, 1), ('14:59:59', 14, 2), ('15:00:02', 15, 3)])
    monkeypatch.setattr(feed, '_tencent', lambda *args: ticks)
    at = datetime.fromisoformat(clock).replace(tzinfo=SHANGHAI_TZ)
    result = feed.fetch(now=at)
    expected = tdw.calculate(HISTORY, PERIOD_1M, at, partial_windows=True)
    assert DAY == result.trade_date
    assert len(HISTORY) == len(feed._history_bars)
    assert DAY+' 14:30' == next(iter(result.tdw_points))
    for tick in ticks:
        assert expected[tick.time[:16]].power == pytest.approx(result.tdw_points[tick.time].power)


def test_midnight_does_not_erase_warm_history_or_restart_ema(monkeypatch):
    monkeypatch.setattr(realtime, 'fetch_kline', lambda *args, **kw: SimpleNamespace(bars=HISTORY, source='tencent'))
    feed = realtime.RealtimeFeed('603986', 'tencent', minute_fallback=True)
    ticks = realtime.second_bars(DAY, [('14:44:32', 13, 1), ('15:00:02', 15, 3)])
    monkeypatch.setattr(feed, '_tencent', lambda *args: ticks)
    at = datetime(2026,11,11,23,59,59,tzinfo=SHANGHAI_TZ)
    before = feed.fetch(now=at)
    after = feed.fetch(now=at+timedelta(seconds=2))
    assert before.tdw_points == after.tdw_points
    assert HISTORY == feed._history_bars


def test_history_only_recovery_uses_last_available_session_but_not_at_next_open(monkeypatch):
    monkeypatch.setattr(realtime, 'fetch_kline', lambda *args, **kw: SimpleNamespace(bars=HISTORY, source='tencent'))
    feed = realtime.RealtimeFeed('603986', 'tencent', minute_fallback=True)
    def fail(*args):
        raise requests.Timeout()
    monkeypatch.setattr(feed, '_tencent', fail)
    result = feed.fetch(now=datetime(2026,11,12,0,10,tzinfo=SHANGHAI_TZ))
    assert DAY == result.trade_date
    assert len(HISTORY) == len(result.tdw_points)
    assert feed.fetch(now=datetime(2026,11,12,9,35,tzinfo=SHANGHAI_TZ)) is None
