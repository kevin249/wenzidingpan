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
    assert "转折价=10.12 | 确认价=10.05 | 最新分时价=10.05 | 分时点=3" in sell[0]
    assert collect(notifier, [10.00, 10.06, 10.12, 10.05]) == []
    assert collect(notifier, [10.00, 10.06, 10.12, 10.05, 9.98]) == []
    buy = collect(notifier, [10.00, 10.06, 10.12, 10.05, 9.98, 10.04])
    assert len(buy) == 1 and " | B | " in buy[0] and "转折价=9.98" in buy[0]


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


def test_shanghai_day_rebaselines_but_shorter_series_does_not_forget_history():
    notifier = LocalBsNotifier()
    collect(notifier, [10.0, 10.01, 10.10, 10.0])
    assert collect(notifier, [10.0, 10.01, 10.10, 10.0, 10.1], at=AT + 86400) == []
    assert collect(notifier, [10.0, 10.01, 10.10], at=AT + 86401) == []
    assert collect(notifier, [10.0, 10.01, 10.10, 10.0], at=AT + 86402) == []
    messages = collect(notifier, [10.0, 10.01, 10.10, 10.0, 10.1, 10.0], at=AT + 86403)
    assert len(messages) == 1 and " | S | " in messages[0]


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
    assert len(notifier._stocks["sh603986"].histories["sample"].seen) <= SAMPLE_LEN


def test_data_mode_changes_only_establish_baseline():
    notifier = LocalBsNotifier()
    collect(notifier, [10.0, 10.01])
    assert collect(notifier, [10.01], sampled=True) == []
    assert collect(notifier, [10.01], sampled=True) == []
    assert collect(notifier, [10.0, 10.01, 10.10, 10.0]) == []
    assert len(collect(notifier, [10.0, 10.01, 10.10, 10.0, 10.10])) == 1


def test_short_full_oscillation_never_replays_old_signals():
    notifier = LocalBsNotifier()
    prices = [280.0, 282.0, 284.0, 281.0, 283.0, 280.0, 282.0]
    assert collect(notifier, prices) == []
    for _ in range(20):
        assert collect(notifier, prices[:3]) == []
        assert collect(notifier, prices) == []
    # 恢复后真正新确认的 S 仍只报一次。
    messages = collect(notifier, prices + [280.0])
    assert len(messages) == 1 and " | S | " in messages[0]
    assert collect(notifier, prices + [280.0]) == []


def test_history_rewritten_at_same_length_is_not_a_new_confirmation():
    notifier = LocalBsNotifier()
    collect(notifier, [10.0, 10.0, 10.0, 10.0, 10.0])
    assert collect(notifier, [10.0, 10.1, 10.0, 10.0, 10.0]) == []
    assert len(collect(notifier, [10.0, 10.1, 10.0, 10.0, 10.1])) == 1


def test_disabled_or_missing_stock_does_not_erase_intraday_history():
    for missing in (True, False):
        notifier = LocalBsNotifier()
        prices = [10.0, 10.1, 10.2, 10.0, 10.1]
        collect(notifier, prices)
        if missing:
            assert notifier.collect([], {}, enabled=True, at=AT) == []
        else:
            assert collect(notifier, prices, enabled=False) == []
        assert collect(notifier, prices[:3]) == []
        assert collect(notifier, prices) == []


def collect_minutes(notifier, prices, minutes, *, source="eastmoney", day="2026-10-23", at=AT):
    quote = Quote.from_prices("603986", "兆易创新", prices[-1], 280.0)
    trend = Trend(prices=prices, minutes=minutes, trade_date=day, source=source)
    return notifier.collect([quote], {quote.symbol: trend}, enabled=True, at=at)


def test_minute_identity_survives_missing_bar_backfill_and_source_switch():
    notifier = LocalBsNotifier()
    # 09:57 补入后，同一批点的数组下标变了，但行情时间没有变。
    sparse = [280.0, 282.0, 280.0, 282.0]
    sparse_minutes = ["09:56", "09:58", "09:59", "10:00"]
    full = [280.0, 280.5, 282.0, 280.0, 282.0]
    full_minutes = ["09:56", "09:57", "09:58", "09:59", "10:00"]
    assert collect_minutes(notifier, sparse, sparse_minutes) == []
    assert collect_minutes(notifier, full, full_minutes) == []
    assert collect_minutes(notifier, sparse, sparse_minutes, source="tencent") == []
    assert collect_minutes(notifier, full, full_minutes) == []
    messages = collect_minutes(notifier, full + [280.0], full_minutes + ["10:01"], at=AT + 60)
    assert len(messages) == 1
    assert "转折时间=10:00 | 确认时间=10:01" in messages[0]
    assert "转折价=282.00 | 确认价=280.00" in messages[0]
    assert "周期=分时" in messages[0] and "数据源=eastmoney" in messages[0]


def test_confirmation_price_is_not_replaced_by_latest_price():
    notifier = LocalBsNotifier()
    collect_minutes(notifier, [280, 280], ["09:59", "10:00"])
    messages = collect_minutes(notifier, [280, 280, 282, 283], ["09:59", "10:00", "10:01", "10:02"], at=AT + 120)
    assert len(messages) == 1
    assert "确认时间=10:01" in messages[0]
    assert "确认价=282.00 | 最新分时价=283.00" in messages[0]


def test_same_minute_can_confirm_new_signal_but_cannot_repeat_it():
    notifier = LocalBsNotifier()
    minutes = ["09:58", "09:59", "10:00"]
    collect_minutes(notifier, [280.0, 280.5, 280.8], minutes)
    assert len(collect_minutes(notifier, [280.0, 280.5, 282.0], minutes)) == 1
    assert collect_minutes(notifier, [280.0, 280.5, 280.8], minutes) == []
    assert collect_minutes(notifier, [280.0, 280.5, 282.1], minutes) == []


def test_initial_partial_history_does_not_replay_pre_start_signals():
    notifier = LocalBsNotifier()
    collect_minutes(notifier, [280, 282], ["09:30", "09:31"])
    assert collect_minutes(notifier, [280, 282, 280, 282], ["09:30", "09:31", "09:32", "09:33"]) == []


def test_old_trade_date_and_invalid_time_axis_do_not_alert_or_reset_history():
    notifier = LocalBsNotifier()
    times = ["09:58", "09:59", "10:00"]
    collect_minutes(notifier, [280, 281, 282], times)
    assert collect_minutes(notifier, [280, 282, 280], times, day="2026-10-22") == []
    for invalid in (["09:58"], ["10:00", "09:59", "09:58"], ["bad"] * 3):
        assert collect_minutes(notifier, [280, 282, 280], invalid) == []
    assert collect_minutes(notifier, [280, 281, 282], times) == []
    assert len(collect_minutes(notifier, [280, 281, 282, 280], times + ["10:01"], at=AT + 60)) == 1
