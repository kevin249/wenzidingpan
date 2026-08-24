"""股票搜索：代码/名称/拼音匹配由东财 suggest 提供，失败时回退到「直接当代码用」。"""

from __future__ import annotations

import requests

from stockwidget.search import StockSearch, parse


def _payload(rows: list[dict]) -> dict:
    return {"QuotationCodeTable": {"Data": rows}}


def test_parse_keeps_a_shares_only():
    results = parse(
        _payload([
            {"Code": "600519", "Name": "贵州茅台", "MktNum": "1"},
            {"Code": "000001", "Name": "平安银行", "MktNum": "0"},
            {"Code": "00700", "Name": "腾讯控股", "MktNum": "116"},  # 港股
            {"Code": "AAPL", "Name": "苹果", "MktNum": "105"},  # 美股
        ])
    )
    assert [r["code"] for r in results] == ["600519", "000001"]
    assert results[0] == {"code": "600519", "name": "贵州茅台", "market": "sh"}


def test_parse_drops_duplicates_and_bad_codes():
    results = parse(
        _payload([
            {"Code": "600519", "Name": "贵州茅台", "MktNum": "1"},
            {"Code": "600519", "Name": "贵州茅台", "MktNum": "1"},
            {"Code": "", "Name": "空代码", "MktNum": "1"},
            {"Code": "abc", "Name": "非法", "MktNum": "1"},
        ])
    )
    assert [r["code"] for r in results] == ["600519"]


def test_parse_tolerates_garbage_payload():
    assert parse(None) == []
    assert parse({}) == []
    assert parse({"QuotationCodeTable": {"Data": ["不是字典"]}}) == []


def test_query_returns_results():
    search = StockSearch(session=_FakeSession(_payload([
        {"Code": "600519", "Name": "贵州茅台", "MktNum": "1"},
    ])))
    result = search.query("gzmt")
    assert result["error"] is None
    assert result["results"][0]["name"] == "贵州茅台"


def test_query_ignores_blank_input_without_network():
    search = StockSearch(session=_FailingSession(AssertionError("不该发起请求")))
    assert search.query("  ") == {"results": [], "error": None}


def test_query_falls_back_to_raw_code_when_offline():
    """搜索接口挂了也得让人能手输代码，否则离线时根本没法加自选。"""
    result = StockSearch(session=_FailingSession(requests.Timeout())).query("600519")
    assert result["error"] == "请求超时"
    assert result["results"] == [{"code": "600519", "name": "", "market": "sh"}]


def test_query_offline_with_non_code_returns_nothing_to_add():
    result = StockSearch(session=_FailingSession(requests.Timeout())).query("茅台")
    assert result["results"] == []
    assert result["error"] == "请求超时"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload

    def get(self, *args, **kwargs):
        return _FakeResponse(self._payload)


class _FailingSession:
    def __init__(self, error):
        self._error = error

    def get(self, *args, **kwargs):
        raise self._error
