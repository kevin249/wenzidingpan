"""Stock search parsing, provider failover and manual code fallback."""

from __future__ import annotations

import json

import pytest
import requests

from stockwidget.search import ENDPOINT, MAX_RESULTS, TENCENT_ENDPOINT, StockSearch, parse, parse_tencent


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


def test_eastmoney_excludes_funds_and_accepts_numeric_market_zero():
    results = parse(_payload([
        {"Code": "000001", "Name": "平安银行", "MktNum": 0, "Classify": "AStock"},
        {"Code": "163803", "Name": "中银持续增长", "MktNum": "0", "Classify": "Fund"},
        {"Code": "510300", "Name": "沪深300ETF", "MktNum": "1"},
        {"Code": "600519", "Name": "非股票", "MktNum": "1", "Classify": "Fund"},
    ]))
    assert [row["code"] for row in results] == ["000001"]


def _tencent(rows: str) -> bytes:
    return ('v_hint=' + json.dumps(rows) + ';').encode("ascii")


def test_tencent_decodes_names_and_excludes_other_securities():
    text = _tencent("^".join([
        "sh~603986~兆易创新~zycx~GP-A",
        "sh~603986~重复~zycx~GP-A",
        "sh~688191~智洋创新~zycx~GP-A-KCB",
        "sz~000001~平安银行~payh~GP-A",
        "bj~920001~北交所股票~bjs~GP-A",
        "hk~03986~兆易创新~zycx~GP",
        "jj~163803~中银持续增长~zycx~KJ",
        "sh~000001~上证指数~szzs~ZS",
        "sh~900901~云赛B股~ysbg~GP-B",
        "sz~510300~ETF~etf~GP-A",
        "malformed",
    ])).decode("ascii")
    results = parse_tencent(text)
    assert [row["code"] for row in results] == ["603986", "688191", "000001", "920001"]
    assert results[0] == {"code": "603986", "name": "兆易创新", "market": "sh"}


def test_tencent_limits_results_and_rejects_invalid_payload():
    text = _tencent("^".join(f"sh~{600000 + n}~股票~gp~GP-A" for n in range(20)))
    assert len(parse_tencent(text.decode("ascii"))) == MAX_RESULTS
    assert parse_tencent('v_hint=""') == []
    with pytest.raises(ValueError):
        parse_tencent('<html>access denied</html>')


@pytest.mark.parametrize("query", ["兆易创新", "zhaoyichuangxin", "zycx", "603986"])
def test_tencent_search_supports_each_input_form(query):
    session = _RoutingSession({TENCENT_ENDPOINT: _tencent("sh~603986~兆易创新~zycx~GP-A")})
    result = StockSearch(session=session).query(query)
    assert result == {"results": [{"code": "603986", "name": "兆易创新", "market": "sh"}], "error": None}
    assert session.calls[0][1]["params"]["q"] == query


@pytest.mark.parametrize("failure", [requests.Timeout(), b'<html>blocked</html>', b'v_hint=""'])
def test_search_falls_back_and_remembers_working_source(failure):
    session = _RoutingSession({
        TENCENT_ENDPOINT: failure,
        ENDPOINT: _payload([{"Code": "600519", "Name": "贵州茅台", "MktNum": "1"}]),
    })
    search = StockSearch(session=session)
    assert search.query("gzmt")["error"] is None
    assert [url for url, _ in session.calls] == [TENCENT_ENDPOINT, ENDPOINT]
    session.calls.clear()
    assert search.query("茅台")["results"][0]["code"] == "600519"
    assert [url for url, _ in session.calls] == [ENDPOINT]


def test_full_pinyin_still_tries_tencent_after_eastmoney_returns_empty():
    session = _RoutingSession({
        TENCENT_ENDPOINT: requests.Timeout(),
        ENDPOINT: _payload([{"Code": "603986", "Name": "兆易创新", "MktNum": "1"}]),
    })
    search = StockSearch(session=session)
    search.query("zycx")
    session.responses[ENDPOINT] = _payload([])
    session.responses[TENCENT_ENDPOINT] = _tencent("sh~603986~兆易创新~zycx~GP-A")
    assert search.query("zhaoyichuangxin")["results"][0]["name"] == "兆易创新"


def test_empty_search_is_not_an_outage_and_code_can_still_be_added():
    session = _RoutingSession({TENCENT_ENDPOINT: b'v_hint=""', ENDPOINT: _payload([])})
    search = StockSearch(session=session)
    assert search.query("不存在的名称") == {"results": [], "error": None}
    assert search.query("600519")["results"][0]["code"] == "600519"


def test_both_malformed_sources_report_failure():
    session = _RoutingSession({TENCENT_ENDPOINT: b'<html>blocked</html>', ENDPOINT: {}})
    assert StockSearch(session=session).query("茅台")["error"]


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
        self.content = payload if isinstance(payload, bytes) else b""

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


class _RoutingSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return _FakeResponse(response)
