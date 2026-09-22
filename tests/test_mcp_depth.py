from __future__ import annotations

import asyncio
from types import SimpleNamespace

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
    assert "5s" in mcp_depth._status_text(False)
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
