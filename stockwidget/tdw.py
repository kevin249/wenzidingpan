"""Local TDW (逃顶王) power line and desktop alert rules."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from .kline import Bar, PERIOD_DAY
from .market_hours import SHANGHAI_TZ


@dataclass(frozen=True)
class Point:
    power: float
    close: float
    events: tuple[str, ...]
    provisional: bool = False
    volume: float = 0.0


# Each DRAWICON in the source has its own FILTER(..., 20) state.
LEVELS = (("B1", 0.2), ("B2", 0.5), ("S1", 3.2), ("S2", 3.5))
EVENT_NAMES = {"B1": "底部", "B2": "关注", "S1": "阶段卖出", "S2": "清仓卖出"}


PERIOD_REALTIME = "realtime"


def calculate(bars: list[Bar], period: str, as_of: datetime, *, forming: str = "",
              partial_windows: bool = False) -> dict[str, Point]:
    """Evaluate each supplied OHLC bar once, retaining the provider's time resolution."""
    now = as_of.astimezone(SHANGHAI_TZ)
    precision = 19 if period == PERIOD_REALTIME else 16
    cutoff = now.strftime("%Y-%m-%d %H:%M:%S")[:precision]
    lows: deque[float] = deque(maxlen=10)
    highs: deque[float] = deque(maxlen=25)
    previous = None
    old_weight = 1.0
    last_event: dict[str, int] = {}
    points: dict[str, Point] = {}
    for index, bar in enumerate(bars):
        stamp = bar.time[:10] + " 15:00" if period == PERIOD_DAY else bar.time[:precision]
        if stamp > cutoff:
            break
        if not (0 < bar.low <= bar.close <= bar.high and math.isfinite(bar.high)):
            # Do not bridge broken bars with a fabricated crossing.
            previous, old_weight = None, 1.0
            lows.clear()
            highs.clear()
            continue
        lows.append(bar.low)
        highs.append(bar.high)
        low, high = min(lows), max(highs)
        if high == low:
            # pandas ewm(adjust=False, ignore_na=False) carries the last EMA,
            # decaying its weight for the next finite observation.
            old_weight *= 0.6
            power = previous
        else:
            value = 4 * (bar.close - low) / (high - low)
            weight = old_weight * 0.6
            power = value if previous is None else (weight * previous + 0.4 * value) / (weight + 0.4)
            old_weight = 1.0
        events = []
        if previous is not None and power is not None:
            for tag, level in LEVELS:
                crossed = (
                    previous <= level < power if tag in ("B2", "S2")
                    else previous >= level > power
                )
                if crossed and index - last_event.get(tag, -21) > 20:
                    events.append(tag)
                    last_event[tag] = index
        # The phone's session chart starts with the bars available at the open.
        if power is not None and (partial_windows or len(highs) == highs.maxlen):
            key = stamp[:10] if period == PERIOD_DAY else stamp
            points[key] = Point(power, bar.close, tuple(events), stamp == forming, bar.volume)
        previous = power
    return points


def align(points: dict[str, Point], stamps) -> tuple[list[float | None], list[tuple[int, str]]]:
    if not points:
        return [], []
    power = []
    signals = []
    for index, stamp in enumerate(stamps):
        point = points.get(stamp)
        power.append(point.power if point else None)
        if point and point.events:
            # Several thresholds can be crossed on one bar.
            signals.append((index, "/".join(point.events) + ("*" if point.provisional else "")))
    return (power if any(value is not None for value in power) else []), signals
