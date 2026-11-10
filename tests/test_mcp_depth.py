from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import stockwidget.mcp_depth as mcp_depth
from stockwidget.config import Config
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


def test_distribution_without_cross_protocol_audit_does_not_fallback():
    calls = []

    class FakeSession:
        async def call_tool(self, name, arguments=None):
            calls.append(arguments['requested_depth'])
            return _result({
                'available': True, 'verified': True, 'service_ready': True,
                'distribution_ready': True, 'method': 'active_protocol_0559',
                'full_depth_verified': False, 'complete': False,
                'depth_limit_reached': True, 'requested_depth': 1000,
                'quantity_audit': None, 'quantity_comparison': 'disabled',
                'levels': _levels(20),
            })

    cached, failures = None, 2
    for _ in range(3):
        snapshot = asyncio.run(mcp_depth._fetch_depth_with_fallback(FakeSession(), '603986'))
        display, cached, failures = mcp_depth._merge_depth_state(snapshot, cached, failures)
        assert display.available is True
        assert display.bid_count == display.ask_count == 20
        assert failures == 0
        assert display.using_cached_full_depth is False
    assert calls == [1000, 1000, 1000]


@pytest.mark.parametrize('flag', ['verified', 'service_ready'])
def test_invalid_distribution_is_not_displayed(flag):
    payload = {
        'available': True, 'verified': True, 'service_ready': True,
        'distribution_ready': True, 'method': 'active_protocol_0559',
        'requested_depth': 1000, 'levels': _levels(20),
    }
    payload[flag] = False
    assert parse_depth_payload('603986', payload).available is False


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
    assert mcp_depth._poll_seconds(True) == 5.0
    assert "串行" in mcp_depth._status_text(False)
    assert "5s" in mcp_depth._status_text(False)
    assert "串行" in mcp_depth._status_text(True)
    assert "5s" in mcp_depth._status_text(True)


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
            {"symbol": "600000", "trade_date": "", "limit": 1000,
             "include_candidates": False},
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
    # 模拟多股串行完成后：最早股票 60 秒到期，后面股票更晚。
    poller._next_depth_due = {"600000": 161.0, "000001": 164.0}

    # B/S 刚刷新过，本轮由最早到期的盘口决定唤醒时间。
    poller._last_bs_fetch_at = 160.0
    assert poller._next_wake_seconds(poller._config, now=160.0) == pytest.approx(1.0)

    # B/S 也已到期时，轮询器优先处理它。
    poller._last_bs_fetch_at = 100.0
    assert poller._next_wake_seconds(poller._config, now=160.0) == pytest.approx(0.1)


def test_fresh_ten_level_replaces_old_full_book_until_full_depth_recovers():
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
    assert cache == display
    assert cache.levels == full.levels
    assert cache.latest_depth_mode == DEPTH_FULL

    for expected_failures in (1, 2, 3):
        display, cache, failures = mcp_depth._merge_depth_state(
            ten, cache, failures
        )
        assert display.depth_mode == DEPTH_TEN
        assert display.levels == ten.levels
        assert display.received_at == ten.received_at
        assert display.using_cached_full_depth is False
        assert cache is None
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


def test_non_debug_polls_during_market_hours_and_explicit_refresh_bypasses_close():
    from datetime import datetime
    config = Config(debug_mode=False)
    market = datetime(2026, 11, 6, 10, 0)
    lunch = datetime(2026, 11, 6, 12, 0)
    assert mcp_depth.McpDepthPoller._should_poll(config, False, market) is True
    assert mcp_depth.McpDepthPoller._should_poll(config, False, lunch) is False
    assert mcp_depth.McpDepthPoller._should_poll(config, True, lunch) is True


def test_debug_mode_polls_depth_around_the_clock():
    config = Config(debug_mode=True)
    assert mcp_depth.McpDepthPoller._should_poll(config, explicit=False) is True


def test_depth_poller_starts_explicit_and_manual_refresh_rearms_it():
    poller = mcp_depth.McpDepthPoller(Config(debug_mode=False, display_theme="theme2"))
    assert poller._explicit is True
    poller._explicit = False
    poller.refresh_now()
    assert poller._explicit is True


def test_bs_synchronization_is_independent_of_passive_depth():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    poller = mcp_depth.McpDepthPoller(Config(debug_mode=False))
    config = poller._config
    tz = ZoneInfo("Asia/Shanghai")
    market = datetime(2026, 10, 9, 10, 0, tzinfo=tz)
    closed = datetime(2026, 10, 11, 10, 0, tzinfo=tz)

    assert poller._should_poll(config, explicit=False, now=closed) is False
    assert poller._should_fetch_bs(config, False, 59.0, market) is False
    assert poller._should_fetch_bs(config, False, 60.0, market) is True
    assert poller._should_fetch_bs(config, False, 600.0, closed) is False
    assert poller._should_fetch_bs(config, True, 0.0, closed) is True
    assert poller._should_fetch_bs(Config(debug_mode=True), False, 60.0, closed) is True


def test_total_failure_keeps_full_book_with_original_timestamp_and_stale_flag():
    full = parse_depth_payload('600000', {
        'available': True, 'full_depth_verified': True, 'levels': _levels(20),
    }, received_at=100.0)
    display, cache, failures = mcp_depth._merge_depth_state(
        mcp_depth.DepthSnapshot('600000', received_at=120.0), full, 0,
    )
    assert display.levels == full.levels
    assert display.received_at == 100.0
    assert display.using_cached_full_depth
    assert failures == 1


def test_normal_worker_keeps_polling_after_startup_without_debug(monkeypatch):
    import time
    monkeypatch.setattr(mcp_depth, 'active_updates_allowed', lambda *_: True)
    monkeypatch.setattr(mcp_depth, 'is_a_share_active_time', lambda *_: True)
    monkeypatch.setattr(mcp_depth, 'DEPTH_POLL_SECONDS', .01)
    worker = mcp_depth.McpDepthPoller(Config(
        symbols=['600000'], mcp_api_key='test', debug_mode=False,
    ))
    calls = []

    async def cycle(config, **kwargs):
        calls.append(kwargs)
        worker._next_depth_due['600000'] = time.monotonic() + .01
        if len(calls) == 3:
            worker.stop()
        return True

    monkeypatch.setattr(worker, '_fetch_cycle', cycle)
    worker.start()
    try:
        assert worker.wait(1500), 'depth stopped after the startup snapshot'
    finally:
        worker.stop()
        assert worker.wait(3000)
    assert [call['fetch_depth'] for call in calls] == [True, True, True]
    assert [call['force_depth'] for call in calls] == [True, False, False]
    assert [call['fetch_bs'] for call in calls] == [True, False, False]


def test_bs_push_during_request_is_coalesced_and_does_not_force_depth(monkeypatch):
    monkeypatch.setattr(mcp_depth, 'active_updates_allowed', lambda *_: False)
    monkeypatch.setattr(mcp_depth, 'is_a_share_active_time', lambda *_: False)
    worker = mcp_depth.McpDepthPoller(Config(symbols=['600000'], mcp_api_key='test'))
    calls = []

    async def cycle(config, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            for _ in range(20):
                worker.refresh_bs_now()
            assert not worker._changed.is_set()
        else:
            worker.stop()
        return True

    monkeypatch.setattr(worker, '_fetch_cycle', cycle)
    worker.start()
    try:
        assert worker.wait(1500), 'pending B/S notification was lost after the request'
    finally:
        worker.stop()
        assert worker.wait(3000)
    assert len(calls) == 2
    assert calls[1] == dict(fetch_bs=True, fetch_depth=False, force_depth=False)


def test_bs_failure_retries_without_waiting_one_minute_even_after_close(monkeypatch):
    import time
    monkeypatch.setattr(mcp_depth, 'active_updates_allowed', lambda *_: False)
    monkeypatch.setattr(mcp_depth, 'is_a_share_active_time', lambda *_: False)
    monkeypatch.setattr(mcp_depth, 'BS_RETRY_SECONDS', .03)
    worker = mcp_depth.McpDepthPoller(Config(symbols=['600000'], mcp_api_key='test'))
    calls = []
    started_at = []

    async def cycle(config, **kwargs):
        calls.append(kwargs)
        started_at.append(time.monotonic())
        worker._bs_sync_ok = len(calls) > 1
        if len(calls) == 2:
            worker.stop()
        return True

    monkeypatch.setattr(worker, '_fetch_cycle', cycle)
    worker.start()
    try:
        assert worker.wait(1500), 'failed B/S synchronization did not retry'
    finally:
        worker.stop()
        assert worker.wait(3000)
    assert len(calls) == 2
    assert started_at[1] - started_at[0] >= .025
    assert calls[1]['fetch_bs'] is True
    assert calls[1]['fetch_depth'] is False


def test_bs_tool_uses_its_own_longer_deadline(monkeypatch):
    from stockwidget import mcp_notifications
    monkeypatch.setattr(mcp_notifications, 'REQUEST_TIMEOUT_SECONDS', .01)
    monkeypatch.setattr(mcp_depth, 'BS_REQUEST_TIMEOUT_SECONDS', .1)
    received = []
    monkeypatch.setattr(mcp_depth, 'record_volatility_bs', received.append)

    class SlowSession:
        async def call_tool(self, name, arguments):
            assert name == 'get_volatility_bs'
            assert arguments['include_candidates'] is False
            await asyncio.sleep(.03)
            return _result({'symbol': '603986', 'markers': []})

    payload = asyncio.run(mcp_depth._fetch_volatility_bs(SlowSession(), '603986'))
    assert payload == {'symbol': '603986', 'markers': []}
    assert received == [payload]


def test_bs_timeout_diagnostic_is_deduplicated_until_recovery(monkeypatch):
    from contextlib import asynccontextmanager
    from PySide6.QtCore import Qt

    worker = mcp_depth.McpBsPoller(Config(symbols=['603986'], mcp_api_key='test'))
    errors, deadlines = [], []
    worker.status_changed.connect(errors.append, Qt.DirectConnection)

    @asynccontextmanager
    async def client(**kwargs):
        yield object()

    @asynccontextmanager
    async def transport(*args, **kwargs):
        yield None, None

    class Session:
        def __init__(self, *args, **kwargs):
            deadlines.append(kwargs['read_timeout_seconds'])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    attempts = iter((False, False, True, False))

    async def fetch(*args):
        if not next(attempts):
            raise TimeoutError()

    async def negotiate(*args):
        return True

    monkeypatch.setattr(mcp_depth.httpx2, 'AsyncClient', client)
    monkeypatch.setattr(mcp_depth, 'streamable_http_client', transport)
    monkeypatch.setattr(mcp_depth, 'ClientSession', Session)
    monkeypatch.setattr(mcp_depth, 'negotiate_protocol', negotiate)
    monkeypatch.setattr(mcp_depth, '_fetch_volatility_bs', fetch)
    for _ in range(4):
        worker._bs_sync_ok = True
        asyncio.run(worker._fetch_cycle(worker._config, fetch_bs=True, fetch_depth=False))

    assert len(errors) == 2
    assert all('TimeoutError: get_volatility_bs 超过 45 秒' in error for error in errors)
    assert deadlines == [mcp_depth.BS_REQUEST_TIMEOUT_SECONDS] * 4


def test_mcp_bs_sync_stays_enabled_without_a_visible_chart():
    config = Config(mcp_api_key='test', show_sparkline=False, intraday_chart=False)
    assert mcp_depth.McpDepthPoller._enabled(config)
    assert not mcp_depth.McpDepthPoller._depth_enabled(config)


def test_push_during_real_fetch_loop_does_not_drop_remaining_symbols(monkeypatch):
    from contextlib import asynccontextmanager
    from PySide6.QtCore import Qt
    worker = mcp_depth.McpDepthPoller(Config(
        symbols=['600000', '000001'], mcp_api_key='test',
    ))
    fetched, delivered = [], []
    worker.depth_ready.connect(delivered.append, Qt.DirectConnection)

    @asynccontextmanager
    async def client(**kwargs):
        yield object()

    @asynccontextmanager
    async def transport(*args, **kwargs):
        yield None, None

    class Session:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def discover(self):
            pass
        async def call_tool(self, name, arguments):
            fetched.append(arguments['symbol'])
            if len(fetched) == 1:
                worker.refresh_bs_now()
            return _result({'available': True, 'full_depth_verified': True,
                            'levels': _levels(20)})

    monkeypatch.setattr(mcp_depth.httpx2, 'AsyncClient', client)
    monkeypatch.setattr(mcp_depth, 'streamable_http_client', transport)
    monkeypatch.setattr(mcp_depth, 'ClientSession', Session)
    monkeypatch.setattr(mcp_depth, 'DEPTH_INTER_SYMBOL_GAP_SECONDS', 0)
    assert asyncio.run(worker._fetch_cycle(worker._config, fetch_bs=False))
    assert fetched == ['600000', '000001']
    assert [snapshot.symbol for snapshot in delivered] == fetched
    assert worker._bs_explicit


def test_stale_full_book_falls_back_even_when_response_was_just_received(monkeypatch):
    from datetime import datetime
    now = datetime.fromisoformat('2026-11-09T10:35:00+08:00').timestamp()
    monkeypatch.setattr(mcp_depth.time, 'time', lambda: now)
    calls = []
    class Session:
        async def call_tool(self, name, arguments):
            requested = arguments['requested_depth']
            calls.append(requested)
            return _result({
                'available': True, 'full_depth_verified': requested == 1000,
                'source_at': ('2026-11-09T10:00:00+08:00' if requested == 1000
                              else '2026-11-09T02:34:58Z'),
                'fetched_at': '2026-11-09T10:35:00+08:00',
                'levels': _levels(20 if requested == 1000 else 10),
            })
    snapshot = asyncio.run(mcp_depth._fetch_depth_with_fallback(Session(), '603986'))
    assert calls == [1000, 10]
    assert snapshot.available and snapshot.depth_mode == DEPTH_TEN
    assert '1000:source_stale' in snapshot.reason
    assert mcp_depth.depth_time_label(snapshot) == '采样 10:34:58'
    assert snapshot.received_at == now


def test_expired_full_cache_cannot_keep_overriding_total_failure():
    full = parse_depth_payload('603986', {
        'available': True, 'full_depth_verified': True, 'levels': _levels(20),
    }, received_at=100.0)
    failed = mcp_depth.DepthSnapshot('603986', received_at=131.0, reason='timeout')
    display, cache, failures = mcp_depth._merge_depth_state(failed, full, 2)
    assert not display.available and not display.levels
    assert cache is None and failures == 3
    assert display.reason == 'timeout'


def test_all_stale_responses_are_unavailable_and_preserve_diagnostics(monkeypatch):
    from datetime import datetime
    now = datetime.fromisoformat('2026-11-09T10:35:00+08:00').timestamp()
    monkeypatch.setattr(mcp_depth.time, 'time', lambda: now)
    class Session:
        async def call_tool(self, name, arguments):
            return _result({'available': True, 'full_depth_verified': True,
                            'source_at': '2026-11-06T15:00:00+08:00', 'levels': _levels(20)})
    snapshot = asyncio.run(mcp_depth._fetch_depth_with_fallback(Session(), '603986'))
    assert not snapshot.available and not snapshot.full_depth
    assert all(f'{depth}:source_stale' in snapshot.reason for depth in (1000, 10, 5))


def test_closed_market_can_display_last_session_without_claiming_new_sample():
    from datetime import datetime
    source = '2026-11-09T15:00:00+08:00'
    now = datetime.fromisoformat('2026-11-09T18:00:00+08:00').timestamp()
    snapshot = parse_depth_payload('603986', {
        'available': True, 'full_depth_verified': True, 'levels': _levels(20),
        'source_at': source, 'fetched_at': '2026-11-09T18:00:00+08:00',
    }, received_at=now)
    assert not mcp_depth.depth_is_stale(snapshot, now)
    assert mcp_depth.depth_time_label(snapshot) == '采样 15:00:00'
