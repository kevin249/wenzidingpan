"""分时横轴：完整 A 股交易日，或按当前数据的时间范围铺满。"""

from __future__ import annotations

from collections.abc import Sequence

SESSION_MINUTES = 240  # 09:30–11:30、13:00–15:00，压缩午休
GRID_STEP_MINUTES = 30


def trading_minute(value: str) -> float | None:
    """HH:MM → 从开盘起的交易分钟，盘前/午休/盘后夹到最近交易边界。"""
    try:
        hour, minute = map(int, value.split(":"))
    except (AttributeError, TypeError, ValueError):
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    clock = hour * 60 + minute
    if clock <= 11 * 60 + 30:
        return float(max(0, clock - (9 * 60 + 30)))
    return float(120 + min(120, max(0, clock - 13 * 60)))


def intraday_fraction(
    index: int, count: int, minutes: Sequence[str] = (), *, fill_width: bool = False
) -> float:
    """数据点在横轴上的比例；缺少时间时按开盘起的分钟序号兜底。"""
    if count <= 0:
        return 0.0
    index = max(0, min(index, count - 1))
    if len(minutes) == count:
        offset = trading_minute(minutes[index])
        start, end = trading_minute(minutes[0]), trading_minute(minutes[-1])
        if offset is not None:
            if not fill_width:
                return offset / SESSION_MINUTES
            if start is not None and end is not None and end > start:
                return max(0.0, min(1.0, (offset - start) / (end - start)))
    denominator = max(1 if fill_width else SESSION_MINUTES, count - 1)
    return index / denominator


def intraday_grid_fractions(
    count: int, minutes: Sequence[str] = (), *, fill_width: bool = False
) -> list[float]:
    """当前横轴内的半小时网格及两端边界，避免铺满模式仍使用全天刻度。"""
    start, end = 0.0, float(SESSION_MINUTES)
    if fill_width:
        if count < 2:
            return []
        if len(minutes) == count:
            start, end = trading_minute(minutes[0]), trading_minute(minutes[-1])
        else:
            end = float(min(SESSION_MINUTES, count - 1))
        if start is None or end is None or end <= start:
            return []
    ticks = [
        (minute - start) / (end - start)
        for minute in range(0, SESSION_MINUTES + 1, GRID_STEP_MINUTES)
        if start < minute < end
    ]
    return [0.0, *ticks, 1.0]
