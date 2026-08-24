"""配置：以 JSON 文件存储，读写两侧都按 schema 校验。

配置来源有两个——磁盘上的 JSON 和 WebUI 提交的表单，都不可信，
统一走 :func:`sanitize` 做类型检查与区间夹取后再使用。
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .providers import DEFAULT_PROVIDER

APP_DIR_NAME = "stock-ticker-widget"
CONFIG_FILE_NAME = "config.json"

LAYOUTS = ("multi", "single")
COLOR_SCHEMES = ("cn", "us")
# 字体名允许中英文、数字、空格、引号、逗号和连字符，挡掉可能破坏样式声明的字符。
FONT_FAMILY_RE = re.compile(r"^[\w \-,'\"一-鿿]{0,120}$")
HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

MAX_SYMBOLS = 50


def config_dir() -> Path:
    """按平台惯例给出配置目录。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / APP_DIR_NAME


def config_path() -> Path:
    return config_dir() / CONFIG_FILE_NAME


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _as_number(value: Any) -> float | None:
    """只接受真正的数字，布尔值在 Python 里是 int 的子类，必须排除。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value and abs(value) != float("inf") else None


@dataclass
class Bounds:
    x: int
    y: int
    width: int
    height: int
    # 缩放不能从窗口绝对宽度反推：多列网格本身就可能很宽。
    scale: float = 1.0
    # 只有用户拖过右下角把手，才把保存的宽高视为用户指定尺寸。
    manual_size: bool = False


@dataclass
class Config:
    provider: str = DEFAULT_PROVIDER
    symbols: list[str] = field(default_factory=lambda: ["600519", "000001", "300750", "601318"])
    refresh_seconds: int = 5
    color_scheme: str = "cn"  # cn = 红涨绿跌，us = 绿涨红跌
    opacity: float = 0.95  # 整窗透明度，文字也会跟着变淡
    background_color: str = "#11141c"
    background_alpha: float = 0.82  # 只影响背景板；调到 0 就只剩文字和曲线浮在桌面上
    click_through: bool = False  # 鼠标穿透，只留左上角把手可拖动
    always_on_top: bool = True
    show_sparkline: bool = True
    show_sparkline_fill: bool = False
    show_bs_points: bool = True
    show_open_line: bool = True
    show_high_low: bool = True
    show_stock_name: bool = True
    show_stock_price: bool = True
    grayscale: bool = False
    # 走势图画当日分时曲线（联网取分钟数据）；关掉则只画组件运行期间的采样点
    intraday_chart: bool = True
    show_dark_trade: bool = True
    compact: bool = False
    layout: str = "multi"  # multi = 多行列表，single = 单行滚动
    visible_rows: int = 4
    font_family: str = ""  # 留空表示跟随系统字体
    font_size: int = 13  # 按钮与间距的基础字号
    stock_name_font_size: int = 12
    stock_price_font_size: int = 15
    stock_percent_font_size: int = 11
    dark_trade_font_size: int = 10
    chart_label_font_size: int = 9
    # ``auto`` 表示沿用该数据原有的涨跌色；十六进制颜色表示固定色。
    stock_name_color: str = "#000000"
    stock_price_color: str = "auto"
    stock_percent_color: str = "auto"
    dark_trade_color: str = "#000000"
    stock_name_bold: bool = False
    stock_price_bold: bool = True
    stock_percent_bold: bool = False
    dark_trade_bold: bool = False
    bounds: Bounds | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sanitize(raw: Any) -> Config:
    """把任意输入收敛成一份合法配置，非法字段回落到默认值。"""
    out = Config()
    if not isinstance(raw, dict):
        return out

    if isinstance(raw.get("provider"), str):
        out.provider = raw["provider"]

    symbols = raw.get("symbols")
    if isinstance(symbols, str):  # WebUI 里是多行文本框
        symbols = re.split(r"[\n,，;；\s]+", symbols)
    if isinstance(symbols, list):
        seen: list[str] = []
        for item in symbols:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if text and text not in seen:
                seen.append(text)
        if seen:
            out.symbols = seen[:MAX_SYMBOLS]

    refresh = _as_number(raw.get("refresh_seconds"))
    if refresh is not None:
        out.refresh_seconds = int(_clamp(round(refresh), 1, 3600))

    if raw.get("color_scheme") in COLOR_SCHEMES:
        out.color_scheme = raw["color_scheme"]
    if raw.get("layout") in LAYOUTS:
        out.layout = raw["layout"]

    opacity = _as_number(raw.get("opacity"))
    if opacity is not None:
        out.opacity = round(_clamp(opacity, 0.2, 1.0), 2)

    background_alpha = _as_number(raw.get("background_alpha"))
    if background_alpha is not None:
        out.background_alpha = round(_clamp(background_alpha, 0.0, 1.0), 2)

    color = raw.get("background_color")
    if isinstance(color, str) and HEX_COLOR_RE.match(color.strip()):
        out.background_color = color.strip().lower()

    rows = _as_number(raw.get("visible_rows"))
    if rows is not None:
        out.visible_rows = int(_clamp(round(rows), 1, 30))

    size = _as_number(raw.get("font_size"))
    if size is not None:
        out.font_size = int(_clamp(round(size), 9, 28))

    font_defaults = {
        "stock_name_font_size": round(out.font_size * 0.95),
        "stock_price_font_size": round(out.font_size * 1.15),
        "stock_percent_font_size": round(out.font_size * 0.85),
        "dark_trade_font_size": round(out.font_size * 0.75),
        "chart_label_font_size": round(out.font_size * 0.7),
    }
    for key, legacy_default in font_defaults.items():
        size = _as_number(raw.get(key))
        if size is not None:
            setattr(out, key, int(_clamp(round(size), 7, 48)))
        elif key not in raw:
            # 旧配置只有 font_size，首次升级时沿用之前各类文字的倍率。
            setattr(out, key, int(_clamp(legacy_default, 7, 48)))

    for key in (
        "stock_name_color",
        "stock_price_color",
        "stock_percent_color",
        "dark_trade_color",
    ):
        color = raw.get(key)
        if isinstance(color, str):
            color = color.strip().lower()
            if color == "auto":
                setattr(out, key, color)
            elif HEX_COLOR_RE.match(color):
                # HTML color 控件只接受 #rrggbb，简写 #rgb 在这里展开。
                normalized = color if len(color) == 7 else "#" + "".join(c * 2 for c in color[1:])
                setattr(out, key, normalized)

    font = raw.get("font_family")
    if isinstance(font, str) and FONT_FAMILY_RE.match(font.strip()):
        out.font_family = font.strip()

    for key in (
        "always_on_top",
        "show_sparkline",
        "show_sparkline_fill",
        "show_bs_points",
        "show_open_line",
        "show_high_low",
        "show_stock_name",
        "show_stock_price",
        "grayscale",
        "intraday_chart",
        "show_dark_trade",
        "compact",
        "click_through",
        "stock_name_bold",
        "stock_price_bold",
        "stock_percent_bold",
        "dark_trade_bold",
    ):
        if isinstance(raw.get(key), bool):
            setattr(out, key, raw[key])

    bounds = raw.get("bounds")
    if isinstance(bounds, dict):
        values = {k: _as_number(bounds.get(k)) for k in ("x", "y", "width", "height")}
        if all(v is not None for v in values.values()):
            scale = _as_number(bounds.get("scale"))
            out.bounds = Bounds(
                x=int(values["x"]),
                y=int(values["y"]),
                width=int(_clamp(round(values["width"]), 200, 4000)),
                height=int(_clamp(round(values["height"]), 56, 4000)),
                scale=round(_clamp(scale if scale is not None else 1.0, 0.6, 3.0), 3),
                manual_size=bounds.get("manual_size") is True,
            )

    return out


class Store:
    """配置的唯一读写入口：内存里保存一份，落盘时同样写 JSON。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else config_path()
        self._config = sanitize(self._read())

    def _read(self) -> Any:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 首次启动或文件损坏，都退回默认配置。
            return {}

    def get(self) -> Config:
        return sanitize(self._config.to_dict())

    def update(self, patch: dict[str, Any]) -> Config:
        """合并补丁并落盘，返回生效后的完整配置。"""
        merged = self._config.to_dict()
        merged.update(patch or {})
        self._config = sanitize(merged)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._config.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:  # 只读文件系统等情况下不该让程序崩掉
            print(f"[config] 配置写入失败: {exc}", file=sys.stderr)
        return self.get()
