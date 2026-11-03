"""本地 B/S 新信号检测；只消费已有行情，不联网，也不修改反转算法。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime

from .intraday import Trend, local_bs_signals
from .market_hours import SHANGHAI_TZ
from .providers.base import Quote
from .symbols import classify

SAMPLE_LEN = 40


@dataclass
class _SignalHistory:
    seen: set[tuple[int, str]] = field(default_factory=set)
    # 已观察到的最晚行情位置。短包、历史补齐、临时缺报价都不能让它后退。
    frontier: int = -1


@dataclass
class _StockState:
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=SAMPLE_LEN))
    sample_count: int = 0
    stream: tuple[str, str] | None = None
    histories: dict[str, _SignalHistory] = field(default_factory=dict)


def _minute_ids(trend: Trend) -> list[int] | None:
    if len(trend.minutes) != len(trend.prices):
        return None
    try:
        parts = [tuple(map(int, minute.split(":"))) for minute in trend.minutes]
        if any(len(p) != 2 or not (0 <= p[0] < 24 and 0 <= p[1] < 60) for p in parts):
            return None
        result = [hour * 60 + minute for hour, minute in parts]
    except (ValueError, TypeError, AttributeError):
        return None
    return result if all(a < b for a, b in zip(result, result[1:])) else None


class LocalBsNotifier:
    """按真实转折分钟去重，只提示上次观察位置之后新确认的信号。"""

    def __init__(self) -> None:
        self._day: date | None = None
        self._stocks: dict[str, _StockState] = {}

    def collect(
        self,
        quotes: list[Quote],
        trends: dict[str, Trend],
        *,
        enabled: bool,
        at: float,
    ) -> list[str]:
        now = datetime.fromtimestamp(at, SHANGHAI_TZ)
        if self._day != now.date():
            self._stocks.clear()
            self._day = now.date()
        if not enabled:
            for state in self._stocks.values():
                state.stream = None
                state.samples.clear()
            return []

        messages: list[str] = []
        handled: set[str] = set()
        for quote in quotes:
            parsed = classify(quote.symbol)
            code = parsed.key if parsed else quote.symbol
            if code in handled:
                continue
            handled.add(code)
            state = self._stocks.setdefault(code, _StockState())
            if quote.error:
                state.samples.clear()
                if state.stream and state.stream[0] == "sample":
                    state.stream = None
                continue
            if quote.price is not None:
                state.samples.append(quote.price)
                state.sample_count += 1

            trend = trends.get(quote.symbol)
            has_trend = bool(trend and not trend.error)
            prices = trend.prices if has_trend else list(state.samples)
            if len(prices) < 2:
                continue
            if has_trend:
                if trend.trade_date and trend.trade_date != now.date().isoformat():
                    # 昨日/错误日期的分时可供图表显示，但不能当作今日新信号。
                    continue
                minutes = _minute_ids(trend)
                if trend.minutes and minutes is None:
                    # 时间轴不完整或无序时不能可靠判断新旧，等待有效快照。
                    continue
                positions = minutes if minutes is not None else list(range(len(prices)))
                kind = "minute" if minutes is not None else "index"
                stream = (kind, trend.source)
            else:
                offset = state.sample_count - len(prices)
                positions = list(range(offset, state.sample_count))
                kind, stream = "sample", ("sample", "")

            history = state.histories.setdefault(kind, _SignalHistory())
            baseline = state.stream != stream or history.frontier < 0
            signals = local_bs_signals(prices)
            # 滚动采样只允许末次采样新确认的点，避免左端移出导致历史重排。
            prior = (
                {(s.index, s.side) for s in local_bs_signals(prices[:-1])}
                if kind == "sample" else set()
            )
            for signal in signals:
                index, side, confirmed = signal.index, signal.side, signal.confirmed_index
                key = (positions[index], side)
                if (
                    not baseline
                    and key not in history.seen
                    and positions[confirmed] >= history.frontier
                    and (index, side) not in prior
                ):
                    source = "分时" if has_trend else "采样"
                    provider = (trend.source or "未知") if has_trend else "报价采样"
                    symbol = parsed.code if parsed else quote.symbol
                    timing = (
                        f"转折时间={trend.minutes[index]} | 确认时间={trend.minutes[confirmed]}"
                        if kind == "minute" else
                        f"转折{source}点={positions[index] + 1} | 确认{source}点={positions[confirmed] + 1}"
                    )
                    messages.append(
                        f"[{now:%Y-%m-%d %H:%M:%S}] [本地B/S] "
                        f"{quote.name or symbol}({symbol}) | {side} | 周期={source} | {timing} | "
                        f"转折价={prices[index]:.2f} | 确认价={prices[confirmed]:.2f} | "
                        f"最新{source}价={prices[-1]:.2f} | "
                        f"{source}点={index + 1 if has_trend else positions[index] + 1} | "
                        f"数据源={provider} | 反转阈值=0.50%"
                    )
                # 无论是否提示都累积历史，不因短包/切源覆盖已见集合。
                history.seen.add(key)
            history.frontier = max(history.frontier, positions[-1])
            if baseline and kind == "minute":
                # 首帧可能只含部分历史：后续补齐启动前的数据也不能补发。
                history.frontier = max(history.frontier, now.hour * 60 + now.minute)
            if kind == "sample":
                history.seen = {key for key in history.seen if key[0] >= positions[0]}
            state.stream = stream

        # 某轮没有某只股票不代表用户删除了它；保留当天历史，跨日统一释放。
        return messages
