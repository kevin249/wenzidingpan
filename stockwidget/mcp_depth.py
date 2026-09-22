"""通过 gupiao_ztfx MCP 读取 TDX 深度，并在千档失败时自动降级。"""

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
from .mcp_bs import record_volatility_bs
from .mcp_notifications import SSE_TIMEOUT, _api_key, _bounded, _tool_payload
from .symbols import classify

DEPTH_POLL_SECONDS = 60.0
DEPTH_RETRY_SECONDS = 5.0
BS_POLL_SECONDS = 60.0
MIN_THOUSAND_LEVELS = 11

DEPTH_FULL = "FULL_DEPTH"
DEPTH_TEN = "TEN_LEVEL"
DEPTH_FIVE = "FIVE_LEVEL"
DEPTH_NONE = "NONE"


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
    depth_mode: str = DEPTH_NONE
    requested_depth: int = 0


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


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


def _depth_mode(
    payload: dict[str, Any],
    *,
    requested_depth: int,
    bid_count: int,
    ask_count: int,
    level_count: int,
) -> str:
    upstream_available = bool(payload.get("available")) and level_count > 0
    full = bool(
        upstream_available
        and payload.get("full_depth_verified") is True
        and payload.get("depth_limit_reached") is not True
    )
    if full:
        return DEPTH_FULL
    if not upstream_available:
        return DEPTH_NONE

    kind = str(payload.get("book_kind") or "").strip().lower()
    if requested_depth == 10 or kind in {"ten_level", "level10", "10"}:
        return DEPTH_TEN
    if requested_depth == 5 or kind in {"five_level", "level5", "5"}:
        return DEPTH_FIVE

    # 兼容旧 MCP 返回：没有 requested_depth 时只能按档数保守判断。
    if bid_count >= 10 and ask_count >= 10:
        return DEPTH_TEN
    if bid_count > 0 and ask_count > 0:
        return DEPTH_FIVE
    return DEPTH_NONE


def parse_depth_payload(
    symbol: str,
    payload: dict[str, Any],
    *,
    received_at: float | None = None,
    requested_depth: int | None = None,
) -> DepthSnapshot:
    """把 get_tdx_depth 返回收敛成 FULL_DEPTH / TEN_LEVEL / FIVE_LEVEL。"""
    normalized = classify(str(payload.get("symbol") or symbol))
    code = normalized.code if normalized else str(symbol or "").strip()[-6:]
    request = _positive_int(payload.get("requested_depth"))
    if not request and requested_depth is not None:
        request = _positive_int(requested_depth)

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
    mode = _depth_mode(
        payload,
        requested_depth=request,
        bid_count=bid_count,
        ask_count=ask_count,
        level_count=len(levels),
    )
    return DepthSnapshot(
        symbol=code,
        levels=tuple(levels),
        fetched_at=str(payload.get("fetched_at") or payload.get("source_at") or ""),
        received_at=time.time() if received_at is None else float(received_at),
        full_depth=mode == DEPTH_FULL,
        available=mode != DEPTH_NONE,
        bid_count=bid_count,
        ask_count=ask_count,
        reason=str(payload.get("reason") or payload.get("reason_code") or ""),
        depth_mode=mode,
        requested_depth=request,
    )


async def _fetch_volatility_bs(session: ClientSession, symbol: str) -> dict[str, Any]:
    """主动读取该股票当天完整 B/S markers，并写入共享缓存。"""
    result = await _bounded(
        session.call_tool(
            "get_volatility_bs",
            arguments={"symbol": symbol, "trade_date": "", "limit": 1000},
        )
    )
    payload = _tool_payload(result)
    record_volatility_bs(payload)
    return payload


async def _fetch_requested_depth(
    session: ClientSession,
    symbol: str,
    requested_depth: int,
) -> DepthSnapshot:
    result = await _bounded(
        session.call_tool(
            "get_tdx_depth",
            arguments={
                "symbol": symbol,
                "force": True,
                "requested_depth": requested_depth,
            },
        )
    )
    return parse_depth_payload(
        symbol,
        _tool_payload(result),
        requested_depth=requested_depth,
    )


async def _fetch_depth_with_fallback(
    session: ClientSession,
    symbol: str,
) -> DepthSnapshot:
    """严格按 千档 → 十档 → 五档 获取，任何降级都保持可显示。"""
    last = DepthSnapshot(symbol=symbol, reason="depth_unavailable")
    try:
        last = await _fetch_requested_depth(session, symbol, 1000)
        if last.full_depth:
            return last
    except Exception as exc:
        last = DepthSnapshot(symbol=symbol, reason=f"1000:{type(exc).__name__}")

    try:
        ten = await _fetch_requested_depth(session, symbol, 10)
        if ten.available:
            return ten
        last = ten
    except Exception as exc:
        last = DepthSnapshot(symbol=symbol, reason=f"10:{type(exc).__name__}")

    try:
        five = await _fetch_requested_depth(session, symbol, 5)
        if five.available:
            return five
        return five
    except Exception as exc:
        return DepthSnapshot(symbol=symbol, reason=f"5:{type(exc).__name__}")


def _poll_seconds(all_full_depth: bool) -> float:
    return DEPTH_POLL_SECONDS if all_full_depth else DEPTH_RETRY_SECONDS


def _status_text(all_full_depth: bool) -> str:
    return (
        "已连接 · 千档 60s"
        if all_full_depth
        else "已连接 · 深度降级 · 千档 5s 重试"
    )


class McpDepthPoller(QThread):
    """深度采集：正常千档低频，降级后 5 秒主动恢复。"""

    depth_ready = Signal(object)
    status_changed = Signal(str)

    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._last_status = ""
        self._last_bs_fetch_at = 0.0

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
            all_full_depth = False
            fetch_bs = started - self._last_bs_fetch_at >= BS_POLL_SECONDS
            try:
                all_full_depth = asyncio.run(self._fetch_cycle(config, fetch_bs=fetch_bs))
                if fetch_bs:
                    self._last_bs_fetch_at = time.monotonic()
                if not self._stopping.is_set():
                    self._emit_status(_status_text(all_full_depth))
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:  # noqa: BLE001
                if not self._stopping.is_set():
                    self._emit_status(
                        f"读取失败 · 千档5s重试：{type(exc).__name__}: {str(exc)[:100]}"
                    )

            interval = _poll_seconds(all_full_depth)
            remaining = max(0.0, interval - (time.monotonic() - started))
            if not self._stopping.is_set():
                self._wake.wait(remaining)
                self._wake.clear()

    async def _fetch_cycle(self, config: Config, *, fetch_bs: bool) -> bool:
        key = _api_key(config)
        if not key:
            return False
        headers = {"Authorization": f"Bearer {key}"}
        all_full_depth = True
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
                            return False
                        symbol = classify(raw_symbol)
                        if symbol is None:
                            continue

                        if fetch_bs:
                            try:
                                await _fetch_volatility_bs(session, symbol.code)
                            except Exception:
                                # B/S 与盘口恢复独立；B/S 失败不阻断深度降级。
                                pass

                        snapshot = await _fetch_depth_with_fallback(session, symbol.code)
                        if not snapshot.full_depth:
                            all_full_depth = False
                        # 十档/五档/不可用都要发给 UI，不能让旧千档永久卡住。
                        self.depth_ready.emit(snapshot)
        return all_full_depth
