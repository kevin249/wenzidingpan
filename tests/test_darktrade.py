"""暗盘资金：字段映射、分页、缓存与失败降级。

字段口径对齐 gupiao_ztfx 的 src/dark_trade/eastmoney.py。
"""

from __future__ import annotations

import requests

from stockwidget.darktrade import DarkTradeClient, format_date, normalize_row


def test_normalize_row_maps_eastmoney_fields():
    row = normalize_row(
        {"3": 1, "4": "600519", "16": "贵州茅台", "6": 123456789, "7": 1000,
         "8": -5000, "11": 3.2, "13": 1680000, "14": 2.35, "21": 7}
    )
    assert row.code == "600519"
    assert row.market == "sh"
    assert row.name == "贵州茅台"
    assert row.dark_fund == 123456789  # 单位：元
    assert row.main_net_inflow == -5000
    assert row.price == 1680.0  # 接口放大了 1000 倍
    assert row.rank == 7


def test_normalize_row_pads_code_and_rejects_empty():
    assert normalize_row({"4": "1", "3": 0}).code == "000001"
    assert normalize_row({"4": ""}) is None
    assert normalize_row("not a dict") is None


def test_format_date():
    assert format_date("20260817") == "2026-08-17"
    assert format_date("2026-08-17") == "2026-08-17"
    assert format_date("bad") == ""


def test_pagination_stops_once_watchlist_is_covered():
    """排行榜按暗盘资金降序分页，自选全部命中就不必继续翻页。"""
    pages = _paged_session(total=1000, codes_by_page={1: ["600519"], 2: ["000001"], 3: ["300750"]})
    client = DarkTradeClient(session=pages)
    result = client.fetch({"600519", "000001"})
    assert set(result.by_code) == {"600519", "000001"}
    assert pages.calls == 2  # 第 3 页没有必要再拉


def test_pagination_covers_watchlist_beyond_page_50():
    """榜单超过 5000 条时，尾部股票也必须能取到。"""
    pages = _paged_session(total=5340, codes_by_page={54: ["603986"]})
    client = DarkTradeClient(session=pages)
    result = client.fetch({"603986"})
    assert set(result.by_code) == {"603986"}
    assert pages.calls == 54


def test_cache_avoids_refetching_within_ttl():
    pages = _paged_session(total=100, codes_by_page={1: ["600519"]})
    client = DarkTradeClient(session=pages)
    client.fetch({"600519"}, now=1000.0)
    client.fetch({"600519"}, now=1000.0 + 60)
    assert pages.calls == 1
    # 自选里出现没覆盖过的代码时必须重新拉
    client.fetch({"600519", "000001"}, now=1000.0 + 61)
    assert pages.calls > 1


def test_failure_returns_error_instead_of_raising():
    result = DarkTradeClient(session=_FailingSession(requests.Timeout())).fetch({"600519"})
    assert result.error == "请求超时"
    assert result.by_code == {}


def test_api_level_error_is_surfaced():
    result = DarkTradeClient(session=_ErrorPayloadSession()).fetch({"600519"})
    assert "暂无数据" in (result.error or "")


# --------------------------------------------------------------- 测试替身


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _PagedSession:
    def __init__(self, total, codes_by_page):
        self.total = total
        self.codes_by_page = codes_by_page
        self.calls = 0

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        page = params["StartPage"]
        rows = [{"4": code, "3": 1, "6": 1000, "16": code} for code in self.codes_by_page.get(page, [])]
        return _Response({"errid": 0, "1": "20260817", "2": self.total, "data": rows})


def _paged_session(total, codes_by_page):
    return _PagedSession(total, codes_by_page)


class _FailingSession:
    def __init__(self, error):
        self._error = error

    def get(self, *args, **kwargs):
        raise self._error


class _ErrorPayloadSession:
    def get(self, *args, **kwargs):
        return _Response({"errid": 1, "errmsg": "暂无数据"})
