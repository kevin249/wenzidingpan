from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from stockwidget import mcp_bs


TZ = ZoneInfo("Asia/Shanghai")


def setup_function():
    mcp_bs.reset_for_tests()


def test_base_and_l2_markers_remain_distinct_on_chart():
    mcp_bs.record_volatility_bs({'available': True, 'code': '600000', 'trade_date': '2026-09-14',
        'markers': [dict(time='10:00', signal='B1', l2_confirmed=False, price=100),
                    dict(time='10:01', signal='B', l2_confirmed=True, price=100),
                    dict(time='10:02', signal='S1', l2_confirmed=False, price=100),
                    dict(time='10:03', signal='S', l2_confirmed=True, price=100)]})
    assert mcp_bs.bs_points('600000', [100.] * 242, datetime(2026,9,14,14,tzinfo=TZ)) == [
        (30,'B1'), (31,'B'), (32,'S1'), (33,'S')]


def test_source_is_persisted(monkeypatch, tmp_path):
    path = tmp_path / "source.json"
    monkeypatch.setattr(mcp_bs, "_source_path", lambda: path)

    assert mcp_bs.get_source() == "local"
    assert mcp_bs.set_source("mcp") == "mcp"
    mcp_bs.reset_for_tests()
    assert mcp_bs.get_source() == "mcp"


@pytest.mark.parametrize(
    ("event_type", "category"),
    [
        ("trading.order_filled", "交易提醒"),
        ("screen.v2.done", "选股 / 筛选"),
        ("market.dark_trade_turning", "行情提醒"),
        ("ai.review.done", "AI 分析"),
        ("leader.realtime", "龙头 / 复盘"),
        ("backtest.done", "回测 / 策略任务"),
        ("research.update_matched", "研究资讯"),
        ("sentiment.state_change", "市场情绪"),
        ("system.test", "系统"),
    ],
)
def test_updated_gupiao_event_families_have_friendly_categories(event_type, category):
    assert mcp_bs._category(event_type) == category


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



def test_active_volatility_snapshot_supplies_all_today_bs_markers():
    prices = [100.0] * 242
    mcp_bs.record_volatility_bs(
        {
            "available": True,
            "code": "600000",
            "trade_date": "2026-09-14",
            "markers": [
                {
                    "time": "09:45",
                    "type": "buy_first",
                    "label": "B2 候选确认",
                    "price": 100.0,
                },
                {
                    "time": "10:31",
                    "type": "sell_first",
                    "label": "S3 结构确认",
                    "price": 100.0,
                },
                {
                    "time": "13:10",
                    "type": "buy_first",
                    "label": "B4 强反转",
                    "price": 100.0,
                },
            ],
        }
    )

    assert mcp_bs.bs_points(
        "600000",
        prices,
        now=datetime(2026, 9, 14, 14, 0, tzinfo=TZ),
    ) == [(15, "B"), (61, "S"), (131, "B")]


def test_active_snapshot_is_authoritative_over_partial_notification_cache():
    prices = [100.0] * 242
    notification = SimpleNamespace(
        event_id="old-notification-only",
        event_type="trading.holding_t_signal",
        title="测试 600000 出现 S4",
        body="",
        priority="normal",
        created_at="2026-09-14T10:00:01+08:00",
        link="",
        payload={
            "code": "600000",
            "direction": "sell_first",
            "bar_at": "2026-09-14T10:00:00+08:00",
            "price": 100.0,
        },
    )
    mcp_bs.record_notification(notification)
    mcp_bs.record_volatility_bs(
        {
            "available": True,
            "code": "600000",
            "trade_date": "2026-09-14",
            "markers": [
                {"time": "09:45", "type": "buy_first", "price": 100.0},
            ],
        }
    )

    # 主动 get_volatility_bs 是完整日内快照；通知里独有的 10:00 S 不应混回来。
    assert mcp_bs.bs_points(
        "600000",
        prices,
        now=datetime(2026, 9, 14, 11, 0, tzinfo=TZ),
    ) == [(15, "B")]
