#!/usr/bin/env python
"""MCP 网关探针：把 gupiao_ztfx 到底提供了什么、推了什么，原样打出来。

组件对提醒是不过滤的——网关推什么就转发什么。所以「没收到某类提醒」只有两种
可能：网关根本没推，或者订阅没通。这个脚本把两者分开：

    python scripts/mcp_probe.py                 # 列工具/资源，dump 当前事件
    python scripts/mcp_probe.py --watch 120     # 再盯 120 秒，看有没有实时推送
    python scripts/mcp_probe.py --grep B/S      # 只挑正文里带这个关键词的事件

地址和 Key 直接读组件的配置（设置页里填的那份），也可以用环境变量覆盖：
GUPIAO_MCP_API_KEY。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import warnings
from collections import Counter
from contextlib import suppress
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx2
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.client.subscriptions import ResourceUpdated, listen
from mcp.shared.exceptions import MCPDeprecationWarning, MCPError

from stockwidget.config import Store
from stockwidget.mcp_notifications import (
    MCP_API_KEY_ENV,
    _api_key,
    _describe_error,
    _notifications,
    _resource_payload,
    _tool_payload,
)


def rule(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def dump(label: str, value: Any) -> None:
    print(f"{label}:")
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def sse_timeout(watch: int) -> httpx2.Timeout:
    """给 SSE 长连接留足读超时。

    推送通知走的是一条 GET 长连接，它和普通请求共用这个超时。读超时一旦短于网关
    的静默间隔，长连接就会断，而 mcp 传输层的 ``handle_get_stream`` 最多重连
    ``MAX_RECONNECTION_ATTEMPTS``（2）次就 ``return``——只打一条 debug 日志，不抛
    异常。此后推送通知永远收不到，探针却还在正常跑，最后会得出「网关没发通知」
    这个正好相反的结论。

    库自己推荐的默认就是 ``read=300s``（create_mcp_http_client），这里再按 --watch
    的时长放宽，保证盯多久都不会中途失聪。
    """
    return httpx2.Timeout(30.0, read=max(300.0, watch + 60))


async def _watch_modern(sub: Any, seen_live: list[str]) -> None:
    async for event in sub:
        if isinstance(event, ResourceUpdated):
            uri = str(event.uri)
            seen_live.append(uri)
            print(f"  ← 收到 subscriptions/listen 资源更新：{uri}")


async def probe(url: str, key: str, watch: int, grep: str) -> int:
    headers = {"Authorization": f"Bearer {key}"}
    seen_live: list[str] = []

    async def handle_message(message: Any) -> None:
        # 仅 legacy resources/subscribe 回退路径会走这里。
        if isinstance(message, types.ResourceUpdatedNotification):
            seen_live.append(str(message.params.uri))
            print(f"  ← 收到 legacy 资源更新通知：{message.params.uri}")

    async with httpx2.AsyncClient(headers=headers, timeout=sse_timeout(watch)) as client:
        async with streamable_http_client(
            url, http_client=client, terminate_on_close=False
        ) as (read_stream, write_stream):
            async with ClientSession(
                read_stream, write_stream, message_handler=handle_message
            ) as session:
                modern = True
                try:
                    await session.discover()
                    print("✓ 已连接并完成 server/discover（2026-07-28）")
                except MCPError as exc:
                    if getattr(exc, "code", None) != -32601:
                        raise
                    modern = False
                    await session.initialize()
                    print("✓ 服务端不支持 server/discover，已回退 initialize（legacy）")

                rule("网关提供的工具")
                tools = await session.list_tools()
                for tool in tools.tools:
                    print(f"  · {tool.name} —— {(tool.description or '').strip()[:100]}")

                rule("网关提供的资源")
                try:
                    resources = await session.list_resources()
                    for res in resources.resources:
                        print(f"  · {res.uri}  ({res.name})")
                except Exception as exc:  # 不是每个网关都实现 list_resources
                    print(f"  （网关不支持 list_resources：{_describe_error(exc)}）")

                rule("get_notification_stream 返回")
                info = _tool_payload(await session.call_tool("get_notification_stream"))
                dump("  payload", info)
                uri = str(info.get("resource_uri") or "")
                if not uri:
                    print("✗ 网关没给 resource_uri，订阅无从谈起")
                    return 1

                if watch <= 0:
                    return await inspect_and_watch(session, uri, watch, grep, seen_live)

                if modern:
                    subscription_cm = listen(session, resource_subscriptions=[uri])
                    sub = await subscription_cm.__aenter__()
                    watcher = asyncio.create_task(_watch_modern(sub, seen_live))
                    try:
                        honored = tuple(getattr(sub.honored, "resource_subscriptions", None) or ())
                        print(f"\n✓ subscriptions/listen 已确认，honored={honored}")
                        if uri not in honored:
                            print("✗ 服务端没有确认目标 resource URI")
                            return 1
                        # listen ack 之后再取基线；官方语义保证从 ack 起的新变化不会丢。
                        return await inspect_and_watch(session, uri, watch, grep, seen_live)
                    finally:
                        watcher.cancel()
                        await asyncio.gather(watcher, return_exceptions=True)
                        with suppress(Exception):
                            await subscription_cm.__aexit__(None, None, None)

                # 只给尚未升级到 2026 协议的服务端保留。
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", MCPDeprecationWarning)
                    await session.subscribe_resource(uri)
                print(f"\n✓ 已订阅 {uri}（legacy resources/subscribe）")
                try:
                    return await inspect_and_watch(session, uri, watch, grep, seen_live)
                finally:
                    with suppress(Exception):
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore", MCPDeprecationWarning)
                            await session.unsubscribe_resource(uri)


async def inspect_and_watch(
    session: ClientSession, uri: str, watch: int, grep: str, seen_live: list[str]
) -> int:
    rule("通知资源当前的完整内容")
    payload = _resource_payload(await session.read_resource(uri))
    dump("  raw", payload)

    items = _notifications(payload)
    rule(f"解析出 {len(items)} 条事件")
    if items:
        kinds = Counter(i.event_type or "(空)" for i in items)
        print(f"  event_type 分布：{dict(kinds)}")
        print()
    for item in items:
        blob = f"{item.event_type} {item.title} {item.body}"
        if grep and grep.lower() not in blob.lower():
            continue
        print(f"  [{item.created_at or '时间未知'}] type={item.event_type!r} "
              f"prio={item.priority!r}\n    {item.title}\n    {item.body[:200]}")
    if grep:
        hit = sum(1 for i in items
                  if grep.lower() in f"{i.event_type} {i.title} {i.body}".lower())
        print(f"\n  含「{grep}」的事件：{hit} 条")

    if watch <= 0:
        return 0

    # listen/subscribe 都在读基线之前建立，所以取基线期间的通知属于基线，不计入
    # 测量区间；从这里开始只统计真正发生在 --watch 窗口里的更新。
    mark = len(seen_live)
    before = {i.event_id for i in items}

    rule(f"盯 {watch} 秒，看有没有实时推送")
    if mark:
        print(f"  （取基线期间已收到 {mark} 次通知，计入基线，不算在本次测量里）")
    print("  等待中……（没有输出就是网关没推）")
    await asyncio.sleep(watch)

    after = _notifications(_resource_payload(await session.read_resource(uri)))
    fresh = [i for i in after if i.event_id not in before]
    during = seen_live[mark:]
    rule("盯完了")
    print(f"  收到资源更新通知 {len(during)} 次：{during or '（一次都没有）'}")
    print(f"  这段时间新增事件 {len(fresh)} 条")
    for item in fresh:
        print(f"    type={item.event_type!r}  {item.title}  {item.body[:120]}")
    print(f"\n  → {verdict(len(during), len(fresh))}")
    return 0


def verdict(pushes: int, fresh: int) -> str:
    """把「收到几次通知」和「新增几条事件」翻成一句结论。"""
    if pushes and fresh:
        return "推送通道正常：有通知、也有对应的新事件"
    if pushes and not fresh:
        return "⚠ 有推送通知但没有新事件——资源被更新了，内容却没变"
    if fresh and not pushes:
        return "⚠ 有新事件但网关没发推送通知——组件靠通知触发，这种情况它收不到"
    return "这段时间网关既没推通知、也没有新事件——就是没东西可推"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--watch", type=int, default=0, help="订阅后盯多少秒（默认 0，不盯）")
    parser.add_argument("--grep", default="", help="只显示正文/标题/类型里含该关键词的事件")
    parser.add_argument("--url", default="", help="覆盖配置里的 MCP 地址")
    args = parser.parse_args()

    config = Store().get()
    url = args.url or config.mcp_url
    key = _api_key(config)
    if not key:
        print(f"✗ 没有 API Key：在设置页填，或设环境变量 {MCP_API_KEY_ENV}", file=sys.stderr)
        return 2

    print(f"目标：{url}")
    print(f"Key ：{'*' * 8}{key[-4:]}（末四位）")
    try:
        return asyncio.run(probe(url, key, args.watch, args.grep))
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:  # noqa: BLE001 - 探针要把真正的原因摊出来
        print(f"\n✗ 失败：{_describe_error(exc, limit=400)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
