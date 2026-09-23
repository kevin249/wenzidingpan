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
from urllib.parse import urlsplit

from .providers import DEFAULT_PROVIDER

APP_DIR_NAME = "stock-ticker-widget"
CONFIG_FILE_NAME = "config.json"

LAYOUTS = ("multi", "single")
DISPLAY_THEMES = ("theme1", "theme2")
THEME2_SIDES = ("right", "left")
# sides = 左中右（左右各两行文字），stacked = 上中下（上下各一行文字）
ROW_STYLES = ("sides", "stacked")
COLOR_SCHEMES = ("cn", "us")

# 只把“显示/窗口外观”按主题隔离。数据源、自选、刷新频率、Debug、
# MCP/托盘提醒仍是全局共享配置，切主题不会复制或分叉这些运行状态。
THEME_SCOPED_FIELDS = (
    "color_scheme",
    "opacity",
    "background_color",
    "background_alpha",
    "click_through",
    "always_on_top",
    "show_title_buttons",
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
    "theme2_side",
    "theme2_depth_width",
    "theme2_popup_font_size",
    "layout",
    "row_style",
    "visible_rows",
    "chart_height",
    "font_family",
    "font_size",
    "stock_name_font_size",
    "stock_price_font_size",
    "stock_percent_font_size",
    "dark_trade_font_size",
    "chart_label_font_size",
    "stock_name_color",
    "stock_price_color",
    "stock_percent_color",
    "dark_trade_color",
    "stock_name_bold",
    "stock_price_bold",
    "stock_percent_bold",
    "dark_trade_bold",
    "bounds",
)
# 字体名允许中英文、数字、空格、引号、逗号和连字符，挡掉可能破坏样式声明的字符。
FONT_FAMILY_RE = re.compile(r"^[\w \-,'\"一-鿿]{0,120}$")
HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

MAX_SYMBOLS = 50

# 整窗透明度下限。留一丝可见度而不是 0：关掉鼠标穿透时，全隐的窗口照样拦鼠标，
# 桌面上就多出一块看不见又点不穿的死区。想彻底看不见请配合「鼠标穿透」使用。
# WebUI 的滑块下限也读这个值，两边不会再各写一份。
MIN_OPACITY = 0.05


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
    # Debug 模式绕过交易时段限制，便于收盘后调试实时行情 / K线 / MCP深度。
    debug_mode: bool = False
    color_scheme: str = "cn"  # cn = 红涨绿跌，us = 绿涨红跌
    opacity: float = 0.95  # 整窗透明度，文字也会跟着变淡
    background_color: str = "#11141c"
    background_alpha: float = 0.82  # 只影响背景板；调到 0 就只剩文字和曲线浮在桌面上
    click_through: bool = False  # 鼠标穿透，只留左上角把手可拖动
    always_on_top: bool = True
    # 右上角的刷新 / 设置 / 灰度 / 退出四个按钮；关掉后整条标题栏一起收起，
    # 窗口只剩行情内容。改配置、刷新和退出仍可从托盘菜单和设置页进行。
    show_title_buttons: bool = True
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
    display_theme: str = "theme1"  # theme1 = 经典网格，theme2 = 千档竖列
    theme2_side: str = "right"  # right = 靠右/向左展，left = 靠左镜像/向右展
    theme2_depth_width: int = 84  # 主题2挂单分布区域宽度（不含股价文字区），像素
    theme2_popup_font_size: int = 11  # 主题2弹出详情四行文字与K线标注字号
    layout: str = "multi"  # multi = 多行列表，single = 单行滚动
    # sides = 左中右：左侧名称/暗盘两行，右侧现价/涨跌幅两行，走势图在中间；
    # stacked = 上中下：名称与现价同一行，暗盘与涨跌幅同一行，走势图永远在中间。
    row_style: str = "sides"
    visible_rows: int = 4
    chart_height: int = 0  # K 线（走势图）高度，0 表示按字号自动推算
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
    # 通知区图标颜色：默认常态白色、收到未读提醒时蓝色。
    tray_icon_normal_color: str = "#ffffff"
    tray_icon_alert_color: str = "#3b82f6"
    tray_unread_font_size: int = 14  # 托盘未读数字字号（64px 绘制画布上的像素）
    mcp_notifications_enabled: bool = False
    # 提醒到达时走哪几路提示，三路互不影响，都受上面那个总开关约束。
    # 正文打印到 CMD 不在此列——那是排查用的，始终打印。
    mcp_bell_terminal: bool = True  # 往终端敲 BEL：响一声，并让终端去闪任务栏
    mcp_bell_toast: bool = True  # 系统通知气泡 / Toast
    mcp_bell_tray_icon: bool = True  # 通知区（任务栏）图标转提醒色并挂未读数
    mcp_bell_window: bool = True  # 在窗口标题栏 BELL 按钮上累计未读数
    mcp_url: str = "http://127.0.0.1:8801/mcp"
    mcp_api_key: str = ""
    bounds: Bounds | None = None
    # theme1/theme2 各保存一份显示参数；当前主题的 profile 会展开到上面的字段，
    # 所以现有 UI/绘制代码仍然只需读取 config.font_size / config.bounds 等。
    theme_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _theme_profile_from_config(config: Config) -> dict[str, Any]:
    serialized = config.to_dict()
    return {key: serialized.get(key) for key in THEME_SCOPED_FIELDS}


def _default_theme_profile(theme: str) -> dict[str, Any]:
    base = Config(display_theme=theme)
    return _theme_profile_from_config(base)


def sanitize(raw: Any, *, _include_theme_profiles: bool = True) -> Config:
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
    if raw.get("display_theme") in DISPLAY_THEMES:
        out.display_theme = raw["display_theme"]
    if raw.get("theme2_side") in THEME2_SIDES:
        out.theme2_side = raw["theme2_side"]
    if raw.get("layout") in LAYOUTS:
        out.layout = raw["layout"]
    if raw.get("row_style") in ROW_STYLES:
        out.row_style = raw["row_style"]

    opacity = _as_number(raw.get("opacity"))
    if opacity is not None:
        out.opacity = round(_clamp(opacity, MIN_OPACITY, 1.0), 2)

    background_alpha = _as_number(raw.get("background_alpha"))
    if background_alpha is not None:
        out.background_alpha = round(_clamp(background_alpha, 0.0, 1.0), 2)

    color = raw.get("background_color")
    if isinstance(color, str) and HEX_COLOR_RE.match(color.strip()):
        out.background_color = color.strip().lower()

    rows = _as_number(raw.get("visible_rows"))
    if rows is not None:
        out.visible_rows = int(_clamp(round(rows), 1, 30))

    chart_height = _as_number(raw.get("chart_height"))
    if chart_height is not None:
        # 0（含负数）保持「自动」，其余夹进一个还能看清曲线的区间。
        height = int(round(chart_height))
        out.chart_height = 0 if height <= 0 else int(_clamp(height, 8, 400))

    depth_width = _as_number(raw.get("theme2_depth_width"))
    if depth_width is not None:
        out.theme2_depth_width = int(_clamp(round(depth_width), 40, 600))

    size = _as_number(raw.get("font_size"))
    if size is not None:
        out.font_size = int(_clamp(round(size), 7, 28))

    font_defaults = {
        "stock_name_font_size": round(out.font_size * 0.95),
        "stock_price_font_size": round(out.font_size * 1.15),
        "stock_percent_font_size": round(out.font_size * 0.85),
        "dark_trade_font_size": round(out.font_size * 0.75),
        "chart_label_font_size": round(out.font_size * 0.7),
        "theme2_popup_font_size": round(out.font_size * 0.85),
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

    tray_unread_size = _as_number(raw.get("tray_unread_font_size"))
    if tray_unread_size is not None:
        out.tray_unread_font_size = int(_clamp(round(tray_unread_size), 7, 32))

    # 托盘颜色不支持 auto，只接受实际十六进制颜色。
    for key in ("tray_icon_normal_color", "tray_icon_alert_color"):
        color = raw.get(key)
        if isinstance(color, str):
            color = color.strip().lower()
            if HEX_COLOR_RE.match(color):
                normalized = color if len(color) == 7 else "#" + "".join(c * 2 for c in color[1:])
                setattr(out, key, normalized)

    font = raw.get("font_family")
    if isinstance(font, str) and FONT_FAMILY_RE.match(font.strip()):
        out.font_family = font.strip()

    mcp_url = raw.get("mcp_url")
    if isinstance(mcp_url, str):
        candidate = mcp_url.strip()
        parsed = urlsplit(candidate)
        if len(candidate) <= 2048 and parsed.scheme in {"http", "https"} and parsed.hostname:
            out.mcp_url = candidate

    mcp_api_key = raw.get("mcp_api_key")
    if isinstance(mcp_api_key, str):
        out.mcp_api_key = mcp_api_key.strip()[:512]

    for key in (
        "debug_mode",
        "always_on_top",
        "show_title_buttons",
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
        "mcp_notifications_enabled",
        "mcp_bell_terminal",
        "mcp_bell_toast",
        "mcp_bell_tray_icon",
        "mcp_bell_window",
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

    if _include_theme_profiles:
        raw_profiles = raw.get("theme_profiles")
        raw_profiles = raw_profiles if isinstance(raw_profiles, dict) else {}
        profiles: dict[str, dict[str, Any]] = {}
        for theme in DISPLAY_THEMES:
            source = raw_profiles.get(theme)
            if not isinstance(source, dict):
                # 旧版配置没有 profile：当前主题沿用旧扁平字段，另一个主题从默认值开始。
                source = (
                    {key: raw.get(key) for key in THEME_SCOPED_FIELDS if key in raw}
                    if theme == out.display_theme
                    else {}
                )
            candidate = sanitize(
                {**source, "display_theme": theme},
                _include_theme_profiles=False,
            )
            profiles[theme] = _theme_profile_from_config(candidate)

        # 这里只校验/保存 profile，不再偷偷反向覆盖 out 的扁平字段。
        # 当前主题什么时候加载自己的 profile，由 Store 显式控制。
        out.theme_profiles = profiles

    return out


class Store:
    """配置的唯一读写入口：内存里保存一份，落盘时同样写 JSON。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else config_path()
        raw = self._read()
        self._config = sanitize(raw)
        # 新版文件已经有两个 profile 时，启动时显式装载当前主题。
        # 旧版没有 profile 时，sanitize 已把旧扁平字段迁移进当前主题 profile，
        # 扁平字段本身也保持原值，无需再覆盖一次。
        if isinstance(raw, dict) and isinstance(raw.get("theme_profiles"), dict):
            self._config = self._activate_theme(self._config, self._config.display_theme)

    @staticmethod
    def _activate_theme(config: Config, theme: str) -> Config:
        """把指定主题 profile 显式展开到运行时扁平 Config。"""
        if theme not in DISPLAY_THEMES:
            theme = config.display_theme
        merged = config.to_dict()
        profiles = {
            name: dict(config.theme_profiles.get(name) or _default_theme_profile(name))
            for name in DISPLAY_THEMES
        }
        merged.update(profiles[theme])
        merged["display_theme"] = theme
        merged["theme_profiles"] = profiles
        return sanitize(merged)

    def _read(self) -> Any:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 首次启动或文件损坏，都退回默认配置。
            return {}

    def get(self) -> Config:
        return sanitize(self._config.to_dict())

    def _persist(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._config.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:  # 只读文件系统等情况下不该让程序崩掉
            print(f"[config] 配置写入失败: {exc}", file=sys.stderr)

    def update(self, patch: dict[str, Any]) -> Config:
        """普通更新直接作用当前 Config；只有切主题时才显式装载另一份 profile。"""
        patch = dict(patch or {})
        current = self._config
        current_theme = current.display_theme
        requested = patch.get("display_theme")
        target_theme = requested if requested in DISPLAY_THEMES else current_theme

        profiles = {
            theme: dict(current.theme_profiles.get(theme) or _default_theme_profile(theme))
            for theme in DISPLAY_THEMES
        }
        # 先把当前真实运行态写回当前 profile，避免 profile 落后于界面。
        profiles[current_theme] = _theme_profile_from_config(current)

        merged = current.to_dict()
        if target_theme != current_theme:
            # 主题切换只在这里发生：先装载目标 profile，再应用这次明确提交给目标主题的字段。
            merged.update(profiles[target_theme])

        scoped_patch = {
            key: value for key, value in patch.items() if key in THEME_SCOPED_FIELDS
        }
        merged.update(scoped_patch)
        profiles[target_theme].update(scoped_patch)

        for key, value in patch.items():
            if key not in THEME_SCOPED_FIELDS and key != "display_theme":
                merged[key] = value
        merged["display_theme"] = target_theme
        merged["theme_profiles"] = profiles

        self._config = sanitize(merged)
        # sanitize 只做校验，不再替换扁平字段；把最终合法运行态同步回当前 profile。
        profiles = {
            theme: dict(self._config.theme_profiles.get(theme) or _default_theme_profile(theme))
            for theme in DISPLAY_THEMES
        }
        profiles[target_theme] = _theme_profile_from_config(self._config)
        normalized = self._config.to_dict()
        normalized["theme_profiles"] = profiles
        self._config = sanitize(normalized)
        self._persist()
        return self.get()

    def update_theme_profile(self, theme: str, patch: dict[str, Any]) -> Config:
        """更新指定（可非当前）主题的显示 profile，不改变另一个主题。"""
        if theme not in DISPLAY_THEMES:
            return self.get()
        current = self._config
        profiles = {
            name: dict(current.theme_profiles.get(name) or _default_theme_profile(name))
            for name in DISPLAY_THEMES
        }
        profiles[current.display_theme] = _theme_profile_from_config(current)
        scoped_patch = {
            key: value for key, value in (patch or {}).items() if key in THEME_SCOPED_FIELDS
        }
        profiles[theme].update(scoped_patch)

        merged = current.to_dict()
        if theme == current.display_theme:
            merged.update(scoped_patch)
        merged["theme_profiles"] = profiles
        self._config = sanitize(merged)
        self._persist()
        return self.get()
