"""当日分时数据。

走势图画的是当日分时曲线，而不是「组件运行期间攒下来的几个采样点」——
后者只有开着程序的那段时间，画不出完整的一天。

数据源与字段口径对齐 ``gupiao_ztfx`` 的 ``trading/services/intraday_kline.py``：
东方财富 push2his ``trends2`` 为主，腾讯分钟接口兜底。东财返回形如::

    {"data": {"preClose": 10.0, "trends": ["2026-08-24 09:30,开,收,高,低,量,额,均价", ...]}}
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import requests

from .providers.base import USER_AGENT, describe_error
from .symbols import Symbol, classify

EASTMONEY_ENDPOINT = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
# 东财网页端公开使用的固定 ut 值，不是密钥。
EASTMONEY_UT = "7eea3edcaed734bea9cbfc24409ed989"
TENCENT_ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"

CACHE_TTL_SECONDS = 60  # 分钟级数据，没必要跟着行情每几秒拉一次
REQUEST_TIMEOUT = 8

# A 股标准交易时段，接口偶尔会带上盘前/盘后的点，这里滤掉。
MORNING = ("09:30", "11:30")
AFTERNOON = ("13:00", "15:00")


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
    prev_close: float | None = None
    open_price: float | None = None
    error: str | None = None

    def __bool__(self) -> bool:
        return len(self.prices) >= 2


def _secid(symbol: Symbol) -> str:
    """东财用 1 表示沪市，0 表示深市与北交所。"""
    return f"{1 if symbol.market == 'sh' else 0}.{symbol.code}"


def calculate_bs_points(prices: list[float], reversal_percent: float = 0.005) -> list[tuple[int, str]]:
    """按波动页的反转确认法标记 B/S 点。

    从当前波段极值反向运行达到 0.5% 才确认转折：谷底为 B，峰顶为 S。
    未确认的最后一段不标记，避免实时价格小幅抖动反复产生信号。
    """
    if len(prices) < 3:
        return []
    direction = 0  # 1 上行、-1 下行；先等第一次有效波动确认方向
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


def parse_eastmoney(payload: object) -> Trend:
    """``trends`` 每行是逗号分隔的「时间,开,收,高,低,量,额,均价」，取收盘价。"""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return Trend(error="返回格式异常")

    prices: list[float] = []
    open_price: float | None = None
    for item in data.get("trends") or []:
        parts = str(item or "").split(",")
        if len(parts) < 3:
            continue
        # 时间戳有 "YYYY-MM-DD HH:MM" 和 "YYYY-MM-DD HH:MM:SS" 两种，统一取 HH:MM。
        minute = parts[0].strip().split(" ")[-1][:5]
        close = _number(parts[2])
        if close is not None and close > 0 and _is_trading_minute(minute):
            if open_price is None:
                candidate = _number(parts[1])
                open_price = candidate if candidate is not None and candidate > 0 else close
            prices.append(close)

    return Trend(prices=prices, prev_close=_number(data.get("preClose")), open_price=open_price)


def parse_tencent(payload: object, key: str) -> Trend:
    """腾讯每行是空格分隔的「HHMM 价格 累计量 累计额」。"""
    if not isinstance(payload, dict):
        return Trend(error="返回格式异常")
    block = (((payload.get("data") or {}).get(key) or {}).get("data") or {}).get("data") or []

    prices: list[float] = []
    for line in block:
        parts = str(line or "").split(" ")
        if len(parts) < 2 or len(parts[0]) < 4 or not parts[0].isdigit():
            continue
        minute = f"{parts[0][:2]}:{parts[0][2:4]}"
        price = _number(parts[1])
        if price is not None and price > 0 and _is_trading_minute(minute):
            prices.append(price)

    # 腾讯这个接口没有稳定的昨收字段，留空由调用方用行情里的昨收补上。
    return Trend(prices=prices, open_price=prices[0] if prices else None)


class IntradayClient:
    """带 TTL 缓存的分时数据查询，多个界面元素共用同一份结果。"""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self._cache: dict[str, tuple[float, Trend]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 请求

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
        # 东财没数据或挂了，再试腾讯。
        try:
            fallback = self._fetch_tencent(symbol)
            if fallback:
                return fallback
        except Exception as exc:
            if trend.error is None:
                trend = Trend(error=describe_error(exc))
        return trend

    # ------------------------------------------------------------ 对外

    def fetch(self, raw_symbol: str, now: float | None = None) -> Trend:
        """取一只股票的当日分时；失败时返回带 error 的空 Trend，不抛异常。"""
        symbol = classify(raw_symbol)
        if symbol is None:
            return Trend(error="代码格式不正确")

        now = time.time() if now is None else now
        with self._lock:
            cached = self._cache.get(symbol.key)
            if cached and now - cached[0] < CACHE_TTL_SECONDS:
                return cached[1]

        trend = self._load(symbol)
        with self._lock:
            self._cache[symbol.key] = (now, trend)
        return trend

    def reset_cache(self) -> None:
        """仅供测试。"""
        with self._lock:
            self._cache.clear()
