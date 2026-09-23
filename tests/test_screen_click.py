"""ScreenClickWatcher 的兜底分支：钩子装不上时退化为应用内事件过滤。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

try:
    from PySide6.QtCore import QEvent, QPointF, QPoint, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication, QWidget
except (ImportError, OSError) as error:
    pytest.skip(f"Qt 运行库不可用：{error}", allow_module_level=True)

from stockwidget.ui.screen_click import ScreenClickWatcher


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _press(global_pos: QPoint) -> QMouseEvent:
    return QMouseEvent(
        QEvent.MouseButtonPress,
        QPointF(3.0, 4.0),
        QPointF(float(global_pos.x()), float(global_pos.y())),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )


def test_watcher_falls_back_to_the_app_filter_when_hook_is_unavailable(app):
    widget = QWidget()
    watcher = ScreenClickWatcher()
    # 钩子装不上（权限/会话/非 win32）→ 应用内过滤兜底。
    watcher._install_hook = lambda: False
    seen = []
    watcher.clicked.connect(seen.append)

    watcher.start()
    assert watcher.active() is True

    app.sendEvent(widget, _press(QPoint(120, 80)))
    assert seen == [QPoint(120, 80)]

    # stop 之后同一事件不再上报。
    watcher.stop()
    app.sendEvent(widget, _press(QPoint(200, 60)))
    assert seen == [QPoint(120, 80)]
    assert watcher.active() is False

    widget.deleteLater()


def test_watcher_start_and_stop_are_idempotent(app):
    watcher = ScreenClickWatcher()
    watcher._install_hook = lambda: False

    watcher.start()
    watcher.start()
    assert watcher.active() is True

    watcher.stop()
    watcher.stop()
    assert watcher.active() is False
