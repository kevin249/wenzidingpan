"""数据源：解析、停牌处理与失败降级。"""

from __future__ import annotations

import pytest
import requests

from stockwidget import providers
from stockwidget.providers.eastmoney import EastmoneyProvider, detect_scale
from stockwidget.providers.mock import MockProvider
from stockwidget.providers.textquote import SinaProvider, TencentProvider, decode_gbk


def test_registry_defaults_to_eastmoney():
    assert providers.DEFAULT_PROVIDER == "eastmoney"
    assert providers.resolve("does-not-exist").id == "eastmoney"
    ids = [item["id"] for item in providers.listing()]
    assert ids == ["eastmoney", "tencent", "sina", "mock"]
    assert "yahoo" not in ids  # 已聚焦 A 股，美股数据源不再提供


def test_tencent_parses_and_handles_halt():
    parsed = TencentProvider().parse('v_sh600000="1~浦发银行~600000~0.00~10.00~9.9~1~2~3";')
    assert parsed["sh600000"] == ("浦发银行", 0.0, 10.0)


def test_sina_parses_and_rejects_empty_payload():
    provider = SinaProvider()
    assert provider.parse('var hq_str_sh600000="浦发银行,9.9,10.00,10.20,";')["sh600000"] == (
        "浦发银行",
        10.20,
        10.00,
    )
    assert provider.parse('var hq_str_sh600001="";')["sh600001"] is None


def test_halted_stock_falls_back_to_prev_close():
    """停牌时现价为 0，直接用会显示成跌停 -100%。"""
    quotes = _fetch_with_body(TencentProvider(), b'v_sh600000="1~\xc6\xd6\xb7\xa2~600000~0.00~10.00~9.9~1~2~3";')
    assert quotes[0].price == 10.0
    assert quotes[0].change == 0
    assert quotes[0].halted is True


def test_decode_gbk_falls_back_without_crashing():
    assert decode_gbk("浦发银行".encode("gbk")) == "浦发银行"
    assert decode_gbk(b"\xff\xfe\xff") != ""  # 不抛异常


@pytest.mark.parametrize(
    "rows,expected",
    [
        ([{"f2": 10.2, "f18": 10, "f3": 2}], 1),
        ([{"f2": 1020, "f18": 1000, "f3": 200}], 100),
        ([], 1),
    ],
)
def test_eastmoney_detects_price_scaling(rows, expected):
    assert detect_scale(rows) == expected


def test_eastmoney_maps_rows_to_quotes():
    provider = EastmoneyProvider(session=_FakeSession({"data": {"diff": [
        {"f2": 1680.5, "f18": 1640.0, "f3": 2.47, "f12": "600519", "f13": 1, "f14": "贵州茅台"},
    ]}}))
    quote = provider.fetch(["600519"])[0]
    assert (quote.name, quote.price, quote.prev_close) == ("贵州茅台", 1680.5, 1640.0)
    assert quote.change == pytest.approx(40.5)


def test_network_failure_degrades_per_symbol():
    """数据源挂掉时每一行显示原因，而不是整屏空白或抛异常。"""
    provider = EastmoneyProvider(session=_FailingSession(requests.Timeout()))
    quotes = provider.fetch(["600519", "000001"])
    assert [q.error for q in quotes] == ["请求超时", "请求超时"]
    assert all(q.price is None for q in quotes)


def test_invalid_symbol_is_reported_on_its_own_row():
    quotes = EastmoneyProvider(session=_FakeSession({"data": {"diff": []}})).fetch(["不是代码"])
    assert quotes[0].error == "代码格式不正确"


def test_mock_provider_needs_no_network():
    quotes = MockProvider().fetch(["600519", "000001"])
    assert len(quotes) == 2
    assert all(q.price and q.price > 0 and q.error is None for q in quotes)


# --------------------------------------------------------------- 测试替身


class _FakeResponse:
    def __init__(self, payload=None, content=b""):
        self._payload = payload
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload=None, content=b""):
        self._response = _FakeResponse(payload, content)

    def get(self, *args, **kwargs):
        return self._response


class _FailingSession:
    def __init__(self, error: Exception):
        self._error = error

    def get(self, *args, **kwargs):
        raise self._error


def _fetch_with_body(provider, body: bytes):
    provider.session = _FakeSession(content=body)
    return provider.fetch(["600000"])
