from datetime import datetime, timedelta

from stockwidget.intraday import Trend
from stockwidget.local_bs import LocalBsNotifier
from stockwidget.market_hours import SHANGHAI_TZ
from stockwidget.providers.base import Quote
from stockwidget.tdw import Point


START = datetime(2026, 10, 23, 10, 0, tzinfo=SHANGHAI_TZ)
QUOTE = Quote.from_prices("603986", "兆易创新", 442, 441)


def collect(notifier, minutes, at=START, **options):
    day = START.date().isoformat()
    trend = Trend(prices=[442.] * len(minutes), minutes=minutes, trade_date=day,
                  tdw_points={f"{day} {m}": Point(.6, 442., ("B2",)) for m in minutes})
    return notifier.collect([QUOTE], {QUOTE.symbol: trend}, enabled=options.get("enabled", True), at=at.timestamp())


def test_notifier_is_silent_on_history_short_packets_and_switches():
    notifier = LocalBsNotifier()
    assert not collect(notifier, ["09:31", "09:32"])
    assert not collect(notifier, ["09:32", "10:00"])
    times = ["09:59", "10:00", "10:01", "10:02"]
    later = START + timedelta(minutes=2)
    messages = collect(notifier, times, at=later)
    assert len(messages) == 2 and "B2 关注" in messages[0] and "行情时间=10:02" in messages[1]
    assert not collect(notifier, times[:2], at=later)
    assert not collect(notifier, times, at=later)
    assert not collect(notifier, times, at=later, enabled=False)
    assert not collect(notifier, times, at=later)
    assert not collect(notifier, times, at=START+timedelta(days=1))


def test_quotes_and_close_only_samples_cannot_fabricate_tdw_signals():
    notifier = LocalBsNotifier()
    for trend in (None, Trend(prices=[10., 15.]), Trend(prices=[10., 15., 9.])):
        assert notifier.collect([QUOTE], {QUOTE.symbol: trend}, enabled=True, at=START.timestamp()) == []


def test_live_cross_is_not_announced_as_closed_or_printed_twice():
    notifier = LocalBsNotifier()
    assert not collect(notifier, ["09:59", "10:00"])
    day, stamp = "2026-10-23", "2026-10-23 10:01"
    now = START + timedelta(minutes=1)
    for provisional, expected in [(True, 0), (False, 1), (False, 0)]:
        trend = Trend(prices=[440., 442.], minutes=["10:00", "10:01"], trade_date=day,
                      tdw_points={stamp: Point(.6, 442., ("B2",), provisional)})
        messages = notifier.collect([QUOTE], {QUOTE.symbol: trend}, enabled=True, at=now.timestamp())
        assert len(messages) == expected


def test_two_confirmed_seconds_in_one_minute_are_notified_independently():
    notifier = LocalBsNotifier()
    assert not collect(notifier, ["09:59:56", "09:59:59"])
    clocks = ["10:00:03", "10:00:06"]
    at = START + timedelta(seconds=7)
    messages = collect(notifier, clocks, at=at)
    assert len(messages) == 2
    assert all(f"行情时间={clock}" in msg for clock, msg in zip(clocks, messages))
    assert not collect(notifier, clocks, at=at)
