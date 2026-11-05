"""Local TDW (逃顶王), matching gupiao_ztfx formula tdx-251."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from .kline import Bar, PERIOD_DAY, PERIOD_1M, KlineClient
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


def forming_minute(now: datetime) -> str:
    """TDX minute bars use the closing timestamp, skipping lunch and non-trading hours."""
    now = now.astimezone(SHANGHAI_TZ)
    hm = now.strftime("%H:%M")
    if now.weekday() < 5 and ("09:30" <= hm < "11:30" or "13:00" <= hm < "15:00"):
        return (now + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    return ""


def calculate(bars: list[Bar], period: str, as_of: datetime, *, live: bool = False) -> dict[str, Point]:
    """Evaluate ordered OHLC bars; optionally include the single current forming minute."""
    now = as_of.astimezone(SHANGHAI_TZ)
    cutoff = now.strftime("%Y-%m-%d %H:%M")
    forming = forming_minute(now) if live and period == PERIOD_1M else ""
    lows: deque[float] = deque(maxlen=10)
    highs: deque[float] = deque(maxlen=25)
    previous = None
    old_weight = 1.0
    last_event: dict[str, int] = {}
    points: dict[str, Point] = {}
    for index, bar in enumerate(bars):
        stamp = bar.time[:10] + " 15:00" if period == PERIOD_DAY else bar.time[:16]
        if stamp > cutoff and stamp != forming:
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
                crossed = previous <= level < power if tag.startswith("B") else previous >= level > power
                if crossed and index - last_event.get(tag, -21) > 20:
                    events.append(tag)
                    last_event[tag] = index
        if len(highs) == 25 and power is not None:
            key = stamp[:10] if period == PERIOD_DAY else stamp
            points[key] = Point(power, bar.close, tuple(events), stamp == forming, bar.volume)
        previous = power
    return points


class MinuteFeed:
    """One worker per stock. Replace revisions of the tail, preserving EMA/FILTER history."""

    def __init__(self, symbol: str, source: str):
        self.symbol = symbol
        self.client = KlineClient(source=source)
        self.bars: list[Bar] = []
        self.provider = ""
        self._points: dict[str, Point] = {}
        self._cutoff = ""
        self._observed = 0.0

    def fetch(self, chart=None, *, now: datetime | None = None) -> dict[str, Point] | None:
        at = now or datetime.now(SHANGHAI_TZ)
        forming = forming_minute(at)
        # Reuse a fresh OHLC snapshot from the same chart provider within this bar.
        live_bar = chart.last_minute_bar if chart and not chart.stale else None
        if (forming and live_bar and self.bars and self.provider == chart.source
                and chart.fetched_at > self._observed
                and self.bars[-1].time == live_bar.time == forming):
            incoming = [live_bar]
            self._observed = chart.fetched_at
        else:
            count = 5 if self.bars else 500
            result = self.client.fetch(self.symbol, PERIOD_1M, count, adjust=False, fresh=True)
            if not result or result.error:
                return None
            if self.bars and (self.provider != result.source or
                              result.bars[0].time > self.bars[-1].time):
                # A source switch or a gap larger than the tail needs a fresh warm-up.
                result = self.client.fetch(self.symbol, PERIOD_1M, 500, adjust=False, fresh=True)
                if not result or result.error:
                    return None
                self.bars = []
            self.provider = result.source
            self._observed = at.timestamp()
            incoming = result.bars
            if (forming and live_bar and chart.source == self.provider
                    and incoming[-1].time == at.strftime("%Y-%m-%d %H:%M") and live_bar.time == forming):
                incoming = [*incoming, live_bar]
        limit = forming or at.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")
        incoming = [bar for bar in incoming if bar.time[:16] <= limit]
        if not incoming:
            return None
        # Ignore a delayed packet ending before the most recent observed bar.
        if self.bars and incoming[-1].time < self.bars[-1].time:
            return self._points
        start = len(self.bars)
        while start and self.bars[start - 1].time >= incoming[0].time:
            start -= 1
        cutoff = at.strftime("%Y-%m-%d %H:%M")
        if self._cutoff == cutoff and self.bars[start:] == incoming:
            return self._points
        old_day = self.bars[-1].time[:10] if self.bars else ""
        prefix = self.bars[:start]
        if old_day != incoming[-1].time[:10]:
            prefix = prefix[-500:]
        self.bars = prefix + incoming
        self._cutoff = cutoff
        points = calculate(self.bars, PERIOD_1M, at, live=True)
        day = self.bars[-1].time[:10]
        self._points = {stamp: point for stamp, point in points.items() if stamp.startswith(day)}
        return self._points


def align(points: dict[str, Point], stamps) -> tuple[list[float | None], list[tuple[int, str]]]:
    if not points:
        return [], []
    power = []
    signals = []
    for index, stamp in enumerate(stamps):
        point = points.get(stamp)
        power.append(point.power if point else None)
        if point and point.events:
            # Two crossings of the same direction can fall on one bar.
            signals.append((index, "/".join(point.events) + ("*" if point.provisional else "")))
    return (power if any(value is not None for value in power) else []), signals
