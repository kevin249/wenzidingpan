"""配置校验：磁盘和 WebUI 送进来的数据都不可信。"""

from __future__ import annotations

import json

import pytest

from stockwidget.config import (
    DEFAULT_GRAYSCALE_LEVEL,
    GRAYSCALE_LEVEL_MAX,
    GRAYSCALE_LEVEL_MIN,
    MIN_OPACITY,
    Store,
    sanitize,
)


def test_empty_input_falls_back_to_defaults():
    for value in (None, {}, [], "nope", 42):
        config = sanitize(value)
        assert config.provider == "auto"
        assert config.symbols  # 自选列表不应为空


def test_out_of_range_values_are_clamped():
    config = sanitize(
        {
            "refresh_seconds": 1e9,
            "opacity": 42,
            "visible_rows": 999,
            "font_size": 2,
            "layout": "diagonal",
            "color_scheme": "nope",
        }
    )
    assert config.refresh_seconds == 3600
    assert config.opacity == 1.0
    assert config.visible_rows == 30
    assert config.font_size == 7
    assert config.layout == "multi"
    assert config.color_scheme == "cn"


def test_independent_font_sizes_are_clamped_and_preserved():
    config = sanitize(
        {
            "stock_name_font_size": 14,
            "stock_price_font_size": 20,
            "stock_percent_font_size": 12,
            "dark_trade_font_size": 2,
            "chart_label_font_size": 99,
        }
    )
    assert config.stock_name_font_size == 14
    assert config.stock_price_font_size == 20
    assert config.stock_percent_font_size == 12
    assert config.dark_trade_font_size == 7
    assert config.chart_label_font_size == 48


def test_base_font_size_below_nine_is_persisted(tmp_path):
    path = tmp_path / "config.json"
    store = Store(path)

    saved = store.update({"font_size": 7})

    assert saved.font_size == 7
    assert Store(path).get().font_size == 7


def test_tray_unread_font_size_is_clamped_and_persisted(tmp_path):
    path = tmp_path / "config.json"
    store = Store(path)

    assert sanitize({}).tray_unread_font_size == 14
    assert sanitize({"tray_unread_font_size": 2}).tray_unread_font_size == 7
    assert sanitize({"tray_unread_font_size": 99}).tray_unread_font_size == 32

    saved = store.update({"tray_unread_font_size": 24})
    assert saved.tray_unread_font_size == 24
    assert Store(path).get().tray_unread_font_size == 24


def test_mcp_notification_settings_are_sanitized_and_persisted(tmp_path):
    store = Store(tmp_path / "config.json")
    saved = store.update(
        {
            "mcp_notifications_enabled": True,
            "mcp_url": "http://127.0.0.1:8801/mcp",
            "mcp_api_key": "gmk_test-key",
        }
    )

    assert saved.mcp_notifications_enabled is True
    assert saved.mcp_api_key == "gmk_test-key"
    assert Store(store.path).get().mcp_notifications_enabled is True
    assert sanitize({"mcp_url": "file:///secret"}).mcp_url == "http://127.0.0.1:8801/mcp"


def test_mcp_bell_channels_default_on_and_toggle_independently(tmp_path):
    """三路提示各有开关；老配置里没有这几个键，得默认全开。"""
    fresh = sanitize({})
    assert (
        fresh.mcp_bell_terminal,
        fresh.mcp_bell_toast,
        fresh.mcp_bell_tray_icon,
        fresh.mcp_bell_window,
    ) == (True, True, True, True)

    store = Store(tmp_path / "config.json")
    saved = store.update({"mcp_bell_terminal": False, "mcp_bell_toast": False})
    assert saved.mcp_bell_terminal is False
    assert saved.mcp_bell_toast is False
    assert saved.mcp_bell_window is True  # 没动的那一路不受影响
    assert Store(store.path).get().mcp_bell_terminal is False

    # 非布尔值一律不认，回落到默认
    assert sanitize({"mcp_bell_window": "no"}).mcp_bell_window is True
    assert sanitize({"mcp_bell_tray_icon": 0}).mcp_bell_tray_icon is True


def test_display_theme_defaults_to_classic_and_accepts_theme2():
    assert sanitize({}).display_theme == "theme1"
    assert sanitize({"display_theme": "theme2"}).display_theme == "theme2"
    assert sanitize({"display_theme": "unknown"}).display_theme == "theme1"


def test_theme2_side_defaults_right_and_accepts_left_mirror():
    assert sanitize({}).theme2_side == "right"
    assert sanitize({"theme2_side": "left"}).theme2_side == "left"
    assert sanitize({"theme2_side": "inside"}).theme2_side == "right"


def test_theme2_depth_width_defaults_and_is_clamped():
    assert sanitize({}).theme2_depth_width == 84
    assert sanitize({"theme2_depth_width": 160}).theme2_depth_width == 160
    assert sanitize({"theme2_depth_width": 1}).theme2_depth_width == 40
    assert sanitize({"theme2_depth_width": 9999}).theme2_depth_width == 600
    assert sanitize({"theme2_depth_width": True}).theme2_depth_width == 84


def test_theme2_popup_font_size_defaults_and_is_clamped():
    assert sanitize({}).theme2_popup_font_size == 11
    assert sanitize({"theme2_popup_font_size": 16}).theme2_popup_font_size == 16
    assert sanitize({"theme2_popup_font_size": 1}).theme2_popup_font_size == 7
    assert sanitize({"theme2_popup_font_size": 100}).theme2_popup_font_size == 48
    assert sanitize({"theme2_popup_font_size": True}).theme2_popup_font_size == 11


def test_row_style_falls_back_to_left_middle_right():
    assert sanitize({}).row_style == "sides"
    assert sanitize({"row_style": "stacked"}).row_style == "stacked"
    assert sanitize({"row_style": "diagonal"}).row_style == "sides"


def test_chart_height_keeps_zero_as_automatic_and_clamps_the_rest():
    assert sanitize({}).chart_height == 0
    assert sanitize({"chart_height": 64}).chart_height == 64
    assert sanitize({"chart_height": 0}).chart_height == 0
    assert sanitize({"chart_height": -20}).chart_height == 0  # 负数同样视为自动
    assert sanitize({"chart_height": 3}).chart_height == 8
    assert sanitize({"chart_height": 9999}).chart_height == 400
    assert sanitize({"chart_height": True}).chart_height == 0


def test_independent_font_colors_and_weights_are_sanitized():
    config = sanitize(
        {
            "stock_name_color": "#112233",
            "stock_price_color": "AUTO",
            "stock_percent_color": "not-a-color",
            "dark_trade_color": "#ABC",
            "stock_name_bold": True,
            "stock_price_bold": False,
            "stock_percent_bold": True,
            "dark_trade_bold": True,
        }
    )

    assert config.stock_name_color == "#112233"
    assert config.stock_price_color == "auto"
    assert config.stock_percent_color == "auto"
    assert config.dark_trade_color == "#aabbcc"
    assert config.stock_name_bold is True
    assert config.stock_price_bold is False
    assert config.stock_percent_bold is True
    assert config.dark_trade_bold is True


def test_font_style_defaults_preserve_existing_appearance():
    config = sanitize({})
    assert config.stock_name_color == "#000000"
    assert config.stock_price_color == "auto"
    assert config.stock_percent_color == "auto"
    assert config.dark_trade_color == "#000000"
    assert config.stock_name_bold is False
    assert config.stock_price_bold is True
    assert config.stock_percent_bold is False
    assert config.dark_trade_bold is False


def test_legacy_base_font_size_initializes_independent_sizes_by_old_ratios():
    config = sanitize({"font_size": 10})
    assert config.stock_name_font_size == 10
    assert config.stock_price_font_size == 12
    assert config.stock_percent_font_size == 8
    assert config.dark_trade_font_size == 8
    assert config.chart_label_font_size == 7
    assert config.theme2_popup_font_size == 8


def test_booleans_are_not_accepted_as_numbers():
    # Python 里 bool 是 int 的子类，不加防护 True 会被当成 1 秒刷新
    assert sanitize({"refresh_seconds": True}).refresh_seconds == 5


@pytest.mark.parametrize(
    "raw,expected",
    [
        (["600519", "600519", "  000001 ", "", "   "], ["600519", "000001"]),
        ("600519\n000001, 300750;601318", ["600519", "000001", "300750", "601318"]),
    ],
)
def test_symbols_are_split_deduplicated_and_trimmed(raw, expected):
    assert sanitize({"symbols": raw}).symbols == expected


def test_symbols_are_capped():
    assert len(sanitize({"symbols": [f"{i:06d}" for i in range(200)]}).symbols) == 50


def test_font_family_rejects_style_injection():
    assert sanitize({"font_family": "微软雅黑, PingFang SC"}).font_family == "微软雅黑, PingFang SC"
    assert sanitize({"font_family": "x;} body{display:none}"}).font_family == ""


def test_bounds_require_all_four_numbers():
    assert sanitize({"bounds": {"x": 1, "y": 2, "width": 300, "height": 200}}).bounds is not None
    assert sanitize({"bounds": {"x": 1, "y": 2}}).bounds is None


def test_bounds_preserve_explicit_scale_and_manual_size():
    bounds = sanitize(
        {
            "bounds": {
                "x": 1,
                "y": 2,
                "width": 700,
                "height": 260,
                "scale": 1.25,
                "manual_size": True,
            }
        }
    ).bounds

    assert bounds is not None
    assert bounds.scale == 1.25
    assert bounds.manual_size is True


def test_store_round_trips_json(tmp_path):
    path = tmp_path / "config.json"
    store = Store(path)
    store.update(
        {
            "provider": "tencent",
            "visible_rows": 7,
            "bounds": {
                "x": 321,
                "y": 234,
                "width": 876,
                "height": 198,
                "scale": 1.4,
                "manual_size": True,
            },
        }
    )

    assert json.loads(path.read_text(encoding="utf-8"))["provider"] == "tencent"
    restored = Store(path).get()
    assert restored.visible_rows == 7
    assert restored.bounds is not None
    assert restored.bounds.x == 321
    assert restored.bounds.y == 234
    assert restored.bounds.width == 876
    assert restored.bounds.height == 198
    assert restored.bounds.scale == 1.4
    assert restored.bounds.manual_size is True


def test_store_survives_corrupt_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert Store(path).get().provider == "auto"


def test_background_color_accepts_hex_only():
    assert sanitize({"background_color": "#1A2B3C"}).background_color == "#1a2b3c"
    assert sanitize({"background_color": "#abc"}).background_color == "#abc"
    # 非法值回落到默认，不让脏字符串流到 QColor
    assert sanitize({"background_color": "red; drop"}).background_color == "#11141c"
    assert sanitize({"background_color": "rgb(1,2,3)"}).background_color == "#11141c"


def test_background_alpha_allows_fully_transparent():
    """背景要能调到完全透明，所以下限是 0，整窗透明度则要留一丝可见。"""
    assert sanitize({"background_alpha": 0}).background_alpha == 0.0
    assert sanitize({"background_alpha": -1}).background_alpha == 0.0
    assert sanitize({"background_alpha": 9}).background_alpha == 1.0
    assert sanitize({"opacity": 0}).opacity == MIN_OPACITY  # 整窗透明度不许全隐


def test_opacity_can_go_far_below_20_percent():
    """20% 以下不该被夹回去：淡到几乎看不见也是合法的用法。"""
    assert MIN_OPACITY < 0.2
    for value in (0.15, 0.1, MIN_OPACITY):
        assert sanitize({"opacity": value}).opacity == pytest.approx(value)
    # 只有低于下限的值才被夹住，且夹到的仍是可见的下限而非全隐。
    assert sanitize({"opacity": -3}).opacity == MIN_OPACITY
    assert sanitize({"opacity": MIN_OPACITY / 2}).opacity == MIN_OPACITY
    assert sanitize({"opacity": 0}).opacity > 0


def test_click_through_defaults_off():
    assert sanitize({}).click_through is False
    assert sanitize({"click_through": True}).click_through is True


def test_title_buttons_default_on_and_can_be_switched_off():
    assert sanitize({}).show_title_buttons is True
    assert sanitize({"show_title_buttons": False}).show_title_buttons is False
    # 只认真布尔值，脏数据回落到默认的「显示」，不会把按钮莫名其妙藏起来。
    assert sanitize({"show_title_buttons": "false"}).show_title_buttons is True
    assert sanitize({"show_title_buttons": 0}).show_title_buttons is True


def test_chart_annotation_and_label_switches_are_sanitized():
    keys = (
        "show_sparkline_fill", "show_bs_points", "show_open_line", "show_high_low",
        "show_stock_name", "show_stock_price", "grayscale",
    )
    config = sanitize({key: False for key in keys})
    assert all(getattr(config, key) is False for key in keys)
    # 字符串 "false" 不能冒充布尔值。
    assert sanitize({"show_bs_points": "false"}).show_bs_points is True
    assert sanitize({}).show_sparkline_fill is False
    assert sanitize({"show_sparkline_fill": True}).show_sparkline_fill is True


def test_grayscale_level_is_clamped_and_kept_per_theme(tmp_path):
    """灰度值是一个 0–255 的灰阶，越界夹取、脏数据回落，且两个主题各存一份。"""
    assert sanitize({}).grayscale_level == DEFAULT_GRAYSCALE_LEVEL
    assert sanitize({"grayscale_level": 999}).grayscale_level == GRAYSCALE_LEVEL_MAX
    assert sanitize({"grayscale_level": -40}).grayscale_level == GRAYSCALE_LEVEL_MIN
    assert sanitize({"grayscale_level": 206.4}).grayscale_level == 206
    # 字符串 "150" 不能冒充数字，回落到默认值。
    assert sanitize({"grayscale_level": "150"}).grayscale_level == DEFAULT_GRAYSCALE_LEVEL

    store = Store(tmp_path / "config.json")
    store.update({"grayscale": True, "grayscale_level": 90})
    # 切到另一个主题时用该主题自己的灰阶（不继承当前主题）。
    assert store.update({"display_theme": "theme2"}).grayscale_level == DEFAULT_GRAYSCALE_LEVEL
    restored = store.update({"display_theme": "theme1"})
    assert restored.grayscale is True
    assert restored.grayscale_level == 90


def test_theme_profiles_keep_display_settings_and_bounds_independent(tmp_path):
    path = tmp_path / "config.json"
    store = Store(path)

    theme1 = store.update(
        {
            "font_size": 10,
            "stock_price_font_size": 19,
            "background_alpha": 0.31,
            "row_style": "stacked",
            "bounds": {
                "x": 101,
                "y": 102,
                "width": 701,
                "height": 211,
                "scale": 1.25,
                "manual_size": True,
            },
        }
    )
    assert theme1.display_theme == "theme1"
    assert theme1.font_size == 10

    # 第一次切到主题2时使用主题2自己的默认 profile，不继承主题1的外观/尺寸。
    theme2 = store.update({"display_theme": "theme2"})
    assert theme2.display_theme == "theme2"
    assert theme2.font_size == 13
    assert theme2.stock_price_font_size == 15
    assert theme2.background_alpha == 0.82
    assert theme2.bounds is None

    theme2 = store.update(
        {
            "font_size": 18,
            "stock_price_font_size": 27,
            "background_alpha": 0.66,
            "theme2_side": "left",
            "theme2_depth_width": 188,
            "bounds": {
                "x": 301,
                "y": 302,
                "width": 388,
                "height": 777,
                "scale": 1.0,
                "manual_size": True,
            },
        }
    )
    assert theme2.font_size == 18
    assert theme2.theme2_depth_width == 188

    restored1 = store.update({"display_theme": "theme1"})
    assert restored1.font_size == 10
    assert restored1.stock_price_font_size == 19
    assert restored1.background_alpha == 0.31
    assert restored1.row_style == "stacked"
    assert restored1.bounds is not None
    assert (restored1.bounds.x, restored1.bounds.width, restored1.bounds.scale) == (
        101,
        701,
        1.25,
    )

    restored2 = store.update({"display_theme": "theme2"})
    assert restored2.font_size == 18
    assert restored2.stock_price_font_size == 27
    assert restored2.background_alpha == 0.66
    assert restored2.theme2_side == "left"
    assert restored2.theme2_depth_width == 188
    assert restored2.bounds is not None
    assert (restored2.bounds.x, restored2.bounds.height) == (301, 777)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["theme_profiles"]["theme1"]["font_size"] == 10
    assert persisted["theme_profiles"]["theme2"]["font_size"] == 18
    assert persisted["theme_profiles"]["theme1"]["bounds"]["width"] == 701
    assert persisted["theme_profiles"]["theme2"]["bounds"]["height"] == 777


def test_theme_switch_keeps_global_runtime_settings_shared(tmp_path):
    store = Store(tmp_path / "config.json")
    store.update(
        {
            "provider": "tencent",
            "symbols": ["600000", "000001"],
            "refresh_seconds": 9,
            "debug_mode": True,
            "mcp_notifications_enabled": True,
            "font_size": 9,
        }
    )

    theme2 = store.update({"display_theme": "theme2"})
    assert theme2.provider == "tencent"
    assert theme2.symbols == ["600000", "000001"]
    assert theme2.refresh_seconds == 9
    assert theme2.debug_mode is True
    assert theme2.mcp_notifications_enabled is True
    assert theme2.font_size == 13


def test_update_inactive_theme_profile_does_not_change_current_theme(tmp_path):
    store = Store(tmp_path / "config.json")
    current = store.update({"font_size": 11})
    assert current.display_theme == "theme1"

    still_theme1 = store.update_theme_profile(
        "theme2",
        {
            "font_size": 22,
            "bounds": {
                "x": 12,
                "y": 34,
                "width": 456,
                "height": 678,
                "scale": 1.0,
                "manual_size": True,
            },
        },
    )
    assert still_theme1.display_theme == "theme1"
    assert still_theme1.font_size == 11

    theme2 = store.update({"display_theme": "theme2"})
    assert theme2.font_size == 22
    assert theme2.bounds is not None
    assert (theme2.bounds.x, theme2.bounds.height) == (12, 678)


def test_current_classic_settings_apply_immediately_with_theme_profiles(tmp_path):
    store = Store(tmp_path / "config.json")
    # 先制造新版双 profile 文件，再验证经典主题普通更新不会被 profile 反向覆盖。
    store.update({"display_theme": "theme2", "font_size": 18})
    store.update({"display_theme": "theme1"})

    classic = store.update(
        {
            "row_style": "stacked",
            "show_title_buttons": False,
            "font_size": 11,
        }
    )
    assert classic.display_theme == "theme1"
    assert classic.row_style == "stacked"
    assert classic.show_title_buttons is False
    assert classic.font_size == 11

    reread = Store(store.path).get()
    assert reread.display_theme == "theme1"
    assert reread.row_style == "stacked"
    assert reread.show_title_buttons is False
    assert reread.font_size == 11


def test_sanitize_does_not_let_stale_profile_override_explicit_flat_fields():
    config = sanitize(
        {
            "display_theme": "theme1",
            "row_style": "stacked",
            "show_title_buttons": False,
            "theme_profiles": {
                "theme1": {
                    "row_style": "sides",
                    "show_title_buttons": True,
                },
                "theme2": {},
            },
        }
    )
    assert config.row_style == "stacked"
    assert config.show_title_buttons is False
