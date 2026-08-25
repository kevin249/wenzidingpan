"""配色、字体与数字格式化。"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont

from ..config import Config

BACKGROUND = QColor(17, 20, 28, 209)  # 约 82% 不透明
BORDER = QColor(255, 255, 255, 26)
TEXT = QColor(232, 234, 240)
BLACK = QColor(0, 0, 0)
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
    if config.grayscale:
        return FLAT if change else MUTED
    if change is None:
        return MUTED
    if change > 0:
        return up_color(config)
    if change < 0:
        return down_color(config)
    return FLAT


def configured_text_color(setting: str, automatic: QColor) -> QColor:
    """解析字体颜色设置；auto 保留行情原有的涨跌色。"""
    return QColor(automatic) if setting == "auto" else QColor(setting)


def make_font(
    config: Config,
    scale: float = 1.0,
    bold: bool = False,
    pixel_size: int | None = None,
) -> QFont:
    """创建界面字体；可指定独立字号，否则使用基础字号。"""
    font = QFont()
    if config.font_family:
        font.setFamilies([f.strip() for f in config.font_family.split(",") if f.strip()])
    base_size = config.font_size if pixel_size is None else pixel_size
    font.setPixelSize(max(7, round(base_size * scale)))
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
    """暗盘资金接口给的是元，一律折算成亿并保留两位小数。

    不足一亿的也写成 0.XX 亿，单位始终一致，扫一眼就能横向比大小。
    """
    if yuan is None:
        return None
    sign = "+" if yuan > 0 else "-" if yuan < 0 else ""
    return f"{sign}{abs(yuan) / 1e8:.2f}亿"
