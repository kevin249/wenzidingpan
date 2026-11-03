"""Exercise real workers, scheduling and delivery without public network calls."""

import threading
import time
from dataclasses import replace
from datetime import datetime

import pytest
from PySide6.QtCore import Qt

from stockwidget import poller as module
from stockwidget.config import Config
from stockwidget.darktrade import DarkResult, DarkRow
from stockwidget.intraday import Trend
from stockwidget.market_hours import SHANGHAI_TZ
from stockwidget.poller import CHART_WORKERS, Poller
from stockwidget.providers.base import Quote


def wait_for(condition, timeout=2):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if condition():
            return
        time.sleep(0.005)
    assert condition(), "worker did not produce the expected result"


@pytest.fixture()
def running(monkeypatch):
    pollers, gates = [], []

    def gate():
        event = threading.Event()
        gates.append(event)
        return event

    def start(config, *, quotes=None, chart=None, dark=None, consume=True, active=True):
        monkeypatch.setattr(module, "active_updates_allowed", lambda *args: active)
        poller = Poller(config)
        pollers.append(poller)
        received, notifications = [], []

        def default_quotes(config):
            return "test", None, [Quote.from_prices(s, s, 10.1, 10) for s in config.symbols]

        monkeypatch.setattr(poller, "_fetch_quotes", quotes or default_quotes)
        monkeypatch.setattr(poller, "_fetch_chart", lambda client, symbol:
                            chart(symbol) if chart else Trend(prices=[10.0, 10.1]))
        monkeypatch.setattr(poller, "_fetch_dark", dark or (lambda config: DarkResult()))

        def ready():
            notifications.append(time.monotonic())
            if consume and (snapshot := poller.take_snapshot()) is not None:
                received.append(snapshot)

        poller.snapshot_ready.connect(ready, Qt.DirectConnection)
        poller.start()
        return poller, received, notifications

    start.gate = gate
    yield start
    for event in gates:
        event.set()
    for poller in pollers:
        poller.stop()
        assert poller.wait(2000)


def config(**changes):
    return replace(Config(symbols=["600519", "603986"], show_dark_trade=False,
                          refresh_seconds=0.1, chart_refresh_seconds=0.1), **changes)


def test_quotes_are_delivered_before_slow_charts_and_dark_funds(running):
    release = running.gate()
    entered = threading.Event()

    def slow_chart(symbol):
        entered.set()
        release.wait(2)
        return Trend(prices=[10, 10.2])

    def slow_dark(cfg):
        release.wait(2)
        return DarkResult()

    poller, received, _ = running(config(show_dark_trade=True), chart=slow_chart, dark=slow_dark)
    assert entered.wait(1)
    wait_for(lambda: any(s.quotes[0].price == 10.1 for s in received))
    assert not release.is_set()
    assert all(not s.trends for s in received)


@pytest.mark.parametrize("theme", ["theme1", "theme2"])
def test_fast_chart_is_delivered_while_quote_and_other_chart_are_blocked(running, theme):
    release = running.gate()

    def slow_quotes(cfg):
        release.wait(2)
        return "test", None, [Quote.from_prices(s, s, 10.1, 10) for s in cfg.symbols]

    def chart(symbol):
        if symbol == "603986":
            release.wait(2)
        return Trend(prices=[10, 10.2])

    poller, received, _ = running(config(display_theme=theme), quotes=slow_quotes, chart=chart)
    wait_for(lambda: any("600519" in s.trends for s in received))
    assert not release.is_set()
    assert all("603986" not in s.trends for s in received)
    assert all(s.quotes[0].price is None for s in received)


def test_request_duration_is_not_added_to_refresh_interval(running):
    starts = []

    def quotes(cfg):
        starts.append(time.monotonic())
        time.sleep(0.08)
        return "test", None, [Quote.from_prices(s, s, len(starts), 10) for s in cfg.symbols]

    running(config(intraday_chart=False), quotes=quotes)
    wait_for(lambda: len(starts) >= 5)
    # The old schedule needed about 0.72s between the first and fifth starts.
    assert (starts[4] - starts[0]) / 4 < 0.16


def test_chart_concurrency_is_bounded_and_one_request_per_stock(running):
    release = running.gate()
    lock = threading.Lock()
    active, peak = {}, [0]
    symbols = ["600519", "603986", "600000", "000001", "300750", "601318"]

    def chart(symbol):
        with lock:
            active[symbol] = active.get(symbol, 0) + 1
            assert active[symbol] == 1
            peak[0] = max(peak[0], sum(active.values()))
        release.wait(2)
        with lock:
            active[symbol] -= 1
        return Trend(prices=[10, 10.2])

    poller, _, _ = running(config(symbols=symbols), chart=chart)
    wait_for(lambda: sum(active.values()) == CHART_WORKERS)
    for _ in range(4):
        poller.refresh_now()
    time.sleep(0.15)
    assert peak[0] == CHART_WORKERS
    release.set()
    wait_for(lambda: all(s in poller._trend_cache for s in symbols))


def test_latest_state_mailbox_does_not_queue_old_frames(running):
    counter = [0]

    def quotes(cfg):
        counter[0] += 1
        return "test", None, [Quote.from_prices(s, s, counter[0], 10) for s in cfg.symbols]

    poller, _, notifications = running(config(intraday_chart=False), quotes=quotes, consume=False)
    wait_for(lambda: counter[0] >= 4)
    assert len(notifications) == 1
    snapshot = poller.take_snapshot()
    assert snapshot.quotes[0].price >= 3
    assert snapshot.updated_symbols is None  # Initial watchlist reconciliation is retained.
    wait_for(lambda: len(notifications) >= 2)


def test_off_hours_only_explicit_requests_run_once(running):
    counts = {"quote": 0, "chart": 0}

    def quotes(cfg):
        counts["quote"] += 1
        return "test", None, [Quote.from_prices(s, s, 10.1, 10) for s in cfg.symbols]

    def chart(symbol):
        counts["chart"] += 1
        return Trend(prices=[10, 10.2])

    poller, received, _ = running(config(symbols=["600519"]), quotes=quotes, chart=chart, active=False)
    wait_for(lambda: any(s.trends for s in received))
    time.sleep(0.15)
    assert counts == {"quote": 1, "chart": 1}
    poller.refresh_now()
    wait_for(lambda: counts == {"quote": 2, "chart": 2})


def test_late_results_from_previous_configuration_are_discarded(running):
    release = running.gate()
    entered = threading.Event()

    def quotes(cfg):
        if cfg.symbols == ["600519"]:
            entered.set()
            release.wait(2)
        return "test", None, [Quote.from_prices(s, s, 10.1, 10) for s in cfg.symbols]

    poller, received, _ = running(config(symbols=["600519"], intraday_chart=False), quotes=quotes)
    assert entered.wait(1)
    poller.apply_config(config(symbols=["603986"], intraday_chart=False))
    wait_for(lambda: any(s.generation == 1 for s in received))
    release.set()
    wait_for(lambda: any(s.generation == 1 and s.quotes[0].price == 10.1 for s in received))
    assert all([q.symbol for q in s.quotes] == ["603986"] for s in received if s.generation == 1)


def test_dark_result_does_not_mutate_an_already_delivered_quote(running):
    release = running.gate()

    def dark(cfg):
        release.wait(2)
        return DarkResult(by_code={"600519": DarkRow(
            code="600519", name="test", market="sh", dark_fund=123, open_fund=0,
            main_net_inflow=456, activity=0, price=10.1, change_percent=1, rank=1,
        )})

    _, received, _ = running(config(symbols=["600519"], show_dark_trade=True), dark=dark)
    wait_for(lambda: any(s.quotes[0].price == 10.1 for s in received))
    earlier = next(s.quotes[0] for s in received if s.quotes[0].price == 10.1)
    assert earlier.dark_fund is None
    release.set()
    wait_for(lambda: any(s.quotes[0].dark_fund == 123 for s in received))
    assert earlier.dark_fund is None


def test_display_only_config_changes_do_not_force_network_requests():
    poller = Poller(Config())
    poller._explicit = False
    poller.apply_config(Config(grayscale=True))
    assert not poller._explicit
    poller.apply_config(Config(chart_refresh_seconds=0.1))
    assert poller._explicit and poller._generation == 1


def test_market_hours_and_explicit_refresh_rules():
    sunday = datetime(2026, 9, 20, 10, 0, tzinfo=SHANGHAI_TZ)
    assert not Poller._should_poll(Config(), False, sunday)
    assert Poller._should_poll(Config(), True, sunday)
    assert Poller._should_poll(Config(debug_mode=True), False, sunday)
