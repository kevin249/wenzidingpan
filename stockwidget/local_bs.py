"""Notify once for newly confirmed real-time TDW bars on the displayed trading day."""

from datetime import datetime

from .intraday import Trend
from .market_hours import SHANGHAI_TZ
from .providers.base import Quote
from .symbols import classify
from .tdw import EVENT_NAMES

SAMPLE_LEN = 40  # Price-only sparkline fallback.


class LocalBsNotifier:
    def __init__(self) -> None:
        self._frontiers: dict[str, str] = {}
        self._date = ""

    def collect(self, quotes: list[Quote], trends: dict[str, Trend], *, enabled: bool, at: float) -> list[str]:
        now = datetime.fromtimestamp(at, SHANGHAI_TZ)
        day = now.date().isoformat()
        if not enabled or self._date != day:
            self._frontiers.clear()
            self._date = day
        messages = []
        if not enabled:
            return messages
        for quote in quotes:
            trend = trends.get(quote.symbol)
            if quote.error or not trend or trend.stale or trend.trade_date != day:
                continue
            previous = self._frontiers.get(quote.symbol)
            latest = previous or ""
            cutoff = now.strftime("%Y-%m-%d %H:%M:%S")
            for minute in trend.minutes:
                stamp = f"{day} {minute}"
                point = trend.tdw_points.get(stamp)
                if point is None or point.provisional or stamp <= latest or stamp > cutoff:
                    continue
                latest = stamp
                if previous and point.events:
                    parsed = classify(quote.symbol)
                    code = parsed.code if parsed else quote.symbol
                    label = " / ".join(f"{tag} {EVENT_NAMES[tag]}" for tag in point.events)
                    messages.append(
                        f"[{now:%Y-%m-%d %H:%M:%S}] [本地B/S] {quote.name or code}({code}) | TDW {label} | "
                        f"行情时间={minute} | 收盘价={point.close:.2f} | 动力线={point.power:.2f}"
                    )
            if latest:
                self._frontiers[quote.symbol] = latest if previous else max(latest, cutoff)
        return messages
