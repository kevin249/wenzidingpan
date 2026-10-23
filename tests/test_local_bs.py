from datetime import datetime

from stockwidget.intraday import Trend
from stockwidget.local_bs import LocalBsNotifier, SAMPLE_LEN
from stockwidget.market_hours import SHANGHAI_TZ
from stockwidget.providers.base import Quote


AT = datetime(2026, 10, 23, 10, 0, tzinfo=SHANGHAI_TZ).timestamp()


def collect(notifier, prices, *, symbol="603986", enabled=True, at=AT, sampled=False):
    quote = Quote.from_prices(symbol, "测试股票", prices[-1], 10.0)
    trends = {} if sampled else {symbol: Trend(prices=prices)}
    return notifier.collect([quote], trends, enabled=enabled, at=at)


def test_initial_history_is_silent_and_new_buy_sell_print_once():
    notifier = LocalBsNotifier()
    assert collect(notifier, [10.00, 10.06, 10.12]) == []
    sell = collect(notifier, [10.00, 10.06, 10.12, 10.05])
    assert len(sell) == 1
    assert "[2026-10-23 10:00:00] [本地B/S] 测试股票(603986) | S" in sell[0]
    assert "转折价=10.12 | 最新分时价=10.05 | 分时点=3" in sell[0]
    assert collect(notifier, [10.00, 10.06, 10.12, 10.05]) == []
    assert collect(notifier, [10.00, 10.06, 10.12, 10.05, 9.98]) == []
    buy = collect(notifier, [10.00, 10.06, 10.12, 10.05, 9.98, 10.04])
    assert len(buy) == 1 and " | B | 转折价=9.98" in buy[0]


def test_same_minute_update_and_disappear_reappear_do_not_repeat():
    notifier = LocalBsNotifier()
    assert collect(notifier, [10.00, 10.02, 10.04]) == []
    assert len(collect(notifier, [10.00, 10.02, 10.06])) == 1
    assert collect(notifier, [10.00, 10.02, 10.04]) == []
    assert collect(notifier, [10.00, 10.02, 10.07]) == []


def test_all_new_points_between_polls_are_reported_in_order():
    notifier = LocalBsNotifier()
    collect(notifier, [10.00, 10.01])
    messages = collect(notifier, [10.00, 10.01, 10.10, 10.00, 10.10])
    assert [message.split(" | ")[1] for message in messages] == ["B", "S", "B"]


def test_disabled_source_or_markers_rebaseline_on_enable():
    notifier = LocalBsNotifier()
    collect(notifier, [10.0, 10.01])
    assert collect(notifier, [10.0, 10.01, 10.10], enabled=False) == []
    assert collect(notifier, [10.0, 10.01, 10.10]) == []
    assert len(collect(notifier, [10.0, 10.01, 10.10, 10.0])) == 1


def test_shanghai_day_and_shorter_series_rebaseline():
    notifier = LocalBsNotifier()
    collect(notifier, [10.0, 10.01, 10.10, 10.0])
    assert collect(notifier, [10.0, 10.01, 10.10, 10.0, 10.1], at=AT + 86400) == []
    assert collect(notifier, [10.0, 10.01, 10.10], at=AT + 86401) == []
    assert len(collect(notifier, [10.0, 10.01, 10.10, 10.0], at=AT + 86402)) == 1


def test_quote_failure_does_not_replay_intraday_signals():
    notifier = LocalBsNotifier()
    prices = [10.0, 10.01, 10.10]
    collect(notifier, prices)
    assert notifier.collect([Quote.failed("603986", "超时")], {}, enabled=True, at=AT) == []
    assert collect(notifier, prices) == []
    assert len(collect(notifier, prices + [10.0])) == 1


def test_stock_identity_is_normalized_but_independent():
    notifier = LocalBsNotifier()
    quotes = [Quote.from_prices(code, code, 10.01, 10.0) for code in ("603986", "300223")]
    trends = {q.symbol: Trend(prices=[10.0, 10.01]) for q in quotes}
    assert notifier.collect(quotes, trends, enabled=True, at=AT) == []
    quotes[0].symbol = "603986.SH"
    trends = {q.symbol: Trend(prices=[10.0, 10.01, 10.10]) for q in quotes}
    messages = notifier.collect(quotes, trends, enabled=True, at=AT)
    assert len(messages) == 2
    assert "(603986)" in messages[0] and "(300223)" in messages[1]
    assert notifier.collect(quotes, trends, enabled=True, at=AT) == []


def test_sample_fallback_notifies_and_rolling_window_does_not_replay():
    notifier = LocalBsNotifier()
    assert collect(notifier, [10.0], sampled=True) == []
    assert collect(notifier, [10.01], sampled=True) == []
    buy = collect(notifier, [10.10], sampled=True)
    assert len(buy) == 1 and "最新采样价=10.10 | 采样点=1" in buy[0]
    for _ in range(SAMPLE_LEN * 3):
        assert collect(notifier, [10.10], sampled=True) == []
    sell = collect(notifier, [10.0], sampled=True)
    assert len(sell) == 1 and " | S | " in sell[0]
    assert collect(notifier, [10.0], sampled=True) == []
    assert len(notifier._stocks["sh603986"].seen) <= SAMPLE_LEN


def test_data_mode_changes_only_establish_baseline():
    notifier = LocalBsNotifier()
    collect(notifier, [10.0, 10.01])
    assert collect(notifier, [10.01], sampled=True) == []
    assert collect(notifier, [10.01], sampled=True) == []
    assert collect(notifier, [10.0, 10.01, 10.10, 10.0]) == []
    assert len(collect(notifier, [10.0, 10.01, 10.10, 10.0, 10.10])) == 1
