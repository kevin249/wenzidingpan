"""当日分时数据。

走势图画的是当日分时曲线，而不是「组件运行期间攒下来的几个采样点」。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import requests

from . import mcp_bs
from .providers.base import USER_AGENT, describe_error
from .symbols import Symbol, classify

EASTMONEY_ENDPOINT = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
EASTMONEY_UT = "7eea3edcaed734bea9cbfc24409ed989"
TENCENT_ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"

CACHE_TTL_SECONDS = 1
REQUEST_TIMEOUT = 8
MORNING = ("09:30", "11:30")
AFTERNOON = ("13:00", "15:00")


class PriceSeries(list[float]):
    """携带股票代码的分时价格序列，供 MCP B/S 点映射使用。"""

    def __init__(self, values=(), symbol: str = "") -> None:
        super().__init__(values)
        self.symbol = symbol


def _is_trading_minute(text: str) -> bool:
    return MORNING[0] <= text <= MORNING[1] or AFTERNOON[0] <= text <= AFTERNOON[1]


def _number(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


@dataclass
class Trend:
    """一只股票的当日分时曲线。"""

    prices: list[float] = field(default_factory=list)
    volumes: list[float] = field(default_factory=list)
    prev_close: float | None = None
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    error: str | None = None

    def __bool__(self) -> bool:
        return len(self.prices) >= 2


def _secid(symbol: Symbol) -> str:
    return f"{1 if symbol.market == 'sh' else 0}.{symbol.code}"


def _local_bs_points(prices: list[float], reversal_percent: float = 0.005) -> list[tuple[int, str]]:
    if len(prices) < 3:
        return []
    direction = 0
    extreme_index = 0
    extreme = prices[0]
    low = high = prices[0]
    low_index = high_index = 0
    signals: list[tuple[int, str]] = []
    for index, price in enumerate(prices[1:], 1):
        if direction == 0:
            if price < low:
                low, low_index = price, index
            if price > high:
                high, high_index = price, index
            if low and (price - low) / low >= reversal_percent:
                signals.append((low_index, "B"))
                direction, extreme, extreme_index = 1, price, index
            elif high and (high - price) / high >= reversal_percent:
                signals.append((high_index, "S"))
                direction, extreme, extreme_index = -1, price, index
        elif direction > 0:
            if price >= extreme:
                extreme, extreme_index = price, index
            elif extreme and (extreme - price) / extreme >= reversal_percent:
                signals.append((extreme_index, "S"))
                direction = -1
                extreme, extreme_index = price, index
        else:
            if price <= extreme:
                extreme, extreme_index = price, index
            elif extreme and (price - extreme) / extreme >= reversal_percent:
                signals.append((extreme_index, "B"))
                direction = 1
                extreme, extreme_index = price, index
    return signals


def calculate_bs_points(prices: list[float], reversal_percent: float = 0.005) -> list[tuple[int, str]]:
    """按设置选择 B/S 来源。

    普通 list（含单元测试和采样回退）继续使用本地算法；只有联网分时返回的
    :class:`PriceSeries` 带股票代码，MCP 模式才从当天 MCP 信号映射到曲线。
    """
    symbol = getattr(prices, "symbol", "")
    if symbol and mcp_bs.get_source() == mcp_bs.SOURCE_MCP:
        return mcp_bs.bs_points(symbol, prices)
    return _local_bs_points(prices, reversal_percent)


def parse_eastmoney(payload: object) -> Trend:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return Trend(error="返回格式异常")

    prices: list[float] = []
    volumes: list[float] = []
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    for item in data.get("trends") or []:
        parts = str(item or "").split(",")
        if len(parts) < 3:
            continue
        minute = parts[0].strip().split(" ")[-1][:5]
        close = _number(parts[2])
        if close is not None and close > 0 and _is_trading_minute(minute):
            if open_price is None:
                candidate = _number(parts[1])
                open_price = candidate if candidate is not None and candidate > 0 else close
            high = _number(parts[3]) if len(parts) > 3 else None
            low = _number(parts[4]) if len(parts) > 4 else None
            if high is not None and high > 0:
                high_price = high if high_price is None else max(high_price, high)
            if low is not None and low > 0:
                low_price = low if low_price is None else min(low_price, low)
            prices.append(close)
            volume = _number(parts[5]) if len(parts) > 5 else None
            volumes.append(volume if volume is not None and volume > 0 else 0.0)

    return Trend(
        prices=prices,
        volumes=volumes,
        prev_close=_number(data.get("preClose")),
        open_price=open_price,
        high_price=high_price if high_price is not None else (max(prices) if prices else None),
        low_price=low_price if low_price is not None else (min(prices) if prices else None),
    )


def parse_tencent(payload: object, key: str) -> Trend:
    if not isinstance(payload, dict):
        return Trend(error="返回格式异常")
    block = (((payload.get("data") or {}).get(key) or {}).get("data") or {}).get("data") or []

    prices: list[float] = []
    volumes: list[float] = []
    prev_volume = 0.0
    for line in block:
        parts = str(line or "").split(" ")
        if len(parts) < 2 or len(parts[0]) < 4 or not parts[0].isdigit():
            continue
        minute = f"{parts[0][:2]}:{parts[0][2:4]}"
        price = _number(parts[1])
        if price is not None and price > 0 and _is_trading_minute(minute):
            prices.append(price)
            cumulative = _number(parts[2]) if len(parts) > 2 else None
            if cumulative is not None and cumulative > 0:
                volume = max(cumulative - prev_volume, 0.0)
                prev_volume = cumulative
            else:
                volume = 0.0
            volumes.append(volume)

    return Trend(
        prices=prices,
        volumes=volumes,
        open_price=prices[0] if prices else None,
        high_price=max(prices) if prices else None,
        low_price=min(prices) if prices else None,
    )


class IntradayClient:
    """带 TTL 缓存的分时数据查询，多个界面元素共用同一份结果。"""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self._cache: dict[str, tuple[float, Trend]] = {}
        self._lock = threading.Lock()

    def _fetch_eastmoney(self, symbol: Symbol) -> Trend:
        response = self.session.get(
            EASTMONEY_ENDPOINT,
            params={
                "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                "ut": EASTMONEY_UT,
                "ndays": 1,
                "iscr": 0,
                "secid": _secid(symbol),
            },
            headers={"User-Agent": USER_AGENT, "Referer": "https://quote.eastmoney.com/"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return parse_eastmoney(response.json())

    def _fetch_tencent(self, symbol: Symbol) -> Trend:
        response = self.session.get(
            TENCENT_ENDPOINT,
            params={"code": symbol.key},
            headers={"User-Agent": USER_AGENT, "Referer": "https://gu.qq.com/"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return parse_tencent(response.json(), symbol.key)

    def _load(self, symbol: Symbol) -> Trend:
        try:
            trend = self._fetch_eastmoney(symbol)
            if trend:
                return trend
        except Exception as exc:
            trend = Trend(error=describe_error(exc))
        try:
            fallback = self._fetch_tencent(symbol)
            if fallback:
                return fallback
        except Exception as exc:
            if trend.error is None:
                trend = Trend(error=describe_error(exc))
        return trend

    def fetch(self, raw_symbol: str, now: float | None = None) -> Trend:
        symbol = classify(raw_symbol)
        if symbol is None:
            return Trend(error="代码格式不正确")

        now = time.time() if now is None else now
        with self._lock:
            cached = self._cache.get(symbol.key)
            if cached and now - cached[0] < CACHE_TTL_SECONDS:
                return cached[1]

        trend = self._load(symbol)
        trend.prices = PriceSeries(trend.prices, symbol.code)
        with self._lock:
            self._cache[symbol.key] = (now, trend)
        return trend

    def reset_cache(self) -> None:
        with self._lock:
            self._cache.clear()
