"""通过 MCP 资源订阅实时接收 gupiao_ztfx 通知事件。"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import warnings
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import httpx2
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPDeprecationWarning
from PySide6.QtCore import QThread, Signal

from .config import Config

MCP_API_KEY_ENV = "GUPIAO_MCP_API_KEY"
RECONNECT_SECONDS = 3


@dataclass(frozen=True)
class McpNotification:
    event_id: str
    title: str
    body: str
    event_type: str = ""
    priority: str = "normal"
    created_at: str = ""
    link: str = ""


def _api_key(config: Config) -> str:
    return config.mcp_api_key or os.environ.get(MCP_API_KEY_ENV, "").strip()


def _describe_error(exc: BaseException, limit: int = 160) -> str:
    """摊平 ExceptionGroup，否则状态栏只会显示 'unhandled errors in a TaskGroup'。

    任务组把真正的原因（连接被拒、401、超时……）包在 exceptions 里，直接 str()
    出来的那句话对排查毫无帮助。
    """
    leaves: list[str] = []

    def walk(node: BaseException) -> None:
        nested = getattr(node, "exceptions", None)
        if nested:
            for child in nested:
                walk(child)
            return
        text = str(node).strip()
        leaves.append(f"{type(node).__name__}: {text}" if text else type(node).__name__)

    walk(exc)
    return (" / ".join(dict.fromkeys(leaves)) or type(exc).__name__)[:limit]


def _tool_payload(result: Any) -> dict[str, Any]:
    if getattr(result, "is_error", getattr(result, "isError", False)):
        raise RuntimeError("MCP 工具返回错误")
    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    return _json_text_payload(getattr(result, "content", ()))


def _json_text_payload(blocks: Any) -> dict[str, Any]:
    for block in blocks:
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("MCP 返回内容格式不正确")


def _resource_payload(result: Any) -> dict[str, Any]:
    return _json_text_payload(getattr(result, "contents", ()))


def _notifications(payload: dict[str, Any]) -> list[McpNotification]:
    rows = payload.get("notifications")
    if not isinstance(rows, list):
        return []
    notifications = []
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("event_id") or "").strip():
            continue
        notifications.append(
            McpNotification(
                event_id=str(row["event_id"]).strip(),
                title=str(row.get("title") or "MCP 提醒").strip(),
                body=str(row.get("body") or "").strip(),
                event_type=str(row.get("event_type") or ""),
                priority=str(row.get("priority") or "normal"),
                created_at=str(row.get("created_at") or ""),
                link=str(row.get("link") or ""),
            )
        )
    return notifications


class McpNotificationListener(QThread):
    notification_ready = Signal(object)
    status_changed = Signal(str)

    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cancel_event: asyncio.Event | None = None
        self._last_status = ""
        self._baseline_ready = False
        self._seen_order: deque[str] = deque()
        self._seen_ids: set[str] = set()

    @staticmethod
    def _identity(config: Config) -> tuple[bool, str, str]:
        return config.mcp_notifications_enabled, config.mcp_url, _api_key(config)

    def apply_config(self, config: Config) -> None:
        with self._lock:
            changed = self._identity(config) != self._identity(self._config)
            self._config = config
            if changed:
                self._baseline_ready = False
                self._seen_order.clear()
                self._seen_ids.clear()
        if changed:
            self._wake.set()
            self._cancel_connection()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        self._cancel_connection()

    def _cancel_connection(self) -> None:
        with self._lock:
            loop, cancel_event = self._loop, self._cancel_event
        if loop is not None and cancel_event is not None:
            loop.call_soon_threadsafe(cancel_event.set)

    def _emit_status(self, status: str) -> None:
        if status != self._last_status:
            self._last_status = status
            self.status_changed.emit(status)

    def run(self) -> None:  # noqa: D102 - QThread 入口
        while not self._stopping.is_set():
            with self._lock:
                config = self._config
            if not config.mcp_notifications_enabled:
                self._emit_status("已关闭")
                self._wake.wait(3600)
                self._wake.clear()
                continue
            try:
                asyncio.run(self._listen(config))
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:  # noqa: BLE001 - MCP 失败不能拖垮行情窗口
                # 必须兜到 BaseException：CancelledError 继承的是 BaseException，
                # 服务端连接被切断（网关重启等）时 anyio 的任务组会抛
                # BaseExceptionGroup。只兜 Exception 的话它会穿透出去、while 直接
                # 退出，监听线程就此永久死掉，而且界面上没有任何提示——表现为
                # 开关还开着、进程还活着，但再也不重连。
                if self._stopping.is_set():
                    break
                self._emit_status(f"连接失败：{_describe_error(exc)}")
            if not self._stopping.is_set():
                self._wake.wait(RECONNECT_SECONDS)
                self._wake.clear()

    async def _listen(self, config: Config) -> None:
        key = _api_key(config)
        if not key:
            raise RuntimeError(f"未配置 MCP API Key（设置页或环境变量 {MCP_API_KEY_ENV}）")
        cancel_event = asyncio.Event()
        self._set_async_state(cancel_event)
        try:
            await self._run_session(config, key, cancel_event)
        finally:
            self._set_async_state(None)

    def _set_async_state(self, cancel_event: asyncio.Event | None) -> None:
        with self._lock:
            self._loop = asyncio.get_running_loop() if cancel_event is not None else None
            self._cancel_event = cancel_event

    async def _run_session(self, config: Config, key: str, cancel_event: asyncio.Event) -> None:
        headers = {"Authorization": f"Bearer {key}"}
        updates: asyncio.Queue[str] = asyncio.Queue(maxsize=1)

        async def handle_message(message: Any) -> None:
            if isinstance(message, types.ResourceUpdatedNotification):
                if updates.empty():
                    updates.put_nowait(str(message.params.uri))

        async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(10.0)) as client:
            async with streamable_http_client(
                config.mcp_url,
                http_client=client,
                terminate_on_close=False,
            ) as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    message_handler=handle_message,
                ) as session:
                    await session.initialize()
                    info = _tool_payload(await session.call_tool("get_notification_stream"))
                    uri = str(info.get("resource_uri") or "")
                    if not uri:
                        raise RuntimeError("MCP 未返回通知资源地址")
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", MCPDeprecationWarning)
                        await session.subscribe_resource(uri)
                    try:
                        await self._establish_baseline(session, uri)
                        self._emit_status("已连接 · 实时订阅")
                        await self._consume(updates, session, uri, cancel_event)
                    finally:
                        with suppress(Exception):
                            with warnings.catch_warnings():
                                warnings.simplefilter("ignore", MCPDeprecationWarning)
                                await session.unsubscribe_resource(uri)

    async def _establish_baseline(self, session: ClientSession, uri: str) -> None:
        payload = _resource_payload(await session.read_resource(uri))
        with self._lock:
            baseline_ready = self._baseline_ready
        if baseline_ready:
            self._deliver(_notifications(payload))
            return
        for item in _notifications(payload):
            self._remember(item.event_id)
        with self._lock:
            self._baseline_ready = True

    async def _deliver_resource(self, session: ClientSession, uri: str) -> None:
        payload = _resource_payload(await session.read_resource(uri))
        self._deliver(_notifications(payload))

    async def _consume(
        self,
        updates: asyncio.Queue[str],
        session: ClientSession,
        uri: str,
        cancel_event: asyncio.Event,
    ) -> None:
        while not cancel_event.is_set():
            event_task = asyncio.create_task(updates.get())
            cancel_task = asyncio.create_task(cancel_event.wait())
            done, pending = await asyncio.wait(
                {event_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if cancel_task in done:
                return
            if event_task.result() == uri:
                await self._deliver_resource(session, uri)

    def _deliver(self, notifications: list[McpNotification]) -> None:
        for notification in notifications:
            if self._remember(notification.event_id):
                self.notification_ready.emit(notification)

    def _remember(self, event_id: str) -> bool:
        with self._lock:
            if event_id in self._seen_ids:
                return False
            if len(self._seen_order) >= 1000:
                self._seen_ids.discard(self._seen_order.popleft())
            self._seen_order.append(event_id)
            self._seen_ids.add(event_id)
            return True
