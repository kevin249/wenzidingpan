"""Independent quote, chart and dark-fund requests with latest-state delivery."""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime

from PySide6.QtCore import QThread, Signal

from . import providers
from .config import Config
from .darktrade import CACHE_TTL_SECONDS as DARK_REFRESH_SECONDS, DarkResult, DarkTradeClient
from .intraday import IntradayClient, Trend
from .market_hours import OFF_HOURS_WAKE_SECONDS, active_updates_allowed
from .providers.base import Quote
from .symbols import classify

CHART_WORKERS = 4


@dataclass
class Snapshot:
    provider_id: str
    quotes: list[Quote]
    at: float = field(default_factory=time.time)
    dark_date: str = ""
    dark_error: str | None = None
    dark_enabled: bool = False
    trends: dict[str, Trend] = field(default_factory=dict)
    effective_provider: str | None = None
    # None reconciles the watchlist; otherwise only these rows need updating.
    updated_symbols: tuple[str, ...] | None = None
    generation: int = 0


class Poller(QThread):
    # At most one notification is queued. The GUI drains the newest state.
    snapshot_ready = Signal()

    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._dark = DarkTradeClient()
        self._clients: dict[str, IntradayClient] = {}
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._explicit = True
        self._generation = 0
        self._delivery_lock = threading.Lock()
        self._pending: Snapshot | None = None
        self._quote_cache: list[Quote] = []
        self._trend_cache: dict[str, Trend] = {}
        self._provider_setting = config.provider
        self._provider_id = config.provider
        self._effective_provider: str | None = None
        self._dark_result = DarkResult()
        self._have_quotes = False

    @staticmethod
    def _fetch_identity(config: Config) -> tuple[object, ...]:
        return (
            config.provider, tuple(config.symbols), config.refresh_seconds,
            config.chart_refresh_seconds, config.show_sparkline,
            config.intraday_chart, config.display_theme, config.show_dark_trade,
            config.debug_mode,
        )

    def apply_config(self, config: Config) -> None:
        with self._lock:
            changed = self._fetch_identity(self._config) != self._fetch_identity(config)
            self._config = config
            if changed:
                self._generation += 1
                self._explicit = True
        self._wake.set()

    def refresh_now(self) -> None:
        with self._lock:
            self._explicit = True
        self._wake.set()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()

    @staticmethod
    def _chart_enabled(config: Config) -> bool:
        return bool(config.intraday_chart and (config.show_sparkline or config.display_theme == "theme2"))

    @staticmethod
    def _should_poll(config: Config, explicit: bool, now: datetime | None = None) -> bool:
        return bool(explicit) or active_updates_allowed(config.debug_mode, now)

    def take_snapshot(self) -> Snapshot | None:
        """Called on the GUI thread; queued older states never accumulate."""
        with self._lock, self._delivery_lock:
            snapshot, self._pending = self._pending, None
            if snapshot is not None and snapshot.generation == self._generation:
                return snapshot
        return None

    def _publish(self, config: Config, generation: int, symbols=None) -> None:
        snapshot = Snapshot(
            provider_id=self._provider_id,
            quotes=list(self._quote_cache),
            dark_date=self._dark_result.trade_date if config.show_dark_trade else "",
            dark_error=self._dark_result.error if config.show_dark_trade else None,
            dark_enabled=config.show_dark_trade,
            trends=dict(self._trend_cache),
            effective_provider=self._effective_provider,
            updated_symbols=tuple(symbols) if symbols is not None else None,
            generation=generation,
        )
        with self._lock, self._delivery_lock:
            if generation != self._generation or self._stopping.is_set():
                return
            notify = self._pending is None
            previous = self._pending
            if previous is not None and previous.generation == generation:
                if previous.updated_symbols is None or snapshot.updated_symbols is None:
                    snapshot.updated_symbols = None
                else:
                    changed = set(previous.updated_symbols) | set(snapshot.updated_symbols)
                    snapshot.updated_symbols = tuple(s for s in config.symbols if s in changed)
            self._pending = snapshot
        if notify:
            self.snapshot_ready.emit()

    def _prepare(self, config: Config) -> None:
        old = {q.symbol: q for q in self._quote_cache}
        if config.provider != self._provider_setting:
            old = {}
            self._effective_provider = None
        self._have_quotes = self._have_quotes and all(s in old for s in config.symbols)
        self._provider_setting = config.provider
        self._provider_id = config.provider
        self._quote_cache = [old.get(s, Quote(symbol=s, name=s)) for s in config.symbols]
        self._trend_cache = {
            s: t for s, t in self._trend_cache.items()
            if s in config.symbols and self._chart_enabled(config)
        }
        self._clients = {s: c for s, c in self._clients.items() if s in config.symbols}
        self._merge_dark(config)

    def _merge_dark(self, config: Config) -> None:
        quotes = []
        for quote in self._quote_cache:
            symbol = classify(quote.symbol)
            row = self._dark_result.by_code.get(symbol.code) if symbol and config.show_dark_trade else None
            # Emitted snapshots must never be mutated by later worker results.
            quotes.append(replace(
                quote, dark_fund=row.dark_fund if row else None,
                dark_main_net_inflow=row.main_net_inflow if row else None,
            ))
        self._quote_cache = quotes

    @staticmethod
    def _fetch_quotes(config: Config):
        try:
            provider = providers.resolve(config.provider)
            quotes = provider.fetch(list(config.symbols))
            return provider.id, getattr(provider, "last_used", None), quotes
        except Exception as exc:
            quotes = [Quote.failed(s, str(exc)[:60]) for s in config.symbols]
            return config.provider, None, quotes

    def _fetch_dark(self, config: Config):
        codes = {p.code for s in config.symbols if (p := classify(s))}
        try:
            return self._dark.fetch(codes)
        except Exception as exc:
            return DarkResult(error=str(exc)[:80])

    @staticmethod
    def _fetch_chart(client: IntradayClient, symbol: str) -> Trend:
        try:
            # Scheduling already enforces the interval; keep last-good fallback.
            client.reset_cache()
            return client.fetch(symbol)
        except Exception as exc:
            return Trend(error=str(exc)[:80])

    @staticmethod
    def _perform(fetch, *args):
        started = time.monotonic()
        return started, fetch(*args)

    @staticmethod
    def _jobs(config: Config) -> list[tuple[str, str]]:
        jobs = [("quotes", "")]
        if config.show_dark_trade:
            jobs.append(("dark", ""))
        if Poller._chart_enabled(config):
            jobs.extend(("chart", s) for s in config.symbols)
        return jobs

    def _accept(self, key, result, config: Config, generation: int) -> None:
        kind, symbol = key
        if kind == "quotes":
            first_quotes = not self._have_quotes
            self._have_quotes = True
            self._provider_id, self._effective_provider, quotes = result
            by_symbol = {q.symbol: q for q in quotes}
            self._quote_cache = [
                by_symbol.get(s, Quote.failed(s, "无数据")) for s in config.symbols
            ]
            self._merge_dark(config)
            changed = None if first_quotes else config.symbols
        elif kind == "dark":
            self._dark_result = result
            self._merge_dark(config)
            changed = config.symbols
        else:
            self._trend_cache[symbol] = result
            changed = [symbol]
        self._publish(config, generation, changed)

    def run(self) -> None:  # noqa: D102
        pools = {
            "quotes": ThreadPoolExecutor(max_workers=1, thread_name_prefix="quotes"),
            "dark": ThreadPoolExecutor(max_workers=1, thread_name_prefix="dark"),
            "chart": ThreadPoolExecutor(max_workers=CHART_WORKERS, thread_name_prefix="chart"),
        }
        inflight: dict[tuple[str, str], tuple[Future, int]] = {}
        due: dict[tuple[str, str], float] = {}
        generation = -1
        forced: set[tuple[str, str]] = set()
        try:
            while not self._stopping.is_set():
                # Clear before inspecting futures/config so wakeups cannot be lost.
                self._wake.clear()
                with self._lock:
                    config = self._config
                    current = self._generation
                    explicit, self._explicit = self._explicit, False
                jobs = self._jobs(config)
                if current != generation:
                    generation = current
                    due.clear()
                    self._prepare(config)
                    self._publish(config, generation)
                if explicit:
                    forced.update(jobs)
                forced.intersection_update(jobs)

                for key, (future, requested_generation) in list(inflight.items()):
                    if not future.done():
                        continue
                    del inflight[key]
                    started, result = future.result()
                    if requested_generation != generation or key not in jobs:
                        continue
                    self._accept(key, result, config, generation)
                    interval = (
                        config.refresh_seconds if key[0] == "quotes" else
                        config.chart_refresh_seconds if key[0] == "chart" else DARK_REFRESH_SECONDS
                    )
                    # Start-to-start cadence: network work is not added to the interval.
                    due[key] = started + interval

                active = self._should_poll(config, False)
                chart_slots = CHART_WORKERS - sum(k[0] == "chart" for k in inflight)
                now = time.monotonic()
                for key in sorted(jobs, key=lambda k: due.get(k, 0.0)):
                    kind, symbol = key
                    if key in inflight or not (active or key in forced):
                        continue
                    if key not in forced and due.get(key, 0.0) > now:
                        continue
                    if kind == "chart" and chart_slots <= 0:
                        continue
                    forced.discard(key)
                    if kind == "quotes":
                        fetch, args = self._fetch_quotes, (config,)
                    elif kind == "dark":
                        fetch, args = self._fetch_dark, (config,)
                    else:
                        if symbol not in self._clients:
                            self._clients[symbol] = IntradayClient()
                        fetch, args = self._fetch_chart, (self._clients[symbol], symbol)
                        chart_slots -= 1
                    future = pools[kind].submit(self._perform, fetch, *args)
                    inflight[key] = (future, generation)
                    future.add_done_callback(lambda _: self._wake.set())

                now = time.monotonic()
                waits = [OFF_HOURS_WAKE_SECONDS]
                for key in jobs:
                    if key in inflight or not (active or key in forced):
                        continue
                    if key[0] == "chart" and chart_slots <= 0:
                        continue
                    waits.append(0.0 if key in forced else max(0.0, due.get(key, 0.0) - now))
                self._wake.wait(min(waits))
        finally:
            # Requests have timeouts; shutdown must not block the GUI on network I/O.
            for pool in pools.values():
                pool.shutdown(wait=False, cancel_futures=True)
