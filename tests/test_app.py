"""提醒分发：终端铃铛 / 系统通知 / 通知区图标 / 窗口 BELL 四路各走各的开关。

直接拿未绑定的 ``_on_mcp_notification`` 配一个假的 self 来跑：分发逻辑本身
不碰 Qt，这样测就不用去和 QApplication 单例较劲。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stockwidget import app as app_module
from stockwidget.config import Config
from stockwidget.mcp_notifications import McpNotification

NOTIFICATION = McpNotification(
    event_id="e1", title="盯盘提醒", body="600519 涨停封板", created_at="09:31"
)


class _FakeWindow:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def show_mcp_notification(self, title: str, body: str) -> None:
        self.calls.append((title, body))


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


def test_tray_channels_are_skipped_without_a_tray(dispatch):
    """没有系统托盘的桌面上，气泡和图标两路都得安静跳过，不能抛异常。"""
    assert _channels(dispatch(Config(), tray=False)) == (1, 0, 0, 1)
