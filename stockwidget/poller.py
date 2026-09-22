"""后台轮询线程：拉行情 + 合并暗盘资金，通过信号送回界面线程。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QThread, Signal

from . import providers
from .config import Config
from .darktrade import DarkTradeClient
from .intraday import IntradayClient, Trend
from .providers.base import Quote
from .symbols import classify


CHART_REFRESH_SECONDS = 1.0


@dataclass
class Snapshot:
    """一次轮询的完整结果。"""

    provider_id: str
    quotes: list[Quote]
    at: float = field(default_factory=time.time)
    dark_date: str = ""
    dark_error: str | None = None
    dark_enabled: bool = False
    # 自选代码 -> 当日分时曲线，关掉分时图时为空
    trends: dict[str, Trend] = field(default_factory=dict)
    # 「自动」模式下真正出数的那个源，供界面显示
    effective_provider: str | None = None


class Poller(QThread):
    snapshot_ready = Signal(object)

    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._dark = DarkTradeClient()
        self._intraday = IntradayClient()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._quote_cache: list[Quote] = []
        self._quote_cache_key: tuple[str, tuple[str, ...], bool] | None = None
        self._quote_fetched_at = 0.0
        self._provider_id = ""
        self._effective_provider: str | None = None
        self._dark_date = ""
        self._dark_error: str | None = None

    # ------------------------------------------------------------ 控制

    def apply_config(self, config: Config) -> None:
        with self._lock:
            self._config = config
        self.refresh_now()

    def refresh_now(self) -> None:
        # 手动刷新既要立刻拉一次普通行情，也要绕过 1 秒走势图缓存。
        self._intraday.reset_cache()
        self._wake.set()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()

    # ------------------------------------------------------------ 循环

    @staticmethod
    def _loop_interval(config: Config) -> float:
        """走势图开启时每秒更新；普通报价仍按 refresh_seconds 自己节流。"""
        if config.show_sparkline and config.intraday_chart:
            return CHART_REFRESH_SECONDS
        return float(config.refresh_seconds)

    def run(self) -> None:  # noqa: D102 - QThread 入口
        force_quotes = True
        while not self._stopping.is_set():
            with self._lock:
                config = self._config
            self.snapshot_ready.emit(self._tick(config, force_quotes=force_quotes))
            woke = self._wake.wait(self._loop_interval(config))
            self._wake.clear()
            force_quotes = woke

    def _tick(self, config: Config, *, force_quotes: bool = False) -> Snapshot:
        provider = providers.resolve(config.provider)
        cache_key = (config.provider, tuple(config.symbols), config.show_dark_trade)
        now = time.monotonic()
        quotes_due = (
            force_quotes
            or cache_key != self._quote_cache_key
            or not self._quote_cache
            or now - self._quote_fetched_at >= config.refresh_seconds
        )

        if quotes_due:
            try:
                quotes = provider.fetch(list(config.symbols))
            except Exception as exc:  # 数据源整体挂掉时也要出一屏，让用户看到原因
                quotes = [Quote.failed(symbol, str(exc)[:60]) for symbol in config.symbols]

            base = Snapshot(
                provider_id=provider.id,
                quotes=quotes,
                dark_enabled=config.show_dark_trade,
                effective_provider=getattr(provider, "last_used", None),
            )
            if config.show_dark_trade:
                self._attach_dark_trade(quotes, base)

            self._quote_cache = quotes
            self._quote_cache_key = cache_key
            self._quote_fetched_at = now
            self._provider_id = base.provider_id
            self._effective_provider = base.effective_provider
            self._dark_date = base.dark_date
            self._dark_error = base.dark_error
        else:
            quotes = self._quote_cache

        snapshot = Snapshot(
            provider_id=self._provider_id or provider.id,
            quotes=quotes,
            dark_date=self._dark_date,
            dark_error=self._dark_error,
            dark_enabled=config.show_dark_trade,
            effective_provider=self._effective_provider,
        )
        if config.show_sparkline and config.intraday_chart:
            self._attach_trends(quotes, snapshot)
        return snapshot

    def _attach_trends(self, quotes: list[Quote], snapshot: Snapshot) -> None:
        """走势图秒级刷新；客户端只缓存 1 秒，失败时界面继续保留普通报价。"""
        for quote in quotes:
            if quote.error:
                continue
            trend = self._intraday.fetch(quote.symbol)
            if trend:
                snapshot.trends[quote.symbol] = trend

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
