from __future__ import annotations

import asyncio
from types import SimpleNamespace

import stockwidget.mcp_depth as mcp_depth
from stockwidget.mcp_depth import MIN_THOUSAND_LEVELS, parse_depth_payload


def _levels(count: int):
    rows = []
    for index in range(count):
        side = "bid" if index % 2 == 0 else "ask"
        rows.append(
            {
                "side": side,
                "level": index + 1,
                "price": 100 + index * 0.01,
                "volume": 10 + index,
            }
        )
    return rows


def test_parse_verified_thousand_depth():
    snapshot = parse_depth_payload(
        "sh600000",
        {
            "available": True,
            "complete": True,
            "full_depth_verified": True,
            "depth_limit_reached": False,
            "symbol": "600000",
            "fetched_at": "2026-09-22T10:00:00+08:00",
            "levels": _levels(20),
        },
        received_at=123.0,
    )

    assert snapshot.symbol == "600000"
    assert snapshot.available is True
    assert snapshot.full_depth is True
    assert snapshot.received_at == 123.0
    assert snapshot.bid_count == 10
    assert snapshot.ask_count == 10
    assert len(snapshot.levels) == 20


def test_ten_levels_are_not_mislabeled_as_thousand_depth():
    snapshot = parse_depth_payload(
        "600000",
        {"available": True, "complete": True, "levels": _levels(MIN_THOUSAND_LEVELS - 1)},
        received_at=123.0,
    )
    assert snapshot.available is False
    assert snapshot.full_depth is False


def test_nested_thousand_levels_are_supported():
    snapshot = parse_depth_payload(
        "600000",
        {
            "available": True,
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
    assert snapshot.bid_count == 6
    assert snapshot.ask_count == 6



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
