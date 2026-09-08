"""提醒分发：终端铃铛 / 系统通知 / 窗口 BELL 三路各走各的开关。

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


class _Recorder:
    """假的窗口 / 托盘，只记下自己有没有被叫到。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def show_mcp_notification(self, title: str, body: str) -> None:
        self.calls.append((title, body))

    def notify(self, title: str, body: str) -> bool:
        self.calls.append((title, body))
        return True


@pytest.fixture()
def dispatch(monkeypatch):
    """按给定配置跑一次提醒回调，返回三路各自收到的调用。"""

    def run(config: Config, *, tray: bool = True):
        rung: list[bool] = []
        monkeypatch.setattr(app_module.desktop, "ring_terminal_bell", lambda: bool(rung.append(True)))

        window = _Recorder()
        tray_icon = _Recorder() if tray else None
        stub = SimpleNamespace(config=config, window=window, tray=tray_icon)
        app_module.WidgetApp._on_mcp_notification(stub, NOTIFICATION)
        return SimpleNamespace(
            bell=len(rung),
            toast=len(tray_icon.calls) if tray_icon else 0,
            window=len(window.calls),
        )

    return run


def test_all_three_channels_fire_by_default(dispatch):
    result = dispatch(Config())
    assert (result.bell, result.toast, result.window) == (1, 1, 1)


@pytest.mark.parametrize(
    "switch, expected",
    [
        ("mcp_bell_terminal", (0, 1, 1)),
        ("mcp_bell_toast", (1, 0, 1)),
        ("mcp_bell_window", (1, 1, 0)),
    ],
)
def test_each_switch_only_silences_its_own_channel(dispatch, switch, expected):
    result = dispatch(Config(**{switch: False}))
    assert (result.bell, result.toast, result.window) == expected


def test_all_switches_off_still_prints_the_body(dispatch, capsys):
    """三路全关只是不提示；正文是排查用的，照打不误。"""
    result = dispatch(
        Config(mcp_bell_terminal=False, mcp_bell_toast=False, mcp_bell_window=False)
    )
    assert (result.bell, result.toast, result.window) == (0, 0, 0)

    printed = capsys.readouterr().out
    assert "盯盘提醒" in printed and "600519 涨停封板" in printed


def test_toast_is_skipped_without_a_tray(dispatch):
    """没有系统托盘的桌面上不能因为要弹气泡就抛异常。"""
    result = dispatch(Config(), tray=False)
    assert (result.bell, result.toast, result.window) == (1, 0, 1)
