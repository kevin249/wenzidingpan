"""当日分时数据：解析、时段过滤、缓存与失败降级。

字段口径对齐 gupiao_ztfx 的 trading/services/intraday_kline.py。
"""

from __future__ import annotations

import pytest
import requests

from stockwidget.intraday import IntradayClient, Trend, parse_eastmoney, parse_tencent


def _em(trends: list[str], prev_close: float | None = 10.0) -> dict:
    return {"data": {"preClose": prev_close, "trends": trends}}


def test_eastmoney_takes_close_price_and_prev_close():
    trend = parse_eastmoney(
        _em([
            "2026-08-24 09:30,10.00,10.10,10.20,9.90,100,1000,10.05",
            "2026-08-24 09:31,10.10,10.30,10.30,10.00,120,1200,10.15",
        ])
    )
    assert trend.prices == [10.10, 10.30]  # 取的是收盘价（第 3 个字段）
    assert trend.prev_close == 10.0
    assert bool(trend) is True


@pytest.mark.parametrize("stamp", ["2026-08-24 09:30", "2026-08-24 09:30:00"])
def test_eastmoney_handles_both_timestamp_formats(stamp):
    """接口的时间戳带不带秒都出现过，负数切片会切错，这里两种都要能认。"""
    trend = parse_eastmoney(_em([f"{stamp},10.00,10.10,10.20,9.90,100,1000,10.05"]))
    assert trend.prices == [10.10]


def test_eastmoney_filters_non_trading_minutes():
    """盘前集合竞价与午休时段的点不该画进分时图。"""
    trend = parse_eastmoney(
        _em([
            "2026-08-24 09:25,9.9,9.90,9.9,9.9,0,0,9.9",     # 盘前
            "2026-08-24 09:30,10.0,10.10,10.2,9.9,100,1000,10.05",
            "2026-08-24 12:00,10.3,10.40,10.4,10.3,50,500,10.35",  # 午休
            "2026-08-24 14:59,10.3,10.50,10.5,10.3,50,500,10.35",
            "2026-08-24 15:30,10.3,10.60,10.6,10.3,50,500,10.35",  # 盘后
        ])
    )
    assert trend.prices == [10.10, 10.50]


def test_eastmoney_skips_malformed_and_zero_rows():
    trend = parse_eastmoney(
        _em([
            "",
            "2026-08-24 09:30,10.0",                          # 字段不够
            "2026-08-24 09:31,10.0,0,10.2,9.9,100,1000,10.05",  # 价格为 0
            "2026-08-24 09:32,10.0,abc,10.2,9.9,100,1000,10.05",  # 非数字
            "2026-08-24 09:33,10.0,10.40,10.4,10.3,50,500,10.35",
        ])
    )
    assert trend.prices == [10.40]


def test_eastmoney_rejects_bad_payload():
    assert parse_eastmoney(None).error == "返回格式异常"
    assert parse_eastmoney({"data": None}).error == "返回格式异常"


def test_trend_is_falsy_without_enough_points():
    """一个点画不出曲线，要让调用方回退到采样点。"""
    assert not Trend(prices=[10.0])
    assert not Trend()
    assert Trend(prices=[10.0, 10.1])


def test_tencent_fallback_parses_minutes():
    payload = {"data": {"sh600000": {"data": {"data": [
        "0930 10.10 100 1000",
        "0931 10.30 220 2200",
        "1201 9.90 1 1",  # 午休，应被滤掉
    ]}}}}
    assert parse_tencent(payload, "sh600000").prices == [10.10, 10.30]


def test_tencent_rejects_bad_payload():
    assert parse_tencent(None, "sh600000").error == "返回格式异常"
    assert parse_tencent({}, "sh600000").prices == []


def test_client_falls_back_to_tencent_when_eastmoney_empty():
    session = _ScriptedSession([
        _Response(_em([], prev_close=None)),  # 东财返回空
        _Response({"data": {"sh600000": {"data": {"data": [
            "0930 10.10 100 1000", "0931 10.30 220 2200",
        ]}}}}),
    ])
    trend = IntradayClient(session=session).fetch("600000")
    assert trend.prices == [10.10, 10.30]
    assert session.calls == 2


def test_client_caches_within_ttl():
    session = _ScriptedSession(
        [_Response(_em(["2026-08-24 09:30,10.0,10.10,10.2,9.9,1,1,10.0",
                        "2026-08-24 09:31,10.1,10.30,10.3,10.0,1,1,10.1"]))] * 4
    )
    client = IntradayClient(session=session)
    client.fetch("600000", now=1000.0)
    client.fetch("600000", now=1030.0)
    assert session.calls == 1
    client.fetch("600000", now=1000.0 + 61)  # 超过 TTL
    assert session.calls == 2


def test_client_reports_error_instead_of_raising():
    trend = IntradayClient(session=_FailingSession(requests.Timeout())).fetch("600000")
    assert trend.error == "请求超时"
    assert trend.prices == []


def test_client_rejects_invalid_symbol_without_network():
    session = _FailingSession(AssertionError("不该发起请求"))
    assert IntradayClient(session=session).fetch("不是代码").error == "代码格式不正确"


# --------------------------------------------------------------- 测试替身


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _ScriptedSession:
    """按顺序吐出预设响应，用来验证主源/兜底源的调用次数。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return self._responses.pop(0)


class _FailingSession:
    def __init__(self, error):
        self._error = error

    def get(self, *args, **kwargs):
        raise self._error
