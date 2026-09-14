from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from stockwidget import mcp_bs


TZ = ZoneInfo("Asia/Shanghai")


def setup_function():
    mcp_bs.reset_for_tests()


def test_source_is_persisted(monkeypatch, tmp_path):
    path = tmp_path / "source.json"
    monkeypatch.setattr(mcp_bs, "_source_path", lambda: path)

    assert mcp_bs.get_source() == "local"
    assert mcp_bs.set_source("mcp") == "mcp"
    mcp_bs.reset_for_tests()
    assert mcp_bs.get_source() == "mcp"


def test_today_records_are_groupable_and_deduplicated():
    notification = SimpleNamespace(
        event_id="evt-1",
        event_type="trading.holding_t_signal",
        title="兆易创新 603986 出现 B4 方向确认",
        body="时间：2026-09-14 10:00\n信号：B4 方向确认",
        priority="high",
        created_at="2026-09-14T10:00:05+08:00",
        link="/intraday-volatility?stock=603986",
        payload={
            "code": "603986",
            "name": "兆易创新",
            "direction": "buy_first",
            "bar_at": "2026-09-14T10:00:00+08:00",
            "price": 200.0,
        },
    )
    mcp_bs.record_notification(notification)
    mcp_bs.record_notification(notification)

    rows = mcp_bs.today_records(datetime(2026, 9, 14, 11, 0, tzinfo=TZ))
    assert len(rows) == 1
    assert rows[0]["category"] == "B/S 波动点"
    assert rows[0]["stock_code"] == "603986"
    assert rows[0]["stock_name"] == "兆易创新"
    assert rows[0]["side"] == "B"


def test_bs_points_map_morning_and_afternoon_minutes():
    prices = [100.0] * 242
    morning = SimpleNamespace(
        event_id="m1",
        event_type="trading.holding_t_signal",
        title="测试 600000 出现 B4",
        body="",
        priority="normal",
        created_at="2026-09-14T10:00:01+08:00",
        link="",
        payload={
            "code": "600000",
            "direction": "buy_first",
            "bar_at": "2026-09-14T10:00:00+08:00",
            "price": 100.0,
        },
    )
    afternoon = SimpleNamespace(
        event_id="a1",
        event_type="trading.holding_t_signal",
        title="测试 600000 出现 S4",
        body="",
        priority="normal",
        created_at="2026-09-14T13:10:01+08:00",
        link="",
        payload={
            "code": "600000",
            "direction": "sell_first",
            "bar_at": "2026-09-14T13:10:00+08:00",
            "price": 100.0,
        },
    )
    mcp_bs.record_notification(morning)
    mcp_bs.record_notification(afternoon)

    points = mcp_bs.bs_points(
        "600000",
        prices,
        now=datetime(2026, 9, 14, 14, 0, tzinfo=TZ),
    )
    assert points == [(30, "B"), (131, "S")]
