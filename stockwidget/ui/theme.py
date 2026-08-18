"""配色、字体与数字格式化。"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont

from ..config import Config

BACKGROUND = QColor(17, 20, 28, 209)  # 约 82% 不透明
BORDER = QColor(255, 255, 255, 26)
TEXT = QColor(232, 234, 240)
MUTED = QColor(139, 147, 167)
FLAT = QColor(154, 163, 184)
HOVER = QColor(255, 255, 255, 13)
RED = QColor(240, 79, 90)
GREEN = QColor(34, 197, 94)


def up_color(config: Config) -> QColor:
    """cn = 红涨绿跌（A 股习惯），us = 绿涨红跌（欧美习惯）。"""
    return RED if config.color_scheme == "cn" else GREEN


def down_color(config: Config) -> QColor:
    return GREEN if config.color_scheme == "cn" else RED


def direction_color(config: Config, change: float | None) -> QColor:
    if change is None:
        return MUTED
    if change > 0:
        return up_color(config)
    if change < 0:
        return down_color(config)
    return FLAT


def make_font(config: Config, scale: float = 1.0, bold: bool = False) -> QFont:
    """所有字号都由配置里的基准字号按比例推出来，改字号时整体等比缩放。"""
    font = QFont()
    if config.font_family:
        font.setFamilies([f.strip() for f in config.font_family.split(",") if f.strip()])
    font.setPixelSize(max(7, round(config.font_size * scale)))
    font.setBold(bold)
    return font


def fmt_price(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}" if abs(value) >= 1 else f"{value:,.4f}"


def fmt_change(change: float | None, percent: float | None) -> str:
    if change is None or percent is None:
        return ""
    sign = "+" if change > 0 else ""
    return f"{sign}{change:.2f}  {sign}{percent:.2f}%"


def fmt_money(yuan: float | None) -> str | None:
    """暗盘资金接口给的是元，按东财口径折算成万 / 亿。"""
    if yuan is None:
        return None
    sign = "+" if yuan > 0 else "-" if yuan < 0 else ""
    magnitude = abs(yuan)
    if magnitude >= 1e8:
        return f"{sign}{magnitude / 1e8:.2f}亿"
    return f"{sign}{magnitude / 1e4:.2f}万"
