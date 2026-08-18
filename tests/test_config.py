"""配置校验：磁盘和 WebUI 送进来的数据都不可信。"""

from __future__ import annotations

import json

import pytest

from stockwidget.config import Store, sanitize


def test_empty_input_falls_back_to_defaults():
    for value in (None, {}, [], "nope", 42):
        config = sanitize(value)
        assert config.provider == "eastmoney"
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
    assert config.font_size == 9
    assert config.layout == "multi"
    assert config.color_scheme == "cn"


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


def test_store_round_trips_json(tmp_path):
    path = tmp_path / "config.json"
    store = Store(path)
    store.update({"provider": "tencent", "visible_rows": 7})

    assert json.loads(path.read_text(encoding="utf-8"))["provider"] == "tencent"
    assert Store(path).get().visible_rows == 7


def test_store_survives_corrupt_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert Store(path).get().provider == "eastmoney"
