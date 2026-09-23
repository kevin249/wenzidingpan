"""提醒分发：终端铃铛 / 系统通知 / 通知区图标 / 窗口 BELL 四路各走各的开关。

直接拿未绑定的方法配假的 self 来跑，避免和 QApplication 单例较劲。
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

try:
    from PySide6.QtWidgets import QSystemTrayIcon
except (ImportError, OSError) as error:
    pytest.skip(f"Qt 运行库不可用：{error}", allow_module_level=True)

from stockwidget import app as app_module
from stockwidget.config import Config, Store
from stockwidget.mcp_notifications import McpNotification

NOTIFICATION = McpNotification(
    event_id="e1", title="盯盘提醒", body="600519 涨停封板", created_at="09:31"
)


class _FakeWindow:
    def __init__(self, visible: bool = True) -> None:
        self.calls: list[tuple[str, str]] = []
        self.cleared = 0
        self.visible = visible
        self.shown = 0
        self.hidden = 0
        self.raised = 0
        self.activated = 0
        self.active = False

    def show_mcp_notification(self, title: str, body: str) -> None:
        self.calls.append((title, body))

    def clear_mcp_notifications(self) -> None:
        self.cleared += 1

    def apply_config(self, config: Config) -> None:
        pass

    def isVisible(self) -> bool:
        return self.visible

    def isActiveWindow(self) -> bool:
        return self.active

    def show(self) -> None:
        self.visible = True
        self.shown += 1

    def hide(self) -> None:
        self.visible = False
        self.hidden += 1

    def raise_(self) -> None:
        self.raised += 1

    def activateWindow(self) -> None:
        self.activated += 1


class _FakeTray:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.unread = 0

    def notify(self, title: str, body: str) -> bool:
        self.calls.append((title, body))
        return True

    def set_unread(self, count: int) -> int:
        self.unread = count
        return count

    def apply_config(self, config: Config) -> None:
        pass


class _FakePoller:
    """只记推送触发的刷新次数：非 Debug 模式下这是界面数据更新的主要来源。"""

    def __init__(self) -> None:
        self.refreshes = 0

    def refresh_now(self) -> None:
        self.refreshes += 1


@pytest.fixture()
def dispatch(monkeypatch):
    def run(config: Config, *, tray: bool = True, times: int = 1):
        rung: list[bool] = []
        monkeypatch.setattr(
            app_module.desktop, "ring_terminal_bell", lambda: bool(rung.append(True))
        )
        monkeypatch.setattr(app_module.desktop, "terminal_is_foreground", lambda: False)

        window = _FakeWindow()
        tray_icon = _FakeTray() if tray else None
        poller = _FakePoller()
        stub = SimpleNamespace(
            config=config, window=window, tray=tray_icon, _unread=0, poller=poller
        )
        for _ in range(times):
            app_module.WidgetApp._on_mcp_notification(stub, NOTIFICATION)
        return SimpleNamespace(
            bell=len(rung),
            toast=len(tray_icon.calls) if tray_icon else 0,
            tray_icon=tray_icon.unread if tray_icon else 0,
            window=len(window.calls),
            refresh=poller.refreshes,
        )

    return run


def _channels(result):
    return (result.bell, result.toast, result.tray_icon, result.window)


def test_all_four_channels_fire_by_default(dispatch):
    assert _channels(dispatch(Config())) == (1, 1, 1, 1)


def test_push_triggers_a_market_refresh(dispatch):
    """推送到达要顺带刷新行情与分时：非 Debug 不做定时轮询，界面靠这条通路更新。"""
    assert dispatch(Config(), times=3).refresh == 3


@pytest.mark.parametrize(
    "switch, expected",
    [
        ("mcp_bell_terminal", (0, 1, 1, 1)),
        ("mcp_bell_toast", (1, 0, 1, 1)),
        ("mcp_bell_tray_icon", (1, 1, 0, 1)),
        ("mcp_bell_window", (1, 1, 1, 0)),
    ],
)
def test_each_switch_only_silences_its_own_channel(dispatch, switch, expected):
    assert _channels(dispatch(Config(**{switch: False}))) == expected


def test_tray_icon_unread_accumulates_across_notifications(dispatch):
    assert dispatch(Config(), times=3).tray_icon == 3


def test_all_switches_off_still_prints_the_body(dispatch, capsys):
    result = dispatch(
        Config(
            mcp_bell_terminal=False,
            mcp_bell_toast=False,
            mcp_bell_tray_icon=False,
            mcp_bell_window=False,
        )
    )
    assert _channels(result) == (0, 0, 0, 0)

    printed = capsys.readouterr().out
    assert "盯盘提醒" in printed and "600519 涨停封板" in printed


def test_consecutive_notifications_get_a_blank_divider_between_them(dispatch, capsys):
    dispatch(Config(), times=2)
    printed = capsys.readouterr().out
    assert "600519 涨停封板\n\n======\n\n[MCP提醒]" in printed


def test_tray_channels_are_skipped_without_a_tray(dispatch):
    assert _channels(dispatch(Config(), tray=False)) == (1, 0, 0, 1)


def _tray_click_stub(unread: int, *, visible: bool = True):
    window, tray = _FakeWindow(visible), _FakeTray()
    tray.unread = unread
    toggled: list[bool] = []
    stub = SimpleNamespace(
        window=window, tray=tray, _unread=unread, toggle_window=lambda: toggled.append(True)
    )
    stub._clear_unread = lambda: app_module.WidgetApp._clear_unread(stub)
    stub._show_window = lambda: app_module.WidgetApp._show_window(stub)
    return stub, window, tray, toggled


def test_tray_click_with_unread_acknowledges_and_keeps_visible_window_open():
    """有未读时第一次点击只确认提醒并置前，不能把本来可见的窗口藏掉。"""
    stub, window, tray, toggled = _tray_click_stub(3, visible=True)

    app_module.WidgetApp._on_tray_activated(stub, QSystemTrayIcon.Trigger)

    assert (stub._unread, tray.unread, window.cleared) == (0, 0, 1)
    assert window.visible is True
    assert window.hidden == 0
    assert window.raised == 1 and window.activated == 1
    assert toggled == []


def test_tray_click_with_unread_shows_hidden_window_immediately():
    """窗口原来被藏着时，有未读的第一次点击就直接显示，不必点第二次。"""
    stub, window, tray, toggled = _tray_click_stub(2, visible=False)

    app_module.WidgetApp._on_tray_activated(stub, QSystemTrayIcon.Trigger)

    assert (stub._unread, tray.unread, window.cleared) == (0, 0, 1)
    assert window.visible is True and window.shown == 1
    assert window.raised == 1 and window.activated == 1
    assert toggled == []


def test_tray_click_without_unread_keeps_toggle_behavior():
    stub, window, tray, toggled = _tray_click_stub(0)

    app_module.WidgetApp._on_tray_activated(stub, QSystemTrayIcon.Trigger)

    assert (stub._unread, tray.unread, window.cleared) == (0, 0, 0)
    assert toggled == [True]


@pytest.mark.parametrize(
    "config, cleared",
    [
        (Config(mcp_notifications_enabled=True), False),
        (Config(mcp_notifications_enabled=False), True),
        (Config(mcp_notifications_enabled=True, mcp_bell_tray_icon=False), True),
    ],
)
def test_tray_indicator_follows_both_the_master_and_its_own_switch(config, cleared):
    window, tray = _FakeWindow(), _FakeTray()
    tray.unread = 2
    stub = SimpleNamespace(
        window=window,
        tray=tray,
        _unread=2,
        poller=SimpleNamespace(apply_config=lambda c: None),
        notification_listener=SimpleNamespace(apply_config=lambda c: None),
    )
    stub._clear_unread = lambda: app_module.WidgetApp._clear_unread(stub)

    app_module.WidgetApp._apply_config(stub, config)

    assert (stub._unread == 0) is cleared
    assert (tray.unread == 0) is cleared


def test_tray_right_click_is_not_an_acknowledgement():
    stub, window, tray, toggled = _tray_click_stub(2)

    app_module.WidgetApp._on_tray_activated(stub, QSystemTrayIcon.Context)

    assert (stub._unread, tray.unread, window.cleared) == (2, 2, 0)
    assert toggled == []


def test_window_activation_clears_tray_unread_without_clearing_window_bell():
    """从后台切回行情窗口等同看过托盘提醒，但 BELL 历史仍保留。"""
    window, tray = _FakeWindow(), _FakeTray()
    tray.unread = 4
    stub = SimpleNamespace(window=window, tray=tray, _unread=4)
    stub._clear_unread = lambda: app_module.WidgetApp._clear_unread(stub)

    app_module.WidgetApp._on_window_activated(stub)

    assert stub._unread == 0
    assert tray.unread == 0
    assert window.cleared == 0


def test_terminal_foreground_transition_clears_tray_unread(monkeypatch):
    window, tray = _FakeWindow(), _FakeTray()
    tray.unread = 5
    stub = SimpleNamespace(
        window=window,
        tray=tray,
        _unread=5,
        _terminal_was_foreground=False,
    )
    stub._clear_unread = lambda: app_module.WidgetApp._clear_unread(stub)
    monkeypatch.setattr(app_module.desktop, "terminal_is_foreground", lambda: True)

    app_module.WidgetApp._poll_terminal_foreground(stub)

    assert stub._unread == 0
    assert tray.unread == 0
    assert stub._terminal_was_foreground is True


def test_notification_does_not_create_unread_while_terminal_is_foreground(monkeypatch):
    window, tray = _FakeWindow(), _FakeTray()
    stub = SimpleNamespace(
        config=Config(), window=window, tray=tray, _unread=0, poller=_FakePoller()
    )
    stub._clear_unread = lambda: app_module.WidgetApp._clear_unread(stub)
    monkeypatch.setattr(app_module.desktop, "ring_terminal_bell", lambda: True)
    monkeypatch.setattr(app_module.desktop, "terminal_is_foreground", lambda: True)

    app_module.WidgetApp._on_mcp_notification(stub, NOTIFICATION)

    assert stub._unread == 0
    assert tray.unread == 0


def test_theme_switch_saves_old_geometry_and_restores_target_profile(tmp_path):
    store = Store(tmp_path / "config.json")
    old_config = store.update(
        {
            "font_size": 10,
            "bounds": {
                "x": 10,
                "y": 20,
                "width": 500,
                "height": 200,
                "scale": 1.2,
                "manual_size": True,
            },
        }
    )
    target_config = store.update(
        {
            "display_theme": "theme2",
            "font_size": 18,
            "bounds": {
                "x": 300,
                "y": 320,
                "width": 420,
                "height": 760,
                "scale": 1.0,
                "manual_size": True,
            },
        }
    )

    class FakeThemeWindow(_FakeWindow):
        def __init__(self):
            super().__init__()
            self.applied = []
            self.restored = []
            self.captured = 0

        def current_bounds(self, *, stop_pending=False):
            assert stop_pending is True
            self.captured += 1
            return {
                "x": 111,
                "y": 222,
                "width": 777,
                "height": 333,
                "scale": 1.35,
                "manual_size": True,
            }

        def apply_config(self, config):
            self.applied.append(config)

        def restore_bounds(self, bounds, available):
            self.restored.append((bounds, available))

    window = FakeThemeWindow()
    screen = SimpleNamespace(availableGeometry=lambda: "screen-geometry")
    stub = SimpleNamespace(
        config=old_config,
        store=store,
        window=window,
        qt=SimpleNamespace(screens=lambda: [screen]),
        tray=None,
        _unread=0,
        poller=SimpleNamespace(apply_config=lambda c: None),
        notification_listener=SimpleNamespace(apply_config=lambda c: None),
    )
    stub._clear_unread = lambda: None

    app_module.WidgetApp._apply_config(stub, target_config)

    assert window.captured == 1
    assert window.applied[-1].display_theme == "theme2"
    restored_bounds, available = window.restored[-1]
    assert restored_bounds is not None
    assert (restored_bounds.x, restored_bounds.y, restored_bounds.width, restored_bounds.height) == (
        300,
        320,
        420,
        760,
    )
    assert available == ["screen-geometry"]

    persisted = store.get()
    assert persisted.display_theme == "theme2"
    old_bounds = persisted.theme_profiles["theme1"]["bounds"]
    assert (old_bounds["x"], old_bounds["y"], old_bounds["width"], old_bounds["height"]) == (
        111,
        222,
        777,
        333,
    )
