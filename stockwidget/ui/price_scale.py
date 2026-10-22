"""价格纵轴的最小窗口与缩放滞回，避免临界价附近反复拉伸。"""

from __future__ import annotations

from datetime import datetime

from ..market_hours import SHANGHAI_TZ

PADDING = 1.10  # 扩容时多留 10%，不追着每一笔价格缩放。
SHRINK_RATIO = 0.70  # 数据占用不足原窗口 70% 才重新收紧。
MIN_HALF_RATIO = 0.0005  # 总窗口至少为价格的 0.1%。
MIN_HALF_PRICE = 0.01  # 低价股也至少留出上下各一分钱。


class PriceScale:
    def __init__(self) -> None:
        self._bounds: tuple[float, float] | None = None
        self._session = None

    def reset(self) -> None:
        self._bounds = None
        self._session = None

    def bounds(
        self, low: float, high: float, *, center: float | None = None
    ) -> tuple[float, float]:
        """返回稳定的价格窗口；指定 center 时始终以它为纵轴中点。

        窗口不足时立即扩容，明显富余时才收紧；小幅往返保留原尺度。
        最小跨度始终生效，不再在「全平」与「差一分钱」之间换算法。
        """
        session = datetime.now(SHANGHAI_TZ).date()
        if session != self._session:
            self.reset()
            self._session = session

        midpoint = (low + high) / 2.0 if center is None else center
        half = max(
            midpoint - low,
            high - midpoint,
            abs(midpoint) * MIN_HALF_RATIO,
            MIN_HALF_PRICE,
        )
        if self._bounds is not None:
            old_low, old_high = self._bounds
            old_half = (old_high - old_low) / 2.0
            if center is None:
                # 普通分时还需锁住窗口位置，避免曲线随自动居中上下晃动。
                if old_low <= low and high <= old_high and half >= old_half * SHRINK_RATIO:
                    return self._bounds
            elif old_half * SHRINK_RATIO <= half <= old_half:
                self._bounds = (midpoint - old_half, midpoint + old_half)
                return self._bounds

        half *= PADDING
        self._bounds = (midpoint - half, midpoint + half)
        return self._bounds
