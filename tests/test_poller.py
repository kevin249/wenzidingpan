from stockwidget.config import Config
from stockwidget.intraday import CACHE_TTL_SECONDS
from stockwidget.poller import CHART_REFRESH_SECONDS, Poller


def test_intraday_chart_refreshes_each_second():
    config = Config(refresh_seconds=9, show_sparkline=True, intraday_chart=True)
    assert CACHE_TTL_SECONDS == 1
    assert CHART_REFRESH_SECONDS == 1.0
    assert Poller._loop_interval(config) == 1.0


def test_quote_refresh_interval_is_kept_when_chart_is_off():
    config = Config(refresh_seconds=9, show_sparkline=False)
    assert Poller._loop_interval(config) == 9.0
