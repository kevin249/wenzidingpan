"""MCP 事件缓存与 B/S 点来源选择。

B/S 有两个互斥来源：

* ``local``：沿用组件自己的分时反转计算；
* ``mcp``：只显示 MCP 通道收到的 ``trading.holding_t_signal`` 信号。

MCP 通知监听线程、Qt 行情线程和 Flask 设置线程都会访问这里，因此所有共享状态都
由同一把锁保护。来源选择单独落盘，避免改动主配置 schema。
"""

from __future__ import annotations

import json
import re
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import config_dir

SOURCE_LOCAL = "local"
SOURCE_MCP = "mcp"
VALID_SOURCES = {SOURCE_LOCAL, SOURCE_MCP}
SOURCE_FILE_NAME = "mcp_bs_source.json"
MAX_EVENTS = 2000
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
BS_EVENT_TYPE = "trading.holding_t_signal"

_LOCK = threading.RLock()
_EVENTS: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
_EVENT_IDS: set[str] = set()
# get_volatility_bs 主动拉取的“当天完整 markers”按股票覆盖保存；它比通知缓冲更完整。
_VOLATILITY_BS: dict[str, dict[str, Any]] = {}
_SOURCE: str | None = None

_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_TIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})")


def _source_path() -> Path:
    return config_dir() / SOURCE_FILE_NAME


def _load_source() -> str:
    try:
        payload = json.loads(_source_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return SOURCE_LOCAL
    value = str(payload.get("source") or "").strip().lower() if isinstance(payload, dict) else ""
    return value if value in VALID_SOURCES else SOURCE_LOCAL


def get_source() -> str:
    global _SOURCE
    with _LOCK:
        if _SOURCE is None:
            _SOURCE = _load_source()
        return _SOURCE


def set_source(value: str) -> str:
    """设置并持久化 B/S 来源，返回实际生效值。"""
    global _SOURCE
    source = str(value or "").strip().lower()
    if source not in VALID_SOURCES:
        raise ValueError("B/S 来源只能是 local 或 mcp")
    with _LOCK:
        _SOURCE = source
        path = _source_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"source": source}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    return source


def _payload(notification: Any) -> dict[str, Any]:
    value = getattr(notification, "payload", {})
    return dict(value) if isinstance(value, dict) else {}


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        match = _TIME_RE.search(text)
        if not match:
            return None
        try:
            parsed = datetime.fromisoformat(f"{match.group(1)}T{match.group(2)}")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed.astimezone(SHANGHAI_TZ)


def _normalize_code(value: Any) -> str:
    match = _CODE_RE.search(str(value or ""))
    return match.group(1) if match else ""


def _stock_info(payload: dict[str, Any], title: str, body: str) -> tuple[str, str]:
    code = _normalize_code(
        payload.get("code") or payload.get("symbol") or payload.get("stock_code") or title or body
    )
    name = str(payload.get("name") or payload.get("stock_name") or "").strip()
    if not name and code and code in title:
        prefix = title.split(code, 1)[0].strip(" 【[]()-—:：")
        if prefix:
            name = prefix.split()[-1]
    return code, name


def _category(event_type: str) -> str:
    event_type = str(event_type or "").strip().lower()
    if event_type == BS_EVENT_TYPE:
        return "B/S 波动点"
    if event_type.startswith("trading."):
        return "交易提醒"
    if event_type.startswith("screen."):
        return "选股 / 筛选"
    if event_type.startswith("market."):
        return "行情提醒"
    if event_type.startswith("ai."):
        return "AI 分析"
    if event_type.startswith("leader."):
        return "龙头 / 复盘"
    if event_type.startswith("backtest."):
        return "回测 / 策略任务"
    if event_type.startswith("research."):
        return "研究资讯"
    if event_type.startswith("sentiment."):
        return "市场情绪"
    if event_type.startswith("system."):
        return "系统"
    return event_type or "其他"


def _side(payload: dict[str, Any], title: str, body: str) -> str:
    direction = str(payload.get("direction") or payload.get("type") or "").strip().lower()
    if direction == "buy_first":
        return "B"
    if direction == "sell_first":
        return "S"
    signal = str(payload.get("side") or payload.get("signal") or "").strip().upper()
    if signal in {"B", "S"}:
        return signal
    text = f"{title}\n{body}"
    match = re.search(r"(?:信号[：:]?\s*)?\b([BS])(?:[234])?\b", text, re.IGNORECASE)
    return match.group(1).upper() if match else ""


def record_notification(notification: Any) -> None:
    """把 MCP 资源中的一条通知放入当天列表；event_id 幂等去重。"""
    event_id = str(getattr(notification, "event_id", "") or "").strip()
    if not event_id:
        return
    payload = _payload(notification)
    title = str(getattr(notification, "title", "") or "").strip()
    body = str(getattr(notification, "body", "") or "").strip()
    event_type = str(getattr(notification, "event_type", "") or "").strip()
    created_at = str(getattr(notification, "created_at", "") or "").strip()
    code, name = _stock_info(payload, title, body)
    row = {
        "event_id": event_id,
        "event_type": event_type,
        "category": _category(event_type),
        "title": title,
        "body": body,
        "priority": str(getattr(notification, "priority", "normal") or "normal"),
        "created_at": created_at,
        "link": str(getattr(notification, "link", "") or ""),
        "payload": payload,
        "stock_code": code,
        "stock_name": name,
        "side": _side(payload, title, body),
        "received_at": datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
    }
    with _LOCK:
        if event_id in _EVENT_IDS:
            return
        if len(_EVENTS) >= MAX_EVENTS and _EVENTS:
            _EVENT_IDS.discard(str(_EVENTS[0].get("event_id") or ""))
        _EVENTS.append(row)
        _EVENT_IDS.add(event_id)


def record_volatility_bs(payload: Any) -> None:
    """缓存 get_volatility_bs 返回的当日完整 B/S markers。

    MCP 工具是只读落盘结果，limit=200 足够覆盖一个交易日所有 B2/B3/B4/S2/S3/S4。
    同一股票每次主动拉取都整包覆盖，避免旧 marker 因通知缓存残留而继续显示。
    """
    if not isinstance(payload, dict):
        return
    code = _normalize_code(payload.get("code") or payload.get("symbol"))
    trade_date = str(payload.get("trade_date") or "").strip()
    if not code or not trade_date:
        return
    markers = [
        dict(item)
        for item in (payload.get("markers") or [])
        if isinstance(item, dict)
    ]
    snapshot = {
        "trade_date": trade_date,
        "available": payload.get("available") is True,
        "markers": markers,
        "as_of_time": str(payload.get("as_of_time") or ""),
        "received_at": datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
    }
    with _LOCK:
        _VOLATILITY_BS[code] = snapshot


def today_records(now: datetime | None = None) -> list[dict[str, Any]]:
    """返回上海交易日口径的当日 MCP 事件，新事件在前。"""
    local_now = (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
    day = local_now.date()
    rows: list[dict[str, Any]] = []
    with _LOCK:
        snapshot = list(_EVENTS)
    for row in snapshot:
        created = _parse_datetime(row.get("created_at")) or _parse_datetime(row.get("received_at"))
        if created is not None and created.date() == day:
            item = {key: value for key, value in row.items() if key != "payload"}
            item["created_at_local"] = created.strftime("%Y-%m-%d %H:%M:%S")
            rows.append(item)
    rows.sort(key=lambda item: item.get("created_at_local") or "", reverse=True)
    return rows


def _minute_index(value: Any) -> int | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    minute = parsed.hour * 60 + parsed.minute
    morning_start, morning_end = 9 * 60 + 30, 11 * 60 + 30
    afternoon_start, afternoon_end = 13 * 60, 15 * 60
    if morning_start <= minute <= morning_end:
        return minute - morning_start
    if afternoon_start <= minute <= afternoon_end:
        return 121 + minute - afternoon_start
    return None


def _signal_time(row: dict[str, Any]) -> Any:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    for key in ("bar_at", "bar_time", "signal_at", "time", "timestamp"):
        if payload.get(key):
            return payload[key]
    match = _TIME_RE.search(str(row.get("body") or ""))
    return f"{match.group(1)}T{match.group(2)}" if match else row.get("created_at")


def _nearest_price_index(prices: list[float], expected: int, value: Any) -> int:
    if not prices:
        return expected
    try:
        target = float(value)
    except (TypeError, ValueError):
        return expected
    if target <= 0:
        return expected
    lo = max(0, expected - 3)
    hi = min(len(prices), expected + 4)
    if lo >= hi:
        return expected
    nearest = min(range(lo, hi), key=lambda index: abs(prices[index] - target))
    # 只在价格足够接近时校正，避免把别的同价波动误吸过来。
    return nearest if abs(prices[nearest] - target) / target <= 0.005 else expected


def _active_marker_time(marker: dict[str, Any], trade_date: str) -> Any:
    value = (
        marker.get("bar_at")
        or marker.get("bar_time")
        or marker.get("signal_at")
        or marker.get("time")
        or marker.get("timestamp")
    )
    text = str(value or "").strip()
    if re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", text):
        return f"{trade_date}T{text}"
    return value


def _append_point(
    result: list[tuple[int, str]],
    seen: set[tuple[int, str]],
    prices: list[float],
    signal_at: Any,
    side: str,
    price: Any,
) -> None:
    parsed = _parse_datetime(signal_at)
    if parsed is None:
        return
    index = _minute_index(parsed)
    if index is None or index >= len(prices):
        return
    index = _nearest_price_index(prices, index, price)
    key = (index, side)
    if key not in seen:
        seen.add(key)
        result.append(key)


def bs_points(symbol: str, prices: list[float], now: datetime | None = None) -> list[tuple[int, str]]:
    """把当天 MCP B/S 信号映射为当前分时曲线的点索引。

    若已主动拉取 get_volatility_bs，则该整包 markers 是当天权威快照；尚未拉到时才
    退回通知资源缓存，避免“只收到部分通知”导致展开 K 线缺 B/S 点。
    """
    code = _normalize_code(symbol)
    if not code or not prices:
        return []
    local_now = (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
    day = local_now.date()
    result: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()

    with _LOCK:
        active = dict(_VOLATILITY_BS.get(code) or {})
        events = list(_EVENTS)

    if active and str(active.get("trade_date") or "") == day.isoformat():
        for marker in active.get("markers") or []:
            if not isinstance(marker, dict):
                continue
            side = _side(marker, str(marker.get("label") or ""), "")
            if side not in {"B", "S"}:
                continue
            _append_point(
                result,
                seen,
                prices,
                _active_marker_time(marker, day.isoformat()),
                side,
                marker.get("price"),
            )
        result.sort(key=lambda item: item[0])
        return result

    for row in events:
        if row.get("event_type") != BS_EVENT_TYPE or row.get("stock_code") != code:
            continue
        signal_at = _parse_datetime(_signal_time(row))
        if signal_at is None or signal_at.date() != day:
            continue
        side = str(row.get("side") or "").upper()
        if side not in {"B", "S"}:
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        _append_point(result, seen, prices, signal_at, side, payload.get("price"))
    result.sort(key=lambda item: item[0])
    return result


def reset_for_tests() -> None:
    """仅供单元测试清空进程内状态。"""
    global _SOURCE
    with _LOCK:
        _EVENTS.clear()
        _EVENT_IDS.clear()
        _VOLATILITY_BS.clear()
        _SOURCE = None
