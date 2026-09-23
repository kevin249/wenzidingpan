from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import stockwidget.mcp_depth as mcp_depth
from stockwidget.mcp_depth import (
    DEPTH_FIVE,
    DEPTH_FULL,
    DEPTH_TEN,
    parse_depth_payload,
)


def _levels(per_side: int):
    rows = []
    for index in range(per_side):
        rows.append(
            {
                "side": "bid",
                "level": index + 1,
                "price": 100 - index * 0.01,
                "volume": 10 + index,
            }
        )
        rows.append(
            {
                "side": "ask",
                "level": index + 1,
                "price": 100.01 + index * 0.01,
                "volume": 20 + index,
            }
        )
    return rows


def _result(payload):
    return SimpleNamespace(is_error=False, structured_content=payload)


def test_parse_verified_thousand_depth():
    snapshot = parse_depth_payload(
        "sh600000",
        {
            "available": True,
            "complete": True,
            "full_depth_verified": True,
            "depth_limit_reached": False,
            "requested_depth": 1000,
            "symbol": "600000",
            "fetched_at": "2026-09-22T10:00:00+08:00",
            "levels": _levels(20),
        },
        received_at=123.0,
    )

    assert snapshot.symbol == "600000"
    assert snapshot.available is True
    assert snapshot.full_depth is True
    assert snapshot.depth_mode == DEPTH_FULL
    assert snapshot.requested_depth == 1000
    assert snapshot.received_at == 123.0
    assert snapshot.bid_count == 20
    assert snapshot.ask_count == 20


def test_parse_explicit_ten_level_is_available_but_not_thousand():
    snapshot = parse_depth_payload(
        "600000",
        {
            "available": True,
            "complete": True,
            "requested_depth": 10,
            "book_kind": "ten_level",
            "levels": _levels(10),
        },
        received_at=123.0,
    )
    assert snapshot.available is True
    assert snapshot.full_depth is False
    assert snapshot.depth_mode == DEPTH_TEN
    assert snapshot.bid_count == 10
    assert snapshot.ask_count == 10


def test_parse_explicit_five_level_is_available():
    snapshot = parse_depth_payload(
        "600000",
        {
            "available": True,
            "complete": True,
            "requested_depth": 5,
            "book_kind": "five_level",
            "levels": _levels(5),
        },
        received_at=123.0,
    )
    assert snapshot.available is True
    assert snapshot.full_depth is False
    assert snapshot.depth_mode == DEPTH_FIVE
    assert snapshot.bid_count == 5
    assert snapshot.ask_count == 5


def test_nested_verified_thousand_levels_are_supported():
    snapshot = parse_depth_payload(
        "600000",
        {
            "available": True,
            "requested_depth": 1000,
            "full_depth_verified": True,
            "depth_limit_reached": False,
            "thousand": {
                "levels": {
                    "bid": [{"price": 100 - i * 0.01, "volume": 10 + i} for i in range(6)],
                    "ask": [{"price": 100.01 + i * 0.01, "volume": 20 + i} for i in range(6)],
                }
            },
        },
        received_at=123.0,
    )
    assert snapshot.available is True
    assert snapshot.full_depth is True
    assert snapshot.depth_mode == DEPTH_FULL
    assert snapshot.bid_count == 6
    assert snapshot.ask_count == 6


def test_fallback_stops_at_ten_when_thousand_is_unavailable():
    calls = []

    class FakeSession:
        async def call_tool(self, name, arguments=None):
            calls.append((name, dict(arguments or {})))
            depth = arguments["requested_depth"]
            if depth == 1000:
                return _result(
                    {
                        "available": False,
                        "requested_depth": 1000,
                        "levels": [],
                    }
                )
            if depth == 10:
                return _result(
                    {
                        "available": True,
                        "requested_depth": 10,
                        "book_kind": "ten_level",
                        "levels": _levels(10),
                    }
                )
            raise AssertionError("五档不应再请求")

    snapshot = asyncio.run(
        mcp_depth._fetch_depth_with_fallback(FakeSession(), "600000")
    )

    assert snapshot.depth_mode == DEPTH_TEN
    assert [item[1]["requested_depth"] for item in calls] == [1000, 10]


def test_fallback_reaches_five_when_ten_is_unavailable():
    calls = []

    class FakeSession:
        async def call_tool(self, name, arguments=None):
            calls.append((name, dict(arguments or {})))
            depth = arguments["requested_depth"]
            if depth in {1000, 10}:
                return _result(
                    {
                        "available": False,
                        "requested_depth": depth,
                        "levels": [],
                    }
                )
            return _result(
                {
                    "available": True,
                    "requested_depth": 5,
                    "book_kind": "five_level",
                    "levels": _levels(5),
                }
            )

    snapshot = asyncio.run(
        mcp_depth._fetch_depth_with_fallback(FakeSession(), "600000")
    )

    assert snapshot.depth_mode == DEPTH_FIVE
    assert [item[1]["requested_depth"] for item in calls] == [1000, 10, 5]


def test_full_thousand_never_requests_fallback():
    calls = []

    class FakeSession:
        async def call_tool(self, name, arguments=None):
            calls.append((name, dict(arguments or {})))
            return _result(
                {
                    "available": True,
                    "requested_depth": 1000,
                    "full_depth_verified": True,
                    "depth_limit_reached": False,
                    "levels": _levels(20),
                }
            )

    snapshot = asyncio.run(
        mcp_depth._fetch_depth_with_fallback(FakeSession(), "600000")
    )

    assert snapshot.depth_mode == DEPTH_FULL
    assert [item[1]["requested_depth"] for item in calls] == [1000]


def test_poll_interval_is_five_seconds_until_thousand_recovers():
    assert mcp_depth._poll_seconds(False) == 5.0
    assert mcp_depth._poll_seconds(True) == 60.0
    assert "串行" in mcp_depth._status_text(False)
    assert "5s" in mcp_depth._status_text(False)
    assert "串行" in mcp_depth._status_text(True)
    assert "60s" in mcp_depth._status_text(True)


def test_active_bs_fetch_requests_all_today_markers(monkeypatch):
    calls = []
    recorded = []

    class FakeSession:
        async def call_tool(self, name, arguments=None):
            calls.append((name, arguments))
            return SimpleNamespace(
                is_error=False,
                structured_content={
                    "available": True,
                    "code": "600000",
                    "trade_date": "2026-09-22",
                    "markers": [{"time": "10:00", "type": "buy_first"}],
                },
            )

    monkeypatch.setattr(mcp_depth, "record_volatility_bs", recorded.append)
    payload = asyncio.run(mcp_depth._fetch_volatility_bs(FakeSession(), "600000"))

    assert calls == [
        (
            "get_volatility_bs",
            {"symbol": "600000", "trade_date": "", "limit": 1000},
        )
    ]
    assert payload["markers"][0]["type"] == "buy_first"
    assert recorded == [payload]



def test_multi_symbol_depth_gap_is_enforced_after_previous_request():
    gap = mcp_depth.DEPTH_INTER_SYMBOL_GAP_SECONDS
    assert gap == 0.75
    assert mcp_depth._serial_depth_delay(0.0, now=100.0) == 0.0
    assert mcp_depth._serial_depth_delay(100.0, now=100.2) == pytest.approx(gap - 0.2)
    assert mcp_depth._serial_depth_delay(100.0, now=101.0) == 0.0



def test_serial_scheduler_wakes_for_earliest_symbol_due():
    poller = mcp_depth.McpDepthPoller(
        mcp_depth.Config(symbols=["600000", "000001"])
    )
    poller._last_bs_fetch_at = 100.0
    poller._next_depth_due = {"600000": 105.0, "000001": 112.0}

    assert poller._next_wake_seconds(poller._config, now=101.0) == pytest.approx(4.0)


def test_serial_scheduler_does_not_double_wait_after_long_batch():
    poller = mcp_depth.McpDepthPoller(
        mcp_depth.Config(symbols=["600000", "000001"])
    )
    poller._last_bs_fetch_at = 100.0
    # 模拟多股串行完成后：最早股票 60 秒到期，后面股票更晚。
    poller._next_depth_due = {"600000": 161.0, "000001": 164.0}

    assert poller._next_wake_seconds(poller._config, now=160.0) == pytest.approx(1.0)


def test_depth_state_keeps_cached_thousand_until_full_depth_recovers():
    full = parse_depth_payload(
        "600000",
        {
            "available": True,
            "requested_depth": 1000,
            "full_depth_verified": True,
            "depth_limit_reached": False,
            "levels": _levels(20),
        },
        received_at=100.0,
    )
    ten = parse_depth_payload(
        "600000",
        {
            "available": True,
            "requested_depth": 10,
            "book_kind": "ten_level",
            "levels": _levels(10),
        },
        received_at=105.0,
    )

    display, cache, failures = mcp_depth._merge_depth_state(full, None, 0)
    assert display.depth_mode == DEPTH_FULL
    assert display.using_cached_full_depth is False
    assert failures == 0
    assert cache == full

    for expected_failures in (1, 2, 3):
        display, cache, failures = mcp_depth._merge_depth_state(
            ten, cache, failures
        )
        assert display.depth_mode == DEPTH_FULL
        assert display.levels == full.levels
        assert display.received_at == full.received_at
        assert display.using_cached_full_depth is True
        assert display.latest_depth_mode == DEPTH_TEN
        assert display.full_depth_failures == expected_failures
        assert failures == expected_failures

    recovered = parse_depth_payload(
        "600000",
        {
            "available": True,
            "requested_depth": 1000,
            "full_depth_verified": True,
            "depth_limit_reached": False,
            "levels": _levels(22),
        },
        received_at=120.0,
    )
    display, cache, failures = mcp_depth._merge_depth_state(
        recovered, cache, failures
    )
    assert display == cache
    assert display.received_at == 120.0
    assert display.full_depth_failures == 0
    assert display.using_cached_full_depth is False
    assert display.latest_depth_mode == DEPTH_FULL
    assert failures == 0


def test_depth_state_uses_fallback_only_before_any_thousand_cache_exists():
    ten = parse_depth_payload(
        "600000",
        {
            "available": True,
            "requested_depth": 10,
            "book_kind": "ten_level",
            "levels": _levels(10),
        },
        received_at=105.0,
    )

    display, cache, failures = mcp_depth._merge_depth_state(ten, None, 0)

    assert cache is None
    assert failures == 1
    assert display.depth_mode == DEPTH_TEN
    assert display.using_cached_full_depth is False
    assert display.full_depth_failures == 1
