from datetime import datetime, timezone

from stockwidget.config import Store, sanitize
from stockwidget.market_hours import (
    SHANGHAI_TZ,
    active_updates_allowed,
    is_a_share_active_time,
)


def _dt(hour: int, minute: int, second: int = 0) -> datetime:
    # 2026-09-21 是周一。
    return datetime(2026, 9, 21, hour, minute, second, tzinfo=SHANGHAI_TZ)


def test_a_share_active_window_includes_call_auction_and_both_sessions():
    assert is_a_share_active_time(_dt(9, 14, 59)) is False
    assert is_a_share_active_time(_dt(9, 15)) is True
    assert is_a_share_active_time(_dt(11, 30)) is True
    assert is_a_share_active_time(_dt(11, 30, 1)) is False
    assert is_a_share_active_time(_dt(13, 0)) is True
    assert is_a_share_active_time(_dt(15, 0)) is True
    assert is_a_share_active_time(_dt(15, 0, 1)) is False


def test_weekend_is_inactive_but_debug_bypasses_all_time_limits():
    sunday = datetime(2026, 9, 20, 10, 0, tzinfo=SHANGHAI_TZ)
    assert is_a_share_active_time(sunday) is False
    assert active_updates_allowed(False, sunday) is False
    assert active_updates_allowed(True, sunday) is True


def test_timezone_is_normalized_to_shanghai():
    # 01:15 UTC == 09:15 Asia/Shanghai。
    utc = datetime(2026, 9, 21, 1, 15, tzinfo=timezone.utc)
    assert is_a_share_active_time(utc) is True


def test_debug_mode_defaults_off_sanitizes_and_persists(tmp_path):
    assert sanitize({}).debug_mode is False
    assert sanitize({"debug_mode": "yes"}).debug_mode is False

    store = Store(tmp_path / "config.json")
    saved = store.update({"debug_mode": True})
    assert saved.debug_mode is True
    assert Store(store.path).get().debug_mode is True
