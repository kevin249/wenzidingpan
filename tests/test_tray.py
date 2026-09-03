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
from stockwidget.ui.tray import Tray


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
