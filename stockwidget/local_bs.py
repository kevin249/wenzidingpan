"""本地 B/S 新信号检测；只消费已有行情，不联网，也不修改反转算法。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime

from .intraday import Trend, _local_bs_points
from .market_hours import SHANGHAI_TZ
from .providers.base import Quote
from .symbols import classify

# 与分时图的报价采样回退保持相同窗口长度。
SAMPLE_LEN = 40


@dataclass
class _StockState:
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=SAMPLE_LEN))
    sample_count: int = 0
    mode: str = ""
    size: int = 0
    seen: set[tuple[int, str]] = field(default_factory=set)


class LocalBsNotifier:
    """按股票、上海日期、数据模式和转折点去重；首次有效数据只建基线。"""

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
        if not enabled or self._day != now.date():
            self._stocks.clear()
            self._day = now.date()
        if not enabled:
            return []

        messages: list[str] = []
        wanted: set[str] = set()
        for quote in quotes:
            parsed = classify(quote.symbol)
            code = parsed.key if parsed else quote.symbol
            if code in wanted:
                continue
            wanted.add(code)
            state = self._stocks.setdefault(code, _StockState())
            if quote.error:
                state.samples.clear()
                state.sample_count = 0
                if state.mode == "sample":
                    state.mode = ""
                continue
            if quote.price is not None:
                state.samples.append(quote.price)
                state.sample_count += 1

            trend = trends.get(quote.symbol)
            has_trend = bool(trend and not trend.error)
            mode = "intraday" if has_trend else "sample"
            prices = trend.prices if has_trend else list(state.samples)
            if len(prices) < 2:
                continue
            offset = 0 if has_trend else state.sample_count - len(prices)
            points = _local_bs_points(prices)
            keys = {(index + offset, side) for index, side in points}
            baseline = state.mode != mode or len(prices) < state.size
            if baseline:
                state.seen = keys.copy()
            else:
                # 滚动采样窗口的左端移出会重算初始方向；只提示最后一次采样
                # 真正新确认的点，不能把窗口移动造成的历史重排当作新信号。
                prior = set(_local_bs_points(prices[:-1])) if mode == "sample" else set()
                for index, side in points:
                    if (index + offset, side) in state.seen or (index, side) in prior:
                        continue
                    source = "分时" if has_trend else "采样"
                    symbol = parsed.code if parsed else quote.symbol
                    messages.append(
                        f"[{now:%Y-%m-%d %H:%M:%S}] [本地B/S] "
                        f"{quote.name or symbol}({symbol}) | {side} | "
                        f"转折价={prices[index]:.2f} | 最新{source}价={prices[-1]:.2f} | "
                        f"{source}点={index + offset + 1} | 反转阈值=0.50%"
                    )
                state.seen.update(keys)
            # 采样点使用递增编号，滚动窗口移动时不重复报；缓存不随运行时间增长。
            if mode == "sample":
                state.seen = {key for key in state.seen if key[0] >= offset}
            state.mode, state.size = mode, len(prices)

        self._stocks = {code: state for code, state in self._stocks.items() if code in wanted}
        return messages
