"""后台轮询线程：拉行情 + 合并暗盘资金，通过信号送回界面线程。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QThread, Signal

from . import providers
from .config import Config
from .darktrade import DarkTradeClient
from .providers.base import Quote
from .symbols import classify


@dataclass
class Snapshot:
    """一次轮询的完整结果。"""

    provider_id: str
    quotes: list[Quote]
    at: float = field(default_factory=time.time)
    dark_date: str = ""
    dark_error: str | None = None
    dark_enabled: bool = False


class Poller(QThread):
    snapshot_ready = Signal(object)

    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._dark = DarkTradeClient()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 控制

    def apply_config(self, config: Config) -> None:
        with self._lock:
            self._config = config
        self.refresh_now()

    def refresh_now(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()

    # ------------------------------------------------------------ 循环

    def run(self) -> None:  # noqa: D102 - QThread 入口
        while not self._stopping.is_set():
            with self._lock:
                config = self._config
            self.snapshot_ready.emit(self._tick(config))
            self._wake.wait(config.refresh_seconds)
            self._wake.clear()

    def _tick(self, config: Config) -> Snapshot:
        provider = providers.resolve(config.provider)
        try:
            quotes = provider.fetch(list(config.symbols))
        except Exception as exc:  # 数据源整体挂掉时也要出一屏，让用户看到原因
            quotes = [Quote.failed(symbol, str(exc)[:60]) for symbol in config.symbols]

        snapshot = Snapshot(provider_id=provider.id, quotes=quotes, dark_enabled=config.show_dark_trade)
        if config.show_dark_trade:
            self._attach_dark_trade(quotes, snapshot)
        return snapshot

    def _attach_dark_trade(self, quotes: list[Quote], snapshot: Snapshot) -> None:
        """暗盘是日频数据，客户端内部有缓存；拉不到只留空，不影响行情。"""
        codes = {symbol.code for q in quotes if (symbol := classify(q.symbol))}
        if not codes:
            return
        result = self._dark.fetch(codes)
        snapshot.dark_date = result.trade_date
        snapshot.dark_error = result.error
        for quote in quotes:
            symbol = classify(quote.symbol)
            row = result.by_code.get(symbol.code) if symbol else None
            if row is not None:
                quote.dark_fund = row.dark_fund
                quote.dark_main_net_inflow = row.main_net_inflow
