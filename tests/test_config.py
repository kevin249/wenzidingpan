"""配置校验：磁盘和 WebUI 送进来的数据都不可信。"""

from __future__ import annotations

import json

import pytest

from stockwidget.config import MIN_OPACITY, Store, sanitize


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
