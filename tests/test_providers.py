"""数据源：解析、停牌处理与失败降级。"""

from __future__ import annotations

import pytest
import requests

from stockwidget import providers
from stockwidget.providers.auto import AutoProvider
from stockwidget.providers.base import Quote
from stockwidget.providers.eastmoney import EastmoneyProvider, detect_scale
from stockwidget.providers.mock import MockProvider
from stockwidget.providers.textquote import SinaProvider, TencentProvider, decode_gbk


def test_registry_defaults_to_auto():
    assert providers.DEFAULT_PROVIDER == "auto"
    assert providers.resolve("does-not-exist").id == "auto"
    ids = [item["id"] for item in providers.listing()]
    assert ids == ["auto", "eastmoney", "tencent", "sina", "mock"]
    assert "yahoo" not in ids  # 已聚焦 A 股，美股数据源不再提供


def test_auto_provider_falls_back_to_next_source():
    """前面的源没出数就换下一个，并记住能用的那个。"""
    dead = _StubProvider("dead", [Quote.failed("600519", "HTTP 403")])
    alive = _StubProvider("alive", [Quote.from_prices("600519", "贵州茅台", 1680.0, 1640.0)])
    auto = AutoProvider([dead, alive])

    quotes = auto.fetch(["600519"])
    assert quotes[0].price == 1680.0
    assert auto.last_used == "alive"
    assert dead.calls == 1

    # 下一轮优先用上次成功的那个，不再白试前面的死源
    auto.fetch(["600519"])
    assert dead.calls == 1
    assert alive.calls == 2


def test_auto_provider_survives_raising_source():
    boom = _RaisingProvider("boom")
    alive = _StubProvider("alive", [Quote.from_prices("600519", "贵州茅台", 1680.0, 1640.0)])
    assert AutoProvider([boom, alive]).fetch(["600519"])[0].price == 1680.0


def test_auto_provider_reports_first_error_when_all_fail():
    """全都不出数时保留第一个源的原因，而不是笼统说一句失败。"""
    first = _StubProvider("first", [Quote.failed("600519", "HTTP 403")])
    second = _StubProvider("second", [Quote.failed("600519", "请求超时")])
    auto = AutoProvider([first, second])
    assert auto.fetch(["600519"])[0].error == "HTTP 403"
    assert auto.last_used is None


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


class _StubProvider:
    """固定返回预设行情的数据源，用来驱动自动回退逻辑。"""

    def __init__(self, provider_id: str, quotes: list[Quote]):
        self.id = provider_id
        self.label = provider_id
        self.placeholder = ""
        self._quotes = quotes
        self.calls = 0

    def fetch(self, symbols: list[str]) -> list[Quote]:
        self.calls += 1
        return list(self._quotes)


class _RaisingProvider:
    def __init__(self, provider_id: str):
        self.id = provider_id
        self.label = provider_id
        self.placeholder = ""

    def fetch(self, symbols: list[str]) -> list[Quote]:
        raise RuntimeError("源挂了")


def _fetch_with_body(provider, body: bytes):
    provider.session = _FakeSession(content=body)
    return provider.fetch(["600000"])
