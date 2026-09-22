"""通过 gupiao_ztfx MCP 每分钟读取 TDX 千档挂单。"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from PySide6.QtCore import QThread, Signal

from .config import Config
from .mcp_notifications import SSE_TIMEOUT, _api_key, _bounded, _tool_payload
from .symbols import classify

DEPTH_POLL_SECONDS = 60.0
MIN_THOUSAND_LEVELS = 11


@dataclass(frozen=True)
class DepthLevel:
    side: str
    price: float
    volume: float


@dataclass(frozen=True)
class DepthSnapshot:
    symbol: str
    levels: tuple[DepthLevel, ...] = ()
    fetched_at: str = ""
    received_at: float = 0.0
    full_depth: bool = False
    available: bool = False
    bid_count: int = 0
    ask_count: int = 0
    reason: str = ""


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _raw_levels(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("levels")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]

    thousand = payload.get("thousand")
    nested = thousand.get("levels") if isinstance(thousand, dict) else None
    if not isinstance(nested, dict):
        return []

    flattened: list[dict[str, Any]] = []
    for side in ("bid", "ask"):
        values = nested.get(side)
        if not isinstance(values, list):
            continue
        for row in values:
            if isinstance(row, dict):
                flattened.append({**row, "side": str(row.get("side") or side)})
    return flattened


def parse_depth_payload(
    symbol: str, payload: dict[str, Any], *, received_at: float | None = None
) -> DepthSnapshot:
    """把 get_tdx_depth 的返回收敛成绘图所需字段。

    十档/五档不能冒充千档；只有超过 10 个有效档位才给 UI 显示“千档”叠加层。
    """
    normalized = classify(str(payload.get("symbol") or symbol))
    code = normalized.code if normalized else str(symbol or "").strip()[-6:]
    levels: list[DepthLevel] = []
    for row in _raw_levels(payload):
        side = str(row.get("side") or "").lower()
        price = _number(row.get("price"))
        volume = _number(row.get("volume"))
        if side not in {"bid", "ask"} or price is None or volume is None:
            continue
        if price <= 0 or volume <= 0:
            continue
        levels.append(DepthLevel(side=side, price=price, volume=volume))

    bid_count = sum(level.side == "bid" for level in levels)
    ask_count = sum(level.side == "ask" for level in levels)
    available = bool(payload.get("available")) and len(levels) >= MIN_THOUSAND_LEVELS
    full_depth = bool(
        available
        and (payload.get("full_depth_verified") is True or payload.get("complete") is True)
        and payload.get("depth_limit_reached") is not True
    )
    return DepthSnapshot(
        symbol=code,
        levels=tuple(levels),
        fetched_at=str(payload.get("fetched_at") or payload.get("source_at") or ""),
        received_at=time.time() if received_at is None else float(received_at),
        full_depth=full_depth,
        available=available,
        bid_count=bid_count,
        ask_count=ask_count,
        reason=str(payload.get("reason") or ""),
    )


class McpDepthPoller(QThread):
    """独立于行情线程的低频千档采集，慢请求不会阻塞 1 秒走势图。"""

    depth_ready = Signal(object)
    status_changed = Signal(str)

    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._last_status = ""

    @staticmethod
    def _enabled(config: Config) -> bool:
        return bool(
            config.symbols
            and _api_key(config)
            and (config.display_theme == "theme2" or (config.show_sparkline and config.intraday_chart))
        )

    def apply_config(self, config: Config) -> None:
        with self._lock:
            changed = (
                config.mcp_url,
                _api_key(config),
                tuple(config.symbols),
                config.show_sparkline,
                config.intraday_chart,
                config.display_theme,
            ) != (
                self._config.mcp_url,
                _api_key(self._config),
                tuple(self._config.symbols),
                self._config.show_sparkline,
                self._config.intraday_chart,
                self._config.display_theme,
            )
            self._config = config
        if changed:
            self._wake.set()

    def refresh_now(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()

    def _emit_status(self, status: str) -> None:
        if status != self._last_status:
            self._last_status = status
            self.status_changed.emit(status)

    def run(self) -> None:  # noqa: D102
        while not self._stopping.is_set():
            with self._lock:
                config = self._config
            if not self._enabled(config):
                self._emit_status("已关闭")
                self._wake.wait(3600)
                self._wake.clear()
                continue

            started = time.monotonic()
            try:
                asyncio.run(self._fetch_cycle(config))
                if not self._stopping.is_set():
                    self._emit_status("已连接 · 千档 60s")
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:  # noqa: BLE001
                if not self._stopping.is_set():
                    self._emit_status(f"读取失败：{type(exc).__name__}: {str(exc)[:120]}")

            remaining = max(0.0, DEPTH_POLL_SECONDS - (time.monotonic() - started))
            if not self._stopping.is_set():
                self._wake.wait(remaining)
                self._wake.clear()

    async def _fetch_cycle(self, config: Config) -> None:
        key = _api_key(config)
        if not key:
            return
        headers = {"Authorization": f"Bearer {key}"}
        async with httpx2.AsyncClient(headers=headers, timeout=SSE_TIMEOUT) as client:
            async with streamable_http_client(
                config.mcp_url,
                http_client=client,
                terminate_on_close=False,
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await _bounded(session.initialize())
                    for raw_symbol in config.symbols:
                        if self._stopping.is_set() or self._wake.is_set():
                            return
                        symbol = classify(raw_symbol)
                        if symbol is None:
                            continue
                        result = await _bounded(
                            session.call_tool(
                                "get_tdx_depth",
                                arguments={"symbol": symbol.code, "force": True},
                            )
                        )
                        snapshot = parse_depth_payload(symbol.code, _tool_payload(result))
                        # 读取失败/退化到十档时保留 UI 上一份千档，过期后由绘图层标“延迟”。
                        if snapshot.available:
                            self.depth_ready.emit(snapshot)
