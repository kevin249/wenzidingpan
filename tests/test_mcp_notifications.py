from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from stockwidget import mcp_notifications
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
