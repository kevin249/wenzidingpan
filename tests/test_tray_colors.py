"""通知区图标颜色配置回归测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

try:
    from PySide6.QtWidgets import QApplication
except (ImportError, OSError) as error:
    pytest.skip(f"Qt 运行库不可用：{error}", allow_module_level=True)

from stockwidget.config import Config, sanitize
from stockwidget.ui.tray import Tray


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_tray_colors_default_to_white_and_blue():
    config = Config()
    assert config.tray_icon_normal_color == "#ffffff"
    assert config.tray_icon_alert_color == "#3b82f6"


def test_tray_color_config_is_sanitized():
    config = sanitize(
        {
            "tray_icon_normal_color": "#abc",
            "tray_icon_alert_color": "#123456",
        }
    )
    assert config.tray_icon_normal_color == "#aabbcc"
    assert config.tray_icon_alert_color == "#123456"

    invalid = sanitize(
        {
            "tray_icon_normal_color": "white",
            "tray_icon_alert_color": "not-a-color",
        }
    )
    assert invalid.tray_icon_normal_color == "#ffffff"
    assert invalid.tray_icon_alert_color == "#3b82f6"


def test_tray_uses_normal_and_alert_colors_and_updates_them_live(app):
    config = Config(tray_icon_normal_color="#ffffff", tray_icon_alert_color="#3b82f6")
    tray = Tray(config)
    normal = tray.icon().pixmap(64, 64).toImage()

    tray.set_unread(1)
    alert = tray.icon().pixmap(64, 64).toImage()
    assert alert != normal

    tray.apply_config(
        Config(tray_icon_normal_color="#00ff00", tray_icon_alert_color="#ff00ff")
    )
    recolored_alert = tray.icon().pixmap(64, 64).toImage()
    assert recolored_alert != alert

    tray.set_unread(0)
    recolored_normal = tray.icon().pixmap(64, 64).toImage()
    assert recolored_normal != normal
