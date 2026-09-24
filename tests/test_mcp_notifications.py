from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from stockwidget import mcp_notifications
from stockwidget.config import Config
from stockwidget.mcp_notifications import (
    REQUEST_TIMEOUT_SECONDS,
    SSE_TIMEOUT,
    McpNotification,
    McpNotificationListener,
    _bounded,
    _notifications,
    _tool_payload,
)


def test_tool_payload_prefers_structured_content():
    result = SimpleNamespace(
        is_error=False,
        structured_content={"resource_uri": "gupiao://notifications/alice"},
        content=[],
    )
    assert _tool_payload(result) == {"resource_uri": "gupiao://notifications/alice"}


def test_tool_payload_falls_back_to_json_text():
    block = SimpleNamespace(text='{"resource_uri": "gupiao://notifications/alice"}')
    result = SimpleNamespace(isError=False, structuredContent=None, content=[block])
    assert _tool_payload(result)["resource_uri"].endswith("/alice")


def test_notifications_ignore_invalid_rows_and_normalize_text():
    payload = {
        "notifications": [
            {"event_id": "event-12", "title": " 提醒 ", "body": " 正文 ", "event_type": "market.alert"},
            {"event_id": "", "title": "忽略"},
        ]
    }

    assert _notifications(payload) == [
        McpNotification(event_id="event-12", title="提醒", body="正文", event_type="market.alert")
    ]


CURRENT_GUPIAO_EVENT_TYPES = (
    "market.fund_flow_retreat",
    "leader.daily",
    "leader.manual",
    "leader.realtime",
    "market.risk_anomaly",
    "market.watchlist_alert",
    "market.dark_trade_turning",
    "market.live_news",
    "sentiment.state_change",
    "ai.prediction.done",
    "ai.review.done",
    "ai.review.failed",
    "ai.model_test.done",
    "ai.model_test.failed",
    "trading.pnl_threshold",
    "trading.position_risk",
    "trading.order_filled",
    "trading.holdings_ai.done",
    "trading.holdings_ai.partial",
    "trading.holdings_ai.failed",
    "trading.holding_t_signal",
    "trading.bank_index_alert",
    "backtest.done",
    "backtest.failed",
    "screen.v2.done",
    "screen.v2.failed",
    "screen.v2.market_update",
    "research.update_matched",
    "system.admin_alert",
    "system.test",
)


@pytest.mark.parametrize("event_type", CURRENT_GUPIAO_EVENT_TYPES)
def test_current_gupiao_event_envelope_is_preserved(event_type):
    payload = {
        "latest_sequence": 37,
        "notifications": [
            {
                "sequence": 37,
                "event_id": "event-37",
                "event_type": event_type,
                "title": "测试提醒",
                "body": "正文",
                "priority": "high",
                "created_at": "2026-09-21T08:30:00+08:00",
                "link": "/detail",
                "payload": {"code": "603986", "nested": {"ok": True}},
            }
        ],
    }

    assert _notifications(payload) == [
        McpNotification(
            event_id="event-37",
            title="测试提醒",
            body="正文",
            event_type=event_type,
            priority="high",
            created_at="2026-09-21T08:30:00+08:00",
            link="/detail",
            payload={"code": "603986", "nested": {"ok": True}},
            sequence=37,
        )
    ]


def _sequence_stub(last_sequence):
    return SimpleNamespace(_lock=threading.Lock(), _last_sequence=last_sequence)


def _sequence_notifications(first: int, last: int) -> list[McpNotification]:
    return [
        McpNotification(event_id=f"event-{sequence}", title="提醒", body="", sequence=sequence)
        for sequence in range(first, last + 1)
    ]


def test_sequence_tracking_accepts_a_complete_resource_window():
    stub = _sequence_stub(50)
    missing = McpNotificationListener._observe_sequence(
        stub,
        {"latest_sequence": 150},
        _sequence_notifications(51, 150),
    )
    assert missing == 0
    assert stub._last_sequence == 150


def test_sequence_tracking_reports_events_evicted_from_the_resource_window():
    stub = _sequence_stub(50)
    # gupiao_ztfx 默认资源窗口只保留最近 100 条；151 到来后，51 已经被挤掉。
    missing = McpNotificationListener._observe_sequence(
        stub,
        {"latest_sequence": 151},
        _sequence_notifications(52, 151),
    )
    assert missing == 1
    assert stub._last_sequence == 151


def test_sequence_tracking_rebaselines_after_gateway_restart():
    stub = _sequence_stub(150)
    missing = McpNotificationListener._observe_sequence(
        stub,
        {"latest_sequence": 3},
        _sequence_notifications(1, 3),
    )
    assert missing == 0
    assert stub._last_sequence == 3


def test_sse_timeout_is_long_enough_for_a_quiet_gateway():
    """旧值是通盘 10 秒：网关静默 10 秒，推送用的 GET 长连接就被掐断，mcp 传输层
    最多重连 2 次就无声放弃——之后再没有推送能到达，状态栏却还显示"已连接"。
    这里必须比那 10 秒宽得多，且是读超时（不是连接超时）在管这件事。"""
    assert SSE_TIMEOUT.read == 300.0
    assert SSE_TIMEOUT.read > 10.0
    assert SSE_TIMEOUT.connect == 30.0


def test_request_timeout_stays_short_despite_the_long_sse_default():
    """initialize / call_tool / read_resource / subscribe_resource /
    unsubscribe_resource 和 GET 长连接共用同一个 httpx 客户端、同一份 SSE_TIMEOUT。
    这些普通请求不该跟着等 300 秒——网关卡住一次响应时，stop()/apply_config() 设
    的 cancel_event 拦不住一个已经在等待的 await，而 app.py::quit() 只给监听线程
    9 秒，够不着 300 秒。这里必须明显短于 SSE_TIMEOUT.read。"""
    assert REQUEST_TIMEOUT_SECONDS < SSE_TIMEOUT.read
    assert REQUEST_TIMEOUT_SECONDS >= 5.0  # 也不能短到网关正常响应都摸不到


def test_bounded_lets_a_fast_call_through_untouched():
    async def fast():
        await asyncio.sleep(0.01)
        return "ok"

    assert asyncio.run(_bounded(fast())) == "ok"


def test_bounded_times_out_and_actually_cancels_a_stuck_call(monkeypatch):
    """超时不能只是"不再等它"——底层协程必须被真的取消，不能留着一直挂在后台。

    这里不传 cancel_event：验证的是"没人喊停、请求也没完成"这一支，超时数字
    本身仍然要生效。
    """
    monkeypatch.setattr(mcp_notifications, "REQUEST_TIMEOUT_SECONDS", 0.02)
    cancelled = []

    async def stuck():
        try:
            await asyncio.sleep(999)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return "never"  # pragma: no cover - 不该走到这里

    async def scenario():
        try:
            await mcp_notifications._bounded(stuck())
        except asyncio.TimeoutError:
            pass
        else:
            raise AssertionError("卡住的调用应该超时，而不是正常返回")
        await asyncio.sleep(0.01)  # 给取消传播留一点时间

    asyncio.run(asyncio.wait_for(scenario(), timeout=2.0))
    assert cancelled == [True]


def test_bounded_is_interrupted_by_an_already_set_cancel_event(monkeypatch):
    """Codex 在 #18 上指出：只缩短超时数字解决不了 stop()/apply_config() 打断不了
    正在等待的请求这件事——网关卡住时，就算超时缩到几秒，也还是要真等那么久。

    这是这条 review 意见对应的核心行为：cancel_event 在调用前就已经设置时，必须
    近乎立刻返回，不能等到 REQUEST_TIMEOUT_SECONDS。超时给得很宽（5 秒），一旦
    退化成"只是缩短了超时"，这条测试会因为等了 5 秒才返回而在 timeout=1.0 上失败，
    足够把回归和"提前打断"区分开。
    """
    monkeypatch.setattr(mcp_notifications, "REQUEST_TIMEOUT_SECONDS", 5.0)
    cancelled = []
    cancel_event = asyncio.Event()
    cancel_event.set()

    async def stuck():
        try:
            await asyncio.sleep(999)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return "never"  # pragma: no cover

    async def scenario():
        try:
            await mcp_notifications._bounded(stuck(), cancel_event)
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancel_event 已设置时应该抛 CancelledError")

    asyncio.run(asyncio.wait_for(scenario(), timeout=1.0))
    assert cancelled == [True]


def test_bounded_is_interrupted_when_cancel_event_fires_mid_flight(monkeypatch):
    """更贴近真实场景：调用发出去之后，stop()/apply_config() 才在中途喊停。"""
    monkeypatch.setattr(mcp_notifications, "REQUEST_TIMEOUT_SECONDS", 5.0)
    cancel_event = asyncio.Event()

    async def stuck():
        await asyncio.sleep(999)
        return "never"  # pragma: no cover

    async def fire_soon():
        await asyncio.sleep(0.05)
        cancel_event.set()

    async def scenario():
        try:
            await asyncio.gather(
                mcp_notifications._bounded(stuck(), cancel_event),
                fire_soon(),
            )
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("中途设置 cancel_event 应该抛 CancelledError")

    asyncio.run(asyncio.wait_for(scenario(), timeout=1.0))


def test_bounded_cancels_its_own_child_tasks_when_its_host_task_is_cancelled():
    """Codex 在 #20 上指出的下一层坑，比上面两个 cancel_event 测试更深一层。

    上面两个测试里，喊停的是 cancel_event——_bounded 自己一直好好跑到
    asyncio.wait 正常返回，只是返回结果告诉调用者"该退了"。这里测的是另一件
    事：_bounded 所在的这个 task 本身被外部直接 cancel() 掉（mcp 传输层内部是
    个 anyio 任务组，一个子任务失败时会连坐取消组里其它任务，run() 那句
    BaseExceptionGroup 的注释说的就是这个）。_bounded 正好挂在
    `await asyncio.wait(...)` 上时被牵连，CancelledError 直接从这次 await 里
    扔出来——asyncio.wait 被取消并不会连带取消它在等的那些 task，只会让等待
    本身提前结束。如果清理代码写在这次 await 之后（而不是 finally 里），就会
    被跳过，request_task / cancel_task 全部留成孤儿。

    这里不通过 cancel_event，直接 cancel 包着 _bounded 的 host task 本身。
    """
    cancel_event = asyncio.Event()  # 一直不 set——这次要试的是 host task 被取消
    cancelled = []

    async def stuck():
        try:
            await asyncio.sleep(999)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return "never"  # pragma: no cover

    async def scenario():
        # asyncio.run(asyncio.wait_for(scenario(), ...)) 本身就会带出两层脚手架
        # task（wait_for 自己那层、scenario() 被包成的那层）——它们和 current_task()
        # 未必是同一个 task，直接拿 all_tasks() 减 current_task() 会把这些无关的
        # 脚手架 task 也算成"泄漏"。先在 _bounded 还没起步时拍一张基线快照，之后
        # 只看多出来的那些，才是 request_task / cancel_task 真正有没有被收拾干净。
        baseline = asyncio.all_tasks()
        host = asyncio.ensure_future(mcp_notifications._bounded(stuck(), cancel_event))
        await asyncio.sleep(0.01)  # 让 host 真的跑进 asyncio.wait 里挂住
        host.cancel()
        try:
            await host
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("host task 被外部取消时，CancelledError 应该传播出来")

        leaked = asyncio.all_tasks() - baseline
        assert leaked == set(), f"_bounded 被外层取消后留下了孤儿任务：{leaked}"

    asyncio.run(asyncio.wait_for(scenario(), timeout=2.0))
    assert cancelled == [True]  # request_task 包的协程真的被取消了，不是被晾在一边


def test_bounded_with_an_unset_cancel_event_behaves_like_plain_timeout():
    """传了 cancel_event 但它没被设置：不该影响正常的超时 / 正常返回。"""
    cancel_event = asyncio.Event()  # 一直不 set

    async def fast():
        await asyncio.sleep(0.01)
        return "ok"

    assert asyncio.run(_bounded(fast(), cancel_event)) == "ok"


class _ListenerStub:
    """轻量替身：只给 ``_consume`` 需要的那几个协作方法，不碰 QThread / Qt 信号，
    测试不用起 QApplication。"""

    def __init__(self) -> None:
        self.delivered: list[str] = []
        self.cancel_events_seen: list[Any] = []

    async def _deliver_resource(self, session, uri: str, cancel_event=None) -> None:
        self.delivered.append(uri)
        self.cancel_events_seen.append(cancel_event)


class _FakeSession:
    """占位 session：这些测试只关心 _consume 调不调 _deliver_resource，不关心
    它读到了什么，所以不需要真的实现 read_resource。"""


def test_consume_delivers_when_a_matching_push_arrives():
    async def scenario():
        stub = _ListenerStub()
        updates: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        cancel_event = asyncio.Event()
        updates.put_nowait("uri://target")

        async def stop_after_delivery():
            # 等 _consume 真的处理完这条推送再喊停，不靠猜测跑几个 tick 才够。
            while not stub.delivered:
                await asyncio.sleep(0)
            cancel_event.set()

        await asyncio.gather(
            McpNotificationListener._consume(stub, updates, _FakeSession(), "uri://target", cancel_event),
            stop_after_delivery(),
        )
        return stub

    stub = asyncio.run(asyncio.wait_for(scenario(), timeout=2.0))
    assert stub.delivered == ["uri://target"]


def test_consume_threads_cancel_event_into_deliver_resource():
    """_deliver_resource 内部会拿 cancel_event 去跟 read_resource 赛跑——传漏了
    的话，网关卡在轮询兜底那一读时，stop()/apply_config() 照样打断不了它，等于
    白加了 _bounded() 的赛跑机制。"""

    async def scenario():
        stub = _ListenerStub()
        updates: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        cancel_event = asyncio.Event()
        updates.put_nowait("uri://target")

        async def stop_after_delivery():
            while not stub.delivered:
                await asyncio.sleep(0)
            cancel_event.set()

        await asyncio.gather(
            McpNotificationListener._consume(stub, updates, _FakeSession(), "uri://target", cancel_event),
            stop_after_delivery(),
        )
        return stub, cancel_event

    stub, cancel_event = asyncio.run(asyncio.wait_for(scenario(), timeout=2.0))
    assert stub.cancel_events_seen == [cancel_event]


def test_consume_ignores_pushes_for_a_different_resource():
    """message_handler 是进程级的，理论上可能收到别的资源的更新通知——不该触发投递。"""

    async def scenario():
        stub = _ListenerStub()
        updates: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        cancel_event = asyncio.Event()
        updates.put_nowait("uri://someone-else")

        async def stop_after_consumed():
            # 等这条不匹配的推送真被 _consume 取出队列，才算测到了"忽略"这一支；
            # 光等一个 tick 赢不了 _consume 内部 asyncio.wait 的调度。
            while not updates.empty():
                await asyncio.sleep(0)
            await asyncio.sleep(0.05)
            cancel_event.set()

        await asyncio.gather(
            McpNotificationListener._consume(stub, updates, _FakeSession(), "uri://mine", cancel_event),
            stop_after_consumed(),
        )
        return stub

    stub = asyncio.run(asyncio.wait_for(scenario(), timeout=2.0))
    assert stub.delivered == []


def test_consume_stops_immediately_without_delivering_on_cancel():
    async def scenario():
        stub = _ListenerStub()
        updates: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        cancel_event = asyncio.Event()
        cancel_event.set()
        await McpNotificationListener._consume(stub, updates, _FakeSession(), "uri://x", cancel_event)
        return stub

    stub = asyncio.run(asyncio.wait_for(scenario(), timeout=2.0))
    assert stub.delivered == []


def test_consume_polls_when_the_push_channel_stays_silent(monkeypatch):
    """核心行为：推送要是无声断掉，_consume 不能永远在 updates.get() 上死等。

    mcp 传输层重连耗尽后就是这样收场的——不抛异常，只是再也不会有推送。旧代码
    在这种情况下会一直显示"已连接 · 实时订阅"，实际上永久收不到任何提醒。
    """
    monkeypatch.setattr(mcp_notifications, "POLL_SECONDS", 0.01)

    async def scenario():
        stub = _ListenerStub()
        updates: asyncio.Queue[str] = asyncio.Queue(maxsize=1)  # 永远没有人往里放
        cancel_event = asyncio.Event()

        async def stop_after_a_few_polls():
            while len(stub.delivered) < 3:
                await asyncio.sleep(0.005)
            cancel_event.set()

        await asyncio.gather(
            McpNotificationListener._consume(stub, updates, _FakeSession(), "uri://x", cancel_event),
            stop_after_a_few_polls(),
        )
        return stub

    stub = asyncio.run(asyncio.wait_for(scenario(), timeout=5.0))
    assert len(stub.delivered) >= 3
    assert all(uri == "uri://x" for uri in stub.delivered)


def test_consume_propagates_deliver_errors_so_the_listener_can_reconnect():
    """轮询兜底读资源失败（网关真的挂了）要冒泡出去，run() 的 except 才能兜住并
    触发重连；吞掉的话又会变回"看着已连接、实际收不到任何东西"。"""

    class _FailingStub(_ListenerStub):
        async def _deliver_resource(self, session, uri: str, cancel_event=None) -> None:
            raise RuntimeError("网关不可达")

    async def scenario():
        stub = _FailingStub()
        updates: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        updates.put_nowait("uri://x")
        cancel_event = asyncio.Event()
        await McpNotificationListener._consume(stub, updates, _FakeSession(), "uri://x", cancel_event)

    try:
        asyncio.run(asyncio.wait_for(scenario(), timeout=2.0))
    except RuntimeError as exc:
        assert "网关不可达" in str(exc)
    else:
        raise AssertionError("_deliver_resource 的异常应该原样冒泡出 _consume")


def test_passive_subscription_follows_the_debug_flag():
    """该标志只描述配置模式；现代 listen 在非 Debug 下仍保留低频 sequence 自检。"""
    assert McpNotificationListener(Config(debug_mode=False))._passive_only() is True
    assert McpNotificationListener(Config(debug_mode=True))._passive_only() is False


def test_negotiate_protocol_prefers_modern_discover():
    class Session:
        def __init__(self):
            self.discovered = False
            self.initialized = False

        async def discover(self):
            self.discovered = True
            return SimpleNamespace()

        async def initialize(self):
            self.initialized = True
            return SimpleNamespace()

    async def scenario():
        session = Session()
        modern = await McpNotificationListener._negotiate_protocol(
            SimpleNamespace(), session, asyncio.Event()
        )
        return modern, session

    modern, session = asyncio.run(scenario())
    assert modern is True
    assert session.discovered is True
    assert session.initialized is False


def test_negotiate_protocol_falls_back_only_when_discover_is_missing():
    class Session:
        def __init__(self):
            self.initialized = False

        async def discover(self):
            from mcp.shared.exceptions import MCPError

            raise MCPError(code=-32601, message="Method not found")

        async def initialize(self):
            self.initialized = True
            return SimpleNamespace()

    async def scenario():
        session = Session()
        modern = await McpNotificationListener._negotiate_protocol(
            SimpleNamespace(), session, asyncio.Event()
        )
        return modern, session

    modern, session = asyncio.run(scenario())
    assert modern is False
    assert session.initialized is True


def test_modern_subscription_converts_resource_updates_into_refresh_requests():
    class Subscription:
        def __init__(self):
            self._events = iter([
                mcp_notifications.ResourceUpdated(uri="gupiao://notifications/alice"),
            ])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._events)
            except StopIteration:
                raise StopAsyncIteration from None

    async def scenario():
        updates: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        await McpNotificationListener._watch_modern_subscription(
            SimpleNamespace(), Subscription(), updates
        )
        return updates.get_nowait()

    assert asyncio.run(scenario()) == "gupiao://notifications/alice"


def test_consume_reconnects_when_modern_listen_stream_ends():
    async def scenario():
        stub = _ListenerStub()
        updates: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        cancel_event = asyncio.Event()

        async def ended_stream():
            await asyncio.sleep(0)

        subscription_task = asyncio.create_task(ended_stream())
        await McpNotificationListener._consume(
            stub,
            updates,
            _FakeSession(),
            "uri://x",
            cancel_event,
            subscription_task=subscription_task,
            mode="2026 listen",
        )

    with pytest.raises(RuntimeError, match="subscriptions/listen 已结束"):
        asyncio.run(asyncio.wait_for(scenario(), timeout=2.0))


def test_negotiate_protocol_falls_back_when_legacy_server_returns_invalid_params():
    class Session:
        def __init__(self):
            self.initialized = False

        async def discover(self):
            from mcp.shared.exceptions import MCPError

            raise MCPError(code=-32602, message="Invalid params")

        async def initialize(self):
            self.initialized = True
            return SimpleNamespace()

    async def scenario():
        session = Session()
        modern = await McpNotificationListener._negotiate_protocol(
            SimpleNamespace(), session, asyncio.Event()
        )
        return modern, session

    modern, session = asyncio.run(scenario())
    assert modern is False
    assert session.initialized is True


def test_modern_subscription_cleanup_ignores_already_set_cancel_event(monkeypatch):
    exited = []

    class Subscription:
        honored = SimpleNamespace(resource_subscriptions=("gupiao://notifications/alice",))

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(3600)

    class ContextManager:
        async def __aenter__(self):
            return Subscription()

        async def __aexit__(self, exc_type, exc, tb):
            exited.append(True)

    async def fake_establish(self, session, uri, cancel_event=None):
        return 0

    async def fake_consume(self, updates, session, uri, cancel_event, **kwargs):
        cancel_event.set()
        return None

    monkeypatch.setattr(mcp_notifications, "listen", lambda *args, **kwargs: ContextManager())
    monkeypatch.setattr(McpNotificationListener, "_establish_baseline", fake_establish)
    monkeypatch.setattr(McpNotificationListener, "_consume", fake_consume)

    async def scenario():
        listener = McpNotificationListener(Config())
        cancel_event = asyncio.Event()
        await listener._run_modern_subscription(
            SimpleNamespace(), "gupiao://notifications/alice", cancel_event
        )

    asyncio.run(scenario())
    assert exited == [True]
