"""离线模拟行情：按代码播种的随机游走，不联网、不需要密钥。"""

from __future__ import annotations

import random
import zlib

from .base import Quote


class MockProvider:
    id = "mock"
    label = "模拟数据（离线，无需网络）"
    placeholder = "任意代码，如 600519、DEMO"

    def __init__(self) -> None:
        self._walks: dict[str, dict[str, float]] = {}

    def _walk(self, symbol: str) -> dict[str, float]:
        walk = self._walks.get(symbol)
        if walk is None:
            seed = zlib.crc32(symbol.encode("utf-8")) / 0xFFFFFFFF
            base = round(10 + seed * 290, 2)
            walk = {"prev_close": base, "price": base, "volatility": 0.002 + seed * 0.006}
            self._walks[symbol] = walk
        return walk

    def fetch(self, symbols: list[str]) -> list[Quote]:
        quotes = []
        for symbol in symbols:
            walk = self._walk(symbol)
            drift = (random.random() - 0.5) * 2 * walk["volatility"]
            walk["price"] = max(0.01, round(walk["price"] * (1 + drift), 2))
            quotes.append(
                Quote.from_prices(symbol, symbol, walk["price"], walk["prev_close"])
            )
        return quotes
