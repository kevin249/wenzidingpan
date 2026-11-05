"""Build local TDW bars from timestamped Eastmoney / Tencent transaction reports."""

from __future__ import annotations

import json
import math
import re
from bisect import bisect_left
from dataclasses import replace
from datetime import datetime, timedelta

import requests

from . import tdw
from .intraday import PriceSeries, Trend, _trade_date
from .kline import Bar
from .market_hours import SHANGHAI_TZ, is_a_share_active_time
from .providers.base import REQUEST_TIMEOUT, USER_AGENT
from .symbols import classify

EASTMONEY = "https://push2.eastmoney.com/api/qt/stock"
TENCENT = "https://stock.gtimg.cn/data/index.php"
CLOCK = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d")


def _session(clock: str) -> bool:
    # The closing print can be timestamped 11:30:01 or 15:00:01 by the vendor.
    return "09:30" <= clock[:5] <= "11:30" or "13:00" <= clock[:5] <= "15:00"


def second_bars(day, trades) -> list[Bar]:
    """Trades are supplied in source order. Equal prices/volumes can be distinct trades."""
    bars: list[Bar] = []
    for clock, raw_price, raw_volume in trades:
        if not CLOCK.fullmatch(clock) or not _session(clock):
            continue
        try:
            price, volume = float(raw_price), float(raw_volume)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(price) and math.isfinite(volume) and price > 0 and volume > 0):
            continue
        stamp = f"{day} {clock}"
        if bars and bars[-1].time > stamp:
            raise ValueError("成交明细时间乱序")
        if bars and bars[-1].time == stamp:
            last = bars[-1]
            bars[-1] = Bar(stamp, last.open, max(last.high, price), min(last.low, price),
                           price, last.volume + volume)
        else:
            bars.append(Bar(stamp, price, price, price, price, volume))
    return bars


def _tx_value(text, key):
    name, _, data = text.strip().rstrip(";").partition("=")
    if name != key:
        raise ValueError("腾讯成交明细代码不匹配")
    first, second = json.loads(data)
    return first, second.split("|")


class RealtimeFeed:
    """One serial worker per symbol; never combine OHLC or EMA state across sources."""

    def __init__(self, symbol: str, source: str, session=None):
        self.symbol = classify(symbol)
        self.sources = (source,) if source in ("eastmoney", "tencent") else ("eastmoney", "tencent")
        self.session = session or requests.Session()
        self._em_day = ""
        self._tx_pages: dict[int, tuple[str, list[str]]] = {}
        self._tx_day = ""
        self._provider = ""
        self._bars: list[Bar] = []
        self._trend: Trend | None = None
        self._forming = ""
        self._next_probe = 0.0

    def _get(self, url, params, referer):
        response = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT,
                                    headers={"User-Agent": USER_AGENT, "Referer": referer})
        response.raise_for_status()
        return response

    def _eastmoney(self, at, warm):
        market = int(self.symbol.market == "sh")
        params = {"secid": f"{market}.{self.symbol.code}"}
        referer = "https://quote.eastmoney.com/"
        if self._em_day != at.date().isoformat():
            meta = self._get(EASTMONEY + "/get", {**params, "fltt": 2, "fields": "f57,f86"}, referer)
            quote = meta.json()["data"]
            if quote["f57"] != self.symbol.code or int(quote["f86"]) <= 0:
                raise ValueError("东财行情日期不可用")
            self._em_day = datetime.fromtimestamp(int(quote["f86"]), SHANGHAI_TZ).date().isoformat()
        count = 5000 if warm else 120
        response = self._get(EASTMONEY + "/details/get", {
            **params, "fields1": "f1,f2,f3,f4,f5", "fields2": "f51,f52,f53,f54,f55", "pos": -count,
        }, referer)
        data = response.json()["data"]
        if data["code"] != self.symbol.code or int(data["market"]) != market:
            raise ValueError("东财成交明细代码不匹配")
        rows = [line.split(",") for line in data["details"]]
        # A bounded tail can start part-way through a second. Keep our existing complete bar.
        partial = rows[0][0] if len(rows) >= count else ""
        trades = ((r[0], r[1], r[2]) for r in rows
                  if len(r) >= 5 and r[0] != partial and r[4] in {"1", "2", "4"} and r[3] != "0")
        return second_bars(self._em_day, trades)

    def _tencent(self, at, warm):
        key = self.symbol.key
        params = {"appn": "detail", "c": key}
        referer = "https://gu.qq.com/"
        index = self._get(TENCENT, {**params, "action": "t"}, referer)
        date_id, ranges = _tx_value(index.text, "v_detail_time_" + key)
        day = _trade_date(date_id)
        if not day:
            raise ValueError("腾讯成交明细日期不可用")
        if day != self._tx_day:
            self._tx_pages.clear()
            self._tx_day = day
        pages = []
        for number, span in enumerate(ranges):
            first, _, last = span.partition("~")
            if not (CLOCK.fullmatch(first) and CLOCK.fullmatch(last) and first <= last):
                raise ValueError("腾讯成交时间段无效")
            if first[:5] <= "15:00" and last[:5] >= "09:30":
                pages.append(number)
        rows = []
        selected = pages[-(4 if warm else 2):]
        for n in selected:
            cached = self._tx_pages.get(n)
            if n == len(ranges) - 1 or not cached or cached[0] != ranges[n]:
                page = self._get(TENCENT, {**params, "action": "data", "p": n}, referer)
                page_number, lines = _tx_value(page.text, "v_detail_data_" + key)
                if page_number != n or not lines:
                    raise ValueError("腾讯成交页不匹配")
                first, last = ranges[n].split("~")
                if lines[0].split("/")[1] != first or lines[-1].split("/")[1] < last:
                    raise ValueError("腾讯成交页时间不匹配")
                self._tx_pages[n] = ranges[n], lines
            rows.extend(self._tx_pages[n][1])
        records = [line.split("/") for line in rows]
        # Do not count a partial second at the oldest page boundary.
        partial = records[0][1] if records and selected[0] > 0 else ""
        trades = ((r[1], r[2], r[4]) for r in records if len(r) >= 7 and r[1] != partial)
        return second_bars(day, trades)

    def fetch(self, *, now=None) -> Trend | None:
        if self.symbol is None:
            return None
        at = (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
        sources = sorted(self.sources, key=lambda name: name != self._provider)
        candidate = None
        for source in sources:
            load = self._eastmoney if source == "eastmoney" else self._tencent
            try:
                warm = (source != self._provider or not self._bars
                        or self._bars[-1].time[:10] != at.date().isoformat())
                bars = load(at, warm)
                if not warm and (not bars or bars[0].time > self._bars[-1].time):
                    # We missed more than the overlap or only received after-hours records.
                    bars = load(at, True)
                if candidate is not None and (not bars or bars[-1].time <= candidate.realtime_bars[-1].time):
                    continue
                observed = at if now is not None else datetime.now(SHANGHAI_TZ)
                trend = self._merge(source, bars, observed)
                if trend is None:
                    continue
                if (source == sources[0] and len(sources) > 1 and is_a_share_active_time(observed)
                        and observed.timestamp() >= self._next_probe
                        and bars[-1].time < (observed - timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")):
                    # A quiet stock and a frozen vendor look alike. Prefer a newer report if available.
                    self._next_probe = observed.timestamp() + 30
                    candidate = trend
                    continue
                return trend
            except (requests.RequestException, ValueError, TypeError, KeyError, IndexError, OverflowError, OSError):
                continue
        return candidate

    def _merge(self, source: str, bars: list[Bar], at: datetime) -> Trend | None:
        limit = at.strftime("%Y-%m-%d %H:%M:%S")
        if not bars or bars[-1].time > limit:
            return None
        day = bars[-1].time[:10]
        if day != limit[:10] and is_a_share_active_time(at):
            return None
        if self._bars:
            previous, latest = self._bars[-1], bars[-1]
            if latest.time < previous.time:
                return None
            if source == self._provider and latest.time == previous.time and latest.volume < previous.volume:
                return None
        continuous = (source == self._provider and self._bars and
                      day == self._bars[-1].time[:10] and bars[0].time <= self._bars[-1].time)
        start = bisect_left(self._bars, bars[0].time, key=lambda bar: bar.time) if continuous else 0
        # A new source second confirms the preceding bar. At lunch or close, the final
        # successful fetch confirms the last reported second, even on a quiet stock.
        same_session = (bars[-1].time[11:13] < "12") == (at.hour < 12)
        forming = bars[-1].time if same_session and is_a_share_active_time(at) and _session(limit[11:]) else ""
        if continuous and self._forming == forming and self._bars[start:] == bars:
            return self._trend
        self._bars = self._bars[:start] + bars
        self._provider, self._forming = source, forming
        self._trend = Trend(
            prices=PriceSeries((bar.close for bar in self._bars), self.symbol.code),
            minutes=[bar.time[11:] for bar in self._bars], volumes=[bar.volume for bar in self._bars],
            high_price=max(bar.high for bar in self._bars), low_price=min(bar.low for bar in self._bars),
            open_price=self._bars[0].open if self._bars[0].time[11:16] == "09:30" else None,
            source=source, trade_date=day, fetched_at=at.timestamp(), realtime_bars=self._bars,
            tdw_points=tdw.calculate(self._bars, tdw.PERIOD_REALTIME, at, forming=forming),
        )
        return self._trend


def with_price_history(live: Trend, history: Trend | None) -> Trend:
    """Use earlier chart prices for context. Only transaction bars carry a TDW result."""
    if not history or not live.minutes or live.trade_date != history.trade_date:
        return live
    first = bisect_left(history.minutes, live.minutes[0][:5])
    highs = [p for p in (history.high_price, live.high_price) if p is not None]
    lows = [p for p in (history.low_price, live.low_price) if p is not None]
    prices = PriceSeries(history.prices[:first] + live.prices, getattr(live.prices, "symbol", ""))
    volumes = history.volumes[:first] + live.volumes if len(history.volumes) >= first else []
    return replace(live, prices=prices, volumes=volumes, minutes=history.minutes[:first] + live.minutes,
                   average_prices=[point for point in history.average_prices if point[0] <= live.minutes[-1][:5]],
                   prev_close=history.prev_close, open_price=history.open_price or live.open_price,
                   high_price=max(highs, default=None), low_price=min(lows, default=None))
