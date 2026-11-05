"""K 线模块：三源解析、周期归一化、优先级降级、缓存与配置项。

覆盖的核心约束是「K 线只走东财/腾讯/新浪三个公开接口，不经过 MCP」，
所以这里既要验三家的字段口径，也要验降级链与固定单源两种模式的区别。
"""

from __future__ import annotations

import pytest
import requests

from stockwidget.config import Config, sanitize
from stockwidget.kline import (
    ADJUST_NONE,
    ADJUST_QFQ,
    EASTMONEY,
    PERIOD_DAY,
    PERIOD_MONTH,
    SOURCE_AUTO,
    SINA,
    TENCENT,
    KlineClient,
    configured_source,
    normalize_limit,
    normalize_period,
    normalize_source,
    parse_eastmoney,
    parse_sina,
    parse_tencent_day,
    parse_tencent_minute,
    reset_cache,
)

# --------------------------------------------------------------- 东财


def _em(klines: list[str], name: str = "贵州茅台") -> dict:
    return {"data": {"code": "600519", "name": name, "klines": klines}}


def test_eastmoney_field_order_is_open_close_high_low():
    bars = parse_eastmoney(
        _em(["2026-09-22,1252.15,1253.80,1265.88,1248.10,24573,3084000000.00,0.13,1.65"]),
        limit=10,
    )
    assert len(bars) == 1
    bar = bars[0]
    assert (bar.time, bar.open, bar.close, bar.high, bar.low) == (
        "2026-09-22",
        1252.15,
        1253.80,
        1265.88,
        1248.10,
    )
    assert bar.volume == 24573.0  # 手
    assert bar.amount == 3084000000.0


def test_eastmoney_minute_time_keeps_clock():
    bars = parse_eastmoney(_em(["2026-08-12 09:35,1346.50,1348.88,1349.95,1332.51,2888"]), limit=10)
    assert bars[0].time == "2026-08-12 09:35"


def test_bars_are_sorted_deduped_and_trimmed_to_limit():
    # 接口在 lmt 上并不可靠，会整段返回；这里验客户端自己按时间排序并只留末尾 limit 根。
    bars = parse_eastmoney(
        _em([
            "2026-09-22,1,2,3,0.5,10",
            "2026-09-18,1,2,3,0.5,10",
            "2026-09-23,1,2,3,0.5,10",
            "2026-09-22,1,9,9,0.5,10",  # 同一时刻重复，取最后一根
        ]),
        limit=2,
    )
    assert [bar.time for bar in bars] == ["2026-09-22", "2026-09-23"]
    assert bars[0].close == 9.0


def test_rows_without_price_are_dropped():
    bars = parse_eastmoney(
        _em(["2026-09-23,1,2,3,0.5,10", "坏数据", "2026-09-24,-,-,-,-,-"]),
        limit=10,
    )
    assert [bar.time for bar in bars] == ["2026-09-23"]


def test_eastmoney_rejects_bad_payload():
    assert parse_eastmoney(None, limit=10) == []
    assert parse_eastmoney({"data": None}, limit=10) == []


# --------------------------------------------------------------- 腾讯


def test_tencent_daily_uses_qfq_prefix_and_lot_volume():
    payload = {"data": {"sh600519": {"qfqday": [
        ["2026-09-22", "1252.150", "1253.800", "1265.880", "1248.100", "24573.000"],
    ], "qt": {"sh600519": ["1", "贵州茅台", "600519"]}}}}
    bars = parse_tencent_day(payload, "sh600519", "day", limit=10)
    assert bars[0].time == "2026-09-22"
    assert (bars[0].open, bars[0].close, bars[0].high, bars[0].low) == (
        1252.15, 1253.80, 1265.88, 1248.10
    )
    assert bars[0].volume == 24573.0


def test_tencent_minute_expands_compact_time():
    payload = {"data": {"sh600519": {"m5": [
        ["202609231450", "1251.43", "1251.06", "1251.60", "1251.00", "694.00", {}, "0.55"],
        ["202609231500", "1251.08", "1251.24", "1251.58", "1250.94", "743.00", {}, "0.59"],
    ]}}}
    bars = parse_tencent_minute(payload, "sh600519", "m5", limit=10)
    assert [bar.time for bar in bars] == ["2026-09-23 14:50", "2026-09-23 15:00"]
    assert bars[-1].volume == 743.0


def test_tencent_falls_back_to_first_quote_block_when_key_missing():
    payload = {"data": {"sz000001": {"day": [
        ["2026-09-23", "10.00", "10.10", "10.20", "9.90", "100"],
    ], "qt": {"sz000001": ["0", "平安银行", "000001"]}}}}
    assert parse_tencent_day(payload, "sz000001", "day", limit=10)[0].close == 10.10


# --------------------------------------------------------------- 新浪


def test_sina_repairs_unquoted_keys_and_converts_shares_to_lots():
    text = (
        '[{day:"2026-09-23",open:"1255.030",high:"1271.500",'
        'low:"1250.890",close:"1251.240",volume:"3098122"}]'
    )
    bars = parse_sina(text, limit=10)
    assert len(bars) == 1
    assert bars[0].time == "2026-09-23"
    assert bars[0].close == 1251.24
    assert bars[0].volume == pytest.approx(30981.22)  # 股 → 手


def test_sina_minute_drops_seconds():
    text = '[{day:"2026-09-23 14:50:00",open:"1",high:"2",low:"0.5",close:"1.5",volume:"100"}]'
    assert parse_sina(text, limit=10)[0].time == "2026-09-23 14:50"


def test_sina_rejects_empty_and_garbage():
    assert parse_sina("null", limit=10) == []
    assert parse_sina("[]", limit=10) == []
    assert parse_sina("<html>403</html>", limit=10) == []
    assert parse_sina(None, limit=10) == []


# --------------------------------------------------------------- 归一化


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1d", PERIOD_DAY),
        ("day", PERIOD_DAY),
        ("D", PERIOD_DAY),
        ("", PERIOD_DAY),
        (None, PERIOD_DAY),
        ("5m", "5m"),
        ("M5", "5m"),
        ("1m", "1m"),
        ("1M", PERIOD_MONTH),
        ("M", PERIOD_MONTH),
        ("monthly", PERIOD_MONTH),
        ("1h", "60m"),
        ("quarter", None),
    ],
)
def test_normalize_period(value, expected):
    assert normalize_period(value) == expected


def test_normalize_source_and_limit():
    assert normalize_source("SINA") == SINA
    assert normalize_source("随便") == SOURCE_AUTO
    assert normalize_source(None) == SOURCE_AUTO
    assert normalize_limit(None) == 120
    assert normalize_limit(0) == 1
    assert normalize_limit(99999) == 2000
    assert normalize_limit("240") == 240


# --------------------------------------------------------------- 客户端


def test_auto_falls_back_to_tencent_when_eastmoney_empty():
    session = _ScriptedSession([
        _JsonResponse(_em([], name="")),
        _JsonResponse({"data": {"sh600519": {"qfqday": [
            ["2026-09-23", "1", "2", "3", "0.5", "100"],
        ], "qt": {"sh600519": ["1", "贵州茅台", "600519"]}}}}),
    ])
    kline = KlineClient(source=SOURCE_AUTO, session=session).fetch("600519")
    assert kline.source == TENCENT
    assert kline.adjust == ADJUST_QFQ
    assert kline.name == "贵州茅台"
    assert len(kline.bars) == 1
    assert session.calls == 2


def test_auto_falls_through_to_sina_and_marks_unadjusted():
    session = _ScriptedSession([
        _JsonResponse(_em([])),
        _JsonResponse({"data": {}}),
        _TextResponse(
            '[{day:"2026-09-23",open:"1",high:"2",low:"0.5",close:"1.5",volume:"100"}]'
        ),
    ])
    kline = KlineClient(source=SOURCE_AUTO, session=session).fetch("600519")
    assert kline.source == SINA
    assert kline.adjust == ADJUST_NONE
    assert kline.bars[0].volume == pytest.approx(1.0)
    assert session.calls == 3


def test_fixed_source_never_falls_back():
    session = _ScriptedSession([_JsonResponse(_em([]))])
    kline = KlineClient(source=EASTMONEY, session=session).fetch("600519")
    assert kline.source == EASTMONEY
    assert kline.error == "无数据"
    assert session.calls == 1  # 固定单源时不再试腾讯/新浪


def test_sina_unsupported_period_is_reported_not_hidden():
    session = _ScriptedSession([_JsonResponse(_em(["2026-09-23,1,2,3,0.5,100"]))])
    kline = KlineClient(source=SINA, session=session).fetch("600519", period="1m")
    assert kline.source == SINA
    assert kline.error and "不支持" in kline.error
    assert session.calls == 0  # 新浪没有 1 分钟线，连请求都不该发


def test_first_source_reason_is_kept_when_all_sources_fail():
    # 全部失败时保留优先级最高那个源的原因，和 providers/auto.py 的口径一致。
    session = _ScriptedSession([
        _JsonResponse(_em([])),
        _JsonResponse({"data": {}}),
        _TextResponse("[]"),
    ])
    kline = KlineClient(source=SOURCE_AUTO, session=session).fetch("600519")
    assert kline.source == EASTMONEY
    assert kline.error == "无数据"
    assert session.calls == 3


def test_client_caches_within_ttl():
    session = _ScriptedSession(
        [_JsonResponse(_em(["2026-09-23,1,2,3,0.5,100"]))] * 4
    )
    client = KlineClient(source=EASTMONEY, session=session)
    client.fetch("600519", now=1000.0)
    client.fetch("600519", now=1030.0)
    assert session.calls == 1  # 日 K 的 TTL 是 60 秒
    client.fetch("600519", now=1061.0)
    assert session.calls == 2


def test_client_reports_error_instead_of_raising():
    kline = KlineClient(session=_FailingSession(requests.Timeout())).fetch("600519")
    assert kline.error == "请求超时"
    assert kline.bars == []
    assert bool(kline) is False


def test_client_rejects_invalid_symbol_without_network():
    session = _FailingSession(AssertionError("不该发起请求"))
    assert KlineClient(session=session).fetch("不是代码").error == "代码格式不正确"


def test_to_dict_is_json_ready():
    import json

    bars = parse_eastmoney(_em(["2026-09-23,1,2,3,0.5,100,1000.00"]), limit=10)
    kline = KlineClient(session=_ScriptedSession([_JsonResponse(_em(
        ["2026-09-23,1,2,3,0.5,100,1000.00"]
    ))]))
    payload = kline.fetch("600519").to_dict()
    payload["bars"] = [bar.to_dict() for bar in bars]
    assert json.loads(json.dumps(payload))["bars"][0]["close"] == 2.0
    assert payload["source_label"] == "东方财富"
    assert payload["period_label"] == "日 K"


# --------------------------------------------------------------- 配置项


def test_config_defaults_to_auto_and_validates_source():
    assert Config().kline_source == SOURCE_AUTO
    assert sanitize({"kline_source": "sina"}).kline_source == SINA
    assert sanitize({"kline_source": "tencent"}).kline_source == TENCENT
    assert sanitize({"kline_source": "不存在的源"}).kline_source == SOURCE_AUTO
    assert sanitize({"kline_source": 123}).kline_source == SOURCE_AUTO


def test_configured_source_reads_config_file(tmp_path, monkeypatch):
    import stockwidget.config as config_module
    import stockwidget.kline as kline_module

    path = tmp_path / "config.json"
    path.write_text('{"kline_source": "eastmoney"}', encoding="utf-8")
    monkeypatch.setattr(config_module, "config_path", lambda: path)
    kline_module.clear_source_memo()
    try:
        assert configured_source() == EASTMONEY
    finally:
        kline_module.clear_source_memo()
        reset_cache()


def test_configured_source_survives_broken_config(tmp_path, monkeypatch):
    import stockwidget.config as config_module
    import stockwidget.kline as kline_module

    def boom() -> None:
        raise OSError("读不了")

    monkeypatch.setattr(config_module, "config_path", boom)
    kline_module.clear_source_memo()
    try:
        assert configured_source() == SOURCE_AUTO
    finally:
        kline_module.clear_source_memo()


# --------------------------------------------------------------- 测试替身


class _JsonResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _TextResponse:
    def __init__(self, text):
        self.text = text
        self.encoding = "utf-8"

    def raise_for_status(self):
        return None

    def json(self):  # 新浪返回的是 JS 字面量，模块按 text 解析
        raise ValueError("新浪接口不是合法 JSON")


class _ScriptedSession:
    """按顺序吐出预设响应，用来验证降级链的调用次数。"""

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
