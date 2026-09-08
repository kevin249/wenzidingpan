"""提醒分发：终端铃铛 / 系统通知 / 通知区图标 / 窗口 BELL 四路各走各的开关。

直接拿未绑定的 ``_on_mcp_notification`` 配一个假的 self 来跑：分发逻辑本身
不碰 Qt，这样测就不用去和 QApplication 单例较劲。
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
from stockwidget.config import Config
from stockwidget.mcp_notifications import McpNotification

NOTIFICATION = McpNotification(
    event_id="e1", title="盯盘提醒", body="600519 涨停封板", created_at="09:31"
)


class _FakeWindow:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.cleared = 0

    def show_mcp_notification(self, title: str, body: str) -> None:
        self.calls.append((title, body))

    def clear_mcp_notifications(self) -> None:
        self.cleared += 1

    def apply_config(self, config: Config) -> None:
        pass


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


@pytest.fixture()
def dispatch(monkeypatch):
    """按给定配置跑 n 次提醒回调，返回四路各自的结果。"""

    def run(config: Config, *, tray: bool = True, times: int = 1):
        rung: list[bool] = []
        monkeypatch.setattr(
            app_module.desktop, "ring_terminal_bell", lambda: bool(rung.append(True))
        )

        window = _FakeWindow()
        tray_icon = _FakeTray() if tray else None
        stub = SimpleNamespace(config=config, window=window, tray=tray_icon, _unread=0)
        for _ in range(times):
            app_module.WidgetApp._on_mcp_notification(stub, NOTIFICATION)
        return SimpleNamespace(
            bell=len(rung),
            toast=len(tray_icon.calls) if tray_icon else 0,
            tray_icon=tray_icon.unread if tray_icon else 0,
            window=len(window.calls),
        )

    return run


def _channels(result):
    return (result.bell, result.toast, result.tray_icon, result.window)


def test_all_four_channels_fire_by_default(dispatch):
    assert _channels(dispatch(Config())) == (1, 1, 1, 1)


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
    """图标要一直挂着未读数，不像气泡闪一下就没了。"""
    assert dispatch(Config(), times=3).tray_icon == 3


def test_all_switches_off_still_prints_the_body(dispatch, capsys):
    """四路全关只是不提示；正文是排查用的，照打不误。"""
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
    """连着来的两条推送不能在终端里挤成一坨——中间要能一眼看出分界。"""
    dispatch(Config(), times=2)

    printed = capsys.readouterr().out
    # 上一条正文之后：1 个空行、一行 ======、再 1 个空行，然后才是下一条的标题行。
    assert "600519 涨停封板\n\n======\n\n[MCP提醒]" in printed


def test_tray_channels_are_skipped_without_a_tray(dispatch):
    """没有系统托盘的桌面上，气泡和图标两路都得安静跳过，不能抛异常。"""
    assert _channels(dispatch(Config(), tray=False)) == (1, 0, 0, 1)


def _tray_click_stub(unread: int):
    window, tray = _FakeWindow(), _FakeTray()
    tray.unread = unread
    toggled: list[bool] = []
    stub = SimpleNamespace(
        window=window, tray=tray, _unread=unread, toggle_window=lambda: toggled.append(True)
    )
    stub._clear_unread = lambda: app_module.WidgetApp._clear_unread(stub)
    return stub, window, tray, toggled


def test_tray_click_acknowledges_both_indicators():
    """点托盘要把两处未读一起清。

    只清托盘的话，同一个动作紧接着打开的窗口还挂着 BELL·N——那几条恰恰是这一
    下刚标记成已读的。
    """
    stub, window, tray, toggled = _tray_click_stub(3)

    app_module.WidgetApp._on_tray_activated(stub, QSystemTrayIcon.Trigger)

    assert (stub._unread, tray.unread, window.cleared) == (0, 0, 1)
    assert toggled == [True]


@pytest.mark.parametrize(
    "config, cleared",
    [
        (Config(mcp_notifications_enabled=True), False),  # 都开着，未读留着
        (Config(mcp_notifications_enabled=False), True),  # 总开关关掉
        (Config(mcp_notifications_enabled=True, mcp_bell_tray_icon=False), True),  # 单独关掉
    ],
)
def test_tray_indicator_follows_both_the_master_and_its_own_switch(config, cleared):
    """总开关一关监听就停，之后再没有提醒能清掉告警色——图标会一直红着。

    判据要和窗口 BELL 那边一致：总开关 and 自己那一路的开关。
    """
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
    """右键弹菜单不等于看过了，未读数得原样留着。"""
    stub, window, tray, toggled = _tray_click_stub(2)

    app_module.WidgetApp._on_tray_activated(stub, QSystemTrayIcon.Context)

    assert (stub._unread, tray.unread, window.cleared) == (2, 2, 0)
    assert toggled == []
