"""托盘菜单：窗口上的按钮可以藏起来，托盘得留着把它们开回来的入口。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

try:
    from PySide6.QtWidgets import QApplication
except (ImportError, OSError) as error:
    pytest.skip(f"Qt 运行库不可用：{error}", allow_module_level=True)

from stockwidget.config import Config
from stockwidget.ui.icon import unread_badge_text
from stockwidget.ui.tray import BASE_TOOLTIP, Tray, message_body


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_tray_can_bring_back_hidden_title_buttons(app):
    tray = Tray(Config())

    assert tray.title_buttons_action in tray.contextMenu().actions()
    assert tray.title_buttons_action.isChecked() is True

    emitted: list[bool] = []
    tray.title_buttons_action.toggled.connect(emitted.append)
    tray.title_buttons_action.setChecked(False)  # 模拟用户点菜单
    assert emitted == [False]

    # 回填状态必须屏蔽信号，否则托盘与配置会来回互相触发。
    emitted.clear()
    tray.apply_config(Config(show_title_buttons=False))
    assert tray.title_buttons_action.isChecked() is False
    tray.apply_config(Config(show_title_buttons=True))
    assert tray.title_buttons_action.isChecked() is True
    assert emitted == []


def test_notification_body_is_flattened_and_never_empty():
    """Windows 收到空正文的通知会直接不弹，必须有兜底。"""
    assert message_body("600519\n  涨停  ") == "600519 涨停"
    assert message_body("") == "收到新的实时提醒"
    assert len(message_body("长" * 500)) == 240


def test_tray_notify_sends_a_balloon_and_stays_quiet_when_unsupported(app, monkeypatch):
    tray = Tray(Config())
    sent: list[tuple] = []
    monkeypatch.setattr(tray, "showMessage", lambda *args: sent.append(args))

    monkeypatch.setattr(tray, "supportsMessages", lambda: True)
    assert tray.notify("  盯盘提醒  ", "600519\n涨停") is True
    assert sent[0][0] == "盯盘提醒"
    assert sent[0][1] == "600519 涨停"

    # 平台不支持气泡就安静收手，不能让提醒回调抛异常
    sent.clear()
    monkeypatch.setattr(tray, "supportsMessages", lambda: False)
    assert tray.notify("标题", "正文") is False
    assert sent == []


def test_unread_badge_text_caps_only_the_tiny_icon_label():
    assert unread_badge_text(0) == ""
    assert unread_badge_text(1) == "1"
    assert unread_badge_text(99) == "99"
    assert unread_badge_text(100) == "99+"


def test_tray_unread_switches_the_icon_and_tooltip(app):
    """未读数直接画在通知区图标上，并一直累计到用户点击确认。"""
    tray = Tray(Config())
    calm = tray.icon().pixmap(64, 64).toImage()
    assert tray.toolTip() == BASE_TOOLTIP

    assert tray.set_unread(2) == 2
    badge_two = tray.icon().pixmap(64, 64).toImage()
    assert badge_two != calm
    assert "2 条未读提醒" in tray.toolTip()

    # 数字变化必须让图标本身变化，而不只是 tooltip / 告警颜色变化。
    assert tray.set_unread(3) == 3
    badge_three = tray.icon().pixmap(64, 64).toImage()
    assert badge_three != badge_two
    assert "3 条未读提醒" in tray.toolTip()

    # 点开看过之后退回常态
    tray.set_unread(0)
    assert tray.icon().pixmap(64, 64).toImage() == calm
    assert tray.toolTip() == BASE_TOOLTIP

    # 负数当成清零；小图标超过 99 显示 99+，tooltip 仍保留真实累计数。
    assert tray.set_unread(-3) == 0
    assert tray.set_unread(150) == 150
    assert "150 条未读提醒" in tray.toolTip()
    assert unread_badge_text(150) == "99+"
