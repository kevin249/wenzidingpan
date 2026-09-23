from datetime import datetime

from stockwidget.config import Config
from stockwidget.intraday import CACHE_TTL_SECONDS
from stockwidget.market_hours import FALLBACK_HEARTBEAT_SECONDS, SHANGHAI_TZ
from stockwidget.poller import CHART_REFRESH_SECONDS, Poller


def _sunday() -> datetime:
    # 2026-09-20 是周日，既非交易日也不在活跃窗口内。
    return datetime(2026, 9, 20, 10, 0, tzinfo=SHANGHAI_TZ)


def test_intraday_chart_refreshes_each_second_in_debug():
    config = Config(refresh_seconds=9, show_sparkline=True, intraday_chart=True, debug_mode=True)
    assert CACHE_TTL_SECONDS == 1
    assert CHART_REFRESH_SECONDS == 1.0
    assert Poller._loop_interval(config) == 1.0


def test_quote_refresh_interval_is_kept_when_chart_is_off():
    config = Config(refresh_seconds=9, show_sparkline=False, debug_mode=True)
    assert Poller._loop_interval(config) == 9.0


def test_theme2_keeps_intraday_refresh_even_when_classic_sparkline_is_hidden():
    config = Config(
        refresh_seconds=9,
        display_theme="theme2",
        show_sparkline=False,
        intraday_chart=True,
        debug_mode=True,
    )
    assert Poller._chart_enabled(config) is True
    assert Poller._loop_interval(config) == 1.0


def test_non_debug_falls_back_to_a_low_frequency_heartbeat():
    """非 Debug 常态由 MCP 推送事件驱动，循环里只留 5 分钟兜底心跳。"""
    config = Config(refresh_seconds=3, show_sparkline=True, intraday_chart=True)
    assert config.debug_mode is False
    assert Poller._loop_interval(config) == FALLBACK_HEARTBEAT_SECONDS == 300.0


def test_non_debug_does_not_poll_proactively_but_explicit_requests_pass():
    """非 Debug + 休市：循环不发请求；启动 / 手动 / 推送这类显式意图必须放行。"""
    config = Config(debug_mode=False)
    assert Poller._should_poll(config, explicit=False, now=_sunday()) is False
    assert Poller._should_poll(config, explicit=True, now=_sunday()) is True


def test_debug_mode_keeps_polling_around_the_clock():
    config = Config(debug_mode=True)
    assert Poller._should_poll(config, explicit=False, now=_sunday()) is True


def test_startup_frame_is_explicit_so_opening_the_app_always_requests_once():
    """打开组件即拉一次，不判断 Debug 也不判断休市，靠首帧的显式标记实现。"""
    poller = Poller(Config(debug_mode=False))
    assert poller._explicit is True


def test_display_only_change_skips_request_while_fetch_change_forces_one():
    poller = Poller(Config(symbols=["600519"]))
    poller._explicit = False

    # 纯展示类设置（灰度）不需要额外打行情源。
    poller.apply_config(Config(symbols=["600519"], grayscale=True))
    assert poller._explicit is False

    # 自选股变化必须立刻重拉，否则界面还挂着旧标的。
    poller.apply_config(Config(symbols=["600519", "000001"]))
    assert poller._explicit is True


def test_manual_refresh_sets_the_explicit_flag():
    poller = Poller(Config(debug_mode=False))
    poller._explicit = False
    poller.refresh_now()
    assert poller._explicit is True
