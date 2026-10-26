"""区域尺寸偏好：有空间时使用指定像素，窗口缩小时仍可压缩。"""

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QLabel, QWidget

MAX_WIDGET_SIZE = 16777215


class SizeHintMixin:
    def set_display_size(self, size: dict[str, int]) -> None:
        width, height = size.get("width", 0), size.get("height", 0)
        changed = (width, height) != (
            getattr(self, "_display_width", 0), getattr(self, "_display_height", 0)
        )
        self._display_width, self._display_height = width, height
        if self._display_width:
            self.setMinimumWidth(0)
        if self._display_height:
            self.setMinimumHeight(0)
        self.setMaximumWidth(self._display_width or MAX_WIDGET_SIZE)
        self.setMaximumHeight(self._display_height or MAX_WIDGET_SIZE)
        if changed:
            self.updateGeometry()

    def _display_size_hint(self, hint: QSize) -> QSize:
        return QSize(
            getattr(self, "_display_width", 0) or hint.width(),
            getattr(self, "_display_height", 0) or hint.height(),
        )

    def sizeHint(self) -> QSize:  # noqa: N802
        return self._display_size_hint(super().sizeHint())


class SizedLabel(SizeHintMixin, QLabel):
    pass


class SizedWidget(SizeHintMixin, QWidget):
    pass
