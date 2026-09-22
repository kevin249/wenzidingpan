from __future__ import annotations

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
