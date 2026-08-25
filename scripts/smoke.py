#!/usr/bin/env python
"""冒烟测试：真正把组件跑起来一次。

单元测试覆盖不到「窗口能不能画出来、WebUI 通不通、两者是不是接上了」，
这个脚本把整套东西启动一遍，必要时还能截图。

    python scripts/smoke.py                    # 跑一遍并检查
    python scripts/smoke.py --shots docs/      # 顺便出截图

没有图形环境时会自动套 xvfb-run；两者都没有则跳过并说明原因。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ✓ {name}")
    else:
        FAILURES.append(name)
        print(f"  ✗ {name}\n    {detail}")


def ensure_display() -> bool:
    """没有 DISPLAY 时用 xvfb-run 重新跑一遍自己。"""
    if os.environ.get("DISPLAY"):
        return True
    if not shutil.which("xvfb-run"):
        print("跳过启动检查：没有图形环境，也没有 xvfb-run")
        return False
    print("没有 DISPLAY，改用 xvfb-run 重新启动…")
    result = subprocess.run(
        ["xvfb-run", "-a", sys.executable, __file__, *sys.argv[1:]],
        env={**os.environ, "SMOKE_NESTED": "1"},
    )
    sys.exit(result.returncode)


def fake_dark_trade() -> None:
    """沙箱里连不上东财，注入假的暗盘资金，专门验证这部分界面。"""
    from stockwidget.darktrade import DarkResult, DarkRow, DarkTradeClient

    rows = {
        "600519": 238_500_000.0,
        "000001": -43_800_000.0,
        "300750": 9_650_000.0,
    }

    def fetch(self, codes, now=None):  # noqa: ANN001
        return DarkResult(
            trade_date="2026-08-17",
            by_code={
                code: DarkRow(code, code, "sh", fund, 0, fund / 4, 1.0, None, None, None)
                for code, fund in rows.items()
            },
        )

    DarkTradeClient.fetch = fetch


def fake_intraday() -> None:
    """同样连不上东财，用一条 240 分钟的随机游走冒充当日分时，验证图形渲染。"""
    import random

    from stockwidget.intraday import IntradayClient, Trend

    def fetch(self, raw_symbol, now=None):  # noqa: ANN001
        seed = sum(ord(ch) for ch in raw_symbol)
        rng = random.Random(seed)
        prev_close = 10 + seed % 300
        price = prev_close * (1 + rng.uniform(-0.01, 0.01))
        prices = []
        for _ in range(240):  # A 股一天 4 小时 = 240 分钟
            price *= 1 + rng.gauss(0, 0.0015)
            prices.append(round(price, 2))
        return Trend(prices=prices, prev_close=round(prev_close, 2))

    IntradayClient.fetch = fetch


def fake_names() -> None:
    """mock 数据源拿代码当名称，看不出「名称压代码」的两行版式；
    截图时给默认自选补上真实名称，让文档里的样子和联网时一致。"""
    from stockwidget.providers.mock import MockProvider

    names = {"600519": "贵州茅台", "000001": "平安银行", "300750": "宁德时代", "601318": "中国平安"}
    original = MockProvider.fetch

    def fetch(self, symbols):  # noqa: ANN001
        quotes = original(self, symbols)
        for quote in quotes:
            quote.name = names.get(quote.symbol, quote.name)
        return quotes

    MockProvider.fetch = fetch


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, help="把截图写到这个目录")
    parser.add_argument("--seconds", type=float, default=4.0, help="每个界面停留多久")
    args = parser.parse_args()

    if os.environ.get("SMOKE_NESTED") != "1" and not ensure_display():
        return 0

    fake_dark_trade()
    fake_intraday()
    fake_names()

    from PySide6.QtCore import QSize, Qt, QTimer
    from stockwidget.app import WidgetApp
    from stockwidget.config import Store

    config_path = Path(tempfile.mkdtemp()) / "config.json"
    store = Store(config_path)
    store.update({"provider": "mock", "refresh_seconds": 1, "visible_rows": 4})

    app = WidgetApp(argv=[], store=store)
    url = app.server.start()
    app.window.show()
    app.poller.start()

    steps: list = []

    def _grid_rows(app) -> set:  # noqa: ANN001
        """当前网格里所有格子占用的行号。"""
        layout = app.window.rows_layout
        return {
            layout.getItemPosition(i)[0]
            for i in range(layout.count())
            if layout.itemAt(i).widget() is not None
        }

    def shot(name: str) -> None:
        if args.shots:
            args.shots.mkdir(parents=True, exist_ok=True)
            app.window.grab().save(str(args.shots / f"{name}.png"))

    def drag_to(size: QSize) -> None:
        """按真实把手手势的开始/移动/结束顺序模拟缩放。"""
        app.window._on_grip_drag_started(app.window.size())
        app.window._on_grip_dragged(size)
        app.window._on_grip_drag_finished()

    def step_multi() -> None:
        check("窗口已显示", app.window.isVisible())
        check("行情已渲染到窗口", len(app.window._rows) == 4, f"行数={len(app.window._rows)}")
        row = next(iter(app.window._rows.values()))
        check("价格已填充", bool(row.price_label.text().strip()), row.price_label.text())
        check("暗盘资金已显示", row.dark_value.isVisible() and "亿" in row.dark_value.text()
              or "万" in row.dark_value.text(), row.dark_value.text())
        check("窗口高度按行数自适应", 120 < app.window.height() < 460, str(app.window.height()))
        check("分时曲线已装入走势图", len(row.sparkline.points) > 100, str(len(row.sparkline.points)))
        check("昨收基准线有值", row.sparkline._prev_close is not None)
        shot("shot-multi")

    def step_font() -> None:
        app._apply_config(store.update({"font_size": 17, "visible_rows": 2}))
        QTimer.singleShot(600, lambda: (
            check("改字号后窗口跟着缩放", app.window.height() < 300, str(app.window.height())),
            shot("shot-font-rows"),
        ))

    def step_tile_one_row() -> None:
        """1 行时四只股票应该全部横向铺开，窗口变宽变矮。"""
        app._apply_config(store.update({"font_size": 13, "visible_rows": 1}))
        QTimer.singleShot(700, lambda: (
            check("1 行时铺成 1×4", app.window._grid_shape == (1, 4), str(app.window._grid_shape)),
            check("1 行时窗口宽大于高",
                  app.window.width() > app.window.height(),
                  f"{app.window.width()}x{app.window.height()}"),
            check("所有股票都在第 0 行", _grid_rows(app) == {0}, str(_grid_rows(app))),
            shot("shot-tile-1"),
        ))

    def step_tile_two_rows() -> None:
        app._apply_config(store.update({"visible_rows": 2}))
        QTimer.singleShot(700, lambda: (
            check("2 行时铺成 2×2", app.window._grid_shape == (2, 2), str(app.window._grid_shape)),
            check("2 行时占用两行", _grid_rows(app) == {0, 1}, str(_grid_rows(app))),
            shot("shot-tile-2"),
        ))

    def step_no_code() -> None:
        row = next(iter(app.window._rows.values()))
        check("左侧不再显示股票代码", not hasattr(row, "code_label"))
        check("左侧仍显示名称", bool(row.name_label.text().strip()), row.name_label.text())

    def cell(row, widget) -> tuple:  # noqa: ANN001
        """某个控件在格子内网格里的 (行, 列)。"""
        layout = row.layout()
        index = layout.indexOf(widget)
        return layout.getItemPosition(index)[:2] if index >= 0 else (-1, -1)

    def check_stacked() -> None:
        row = next(iter(app.window._rows.values()))
        name, price = cell(row, row.name_label), cell(row, row.price_label)
        dark, percent = cell(row, row.dark_box), cell(row, row.percent_label)
        chart = cell(row, row.sparkline)
        check("上中下时名称与现价同一行", name[0] == price[0] and name[1] != price[1],
              f"{name} {price}")
        check("上中下时暗盘与涨跌幅同一行", dark[0] == percent[0] and dark[1] != percent[1],
              f"{dark} {percent}")
        check("上中下时 K 线夹在上下两行中间", name[0] < chart[0] < dark[0],
              f"{name} {chart} {dark}")
        check("K 线高度按参数固定", row.sparkline.height() == 60, str(row.sparkline.height()))
        shot("shot-stacked")

    def check_sides() -> None:
        row = next(iter(app.window._rows.values()))
        name, dark = cell(row, row.name_label), cell(row, row.dark_box)
        price, percent = cell(row, row.price_label), cell(row, row.percent_label)
        check("左中右时左侧名称压暗盘两行", name[1] == dark[1] and name[0] < dark[0],
              f"{name} {dark}")
        check("左中右时右侧现价压涨跌幅两行", price[1] == percent[1] and price[0] < percent[0],
              f"{price} {percent}")
        check("左中右时 K 线在中间列", name[1] < cell(row, row.sparkline)[1] < price[1],
              f"{name} {cell(row, row.sparkline)} {price}")
        check("K 线高度恢复自动后不再固定", row.sparkline.maximumHeight() > 400,
              str(row.sparkline.maximumHeight()))

    def step_row_style() -> None:
        """两种行内样式与 K 线高度参数：上中下上下各一行，左中右左右各两行。"""
        app._apply_config(store.update({"row_style": "stacked", "chart_height": 60}))
        QTimer.singleShot(700, check_stacked)
        QTimer.singleShot(900, lambda: app._apply_config(
            store.update({"row_style": "sides", "chart_height": 0})
        ))
        QTimer.singleShot(1200, check_sides)

    def step_compact() -> None:
        """紧凑模式以前会把走势图一起藏掉，这是回归点。"""
        app._apply_config(store.update({"compact": True, "font_size": 13, "visible_rows": 4}))
        QTimer.singleShot(700, lambda: (
            check("紧凑模式仍显示走势图",
                  next(iter(app.window._rows.values())).sparkline.isVisible()),
            check("紧凑模式确实更紧凑", app.window.height() < 260, str(app.window.height())),
            shot("shot-compact"),
        ))

    def step_scale() -> None:
        """只拖宽外框时，高度仍是限制轴；内容应留在框内且不出现滚动条。"""
        app._apply_config(store.update({"compact": False}))
        before = app.window.size()
        drag_to(QSize(560, app.window.height()))  # 模拟拖动右下角把手
        QTimer.singleShot(700, lambda: (
            check("拉宽后外框尺寸已改变",
                  app.window.width() > before.width(),
                  f"{before.width()} -> {app.window.width()}"),
            check("拉宽后内部仍无滚动条",
                  not app.window.scroll.horizontalScrollBar().isVisible()
                  and not app.window.scroll.verticalScrollBar().isVisible()),
            shot("shot-scaled"),
        ))

    def step_background() -> None:
        app._apply_config(store.update({"background_color": "#1d3f2b", "background_alpha": 0.0}))
        QTimer.singleShot(700, lambda: (
            check("背景可调到完全透明", app.window._config.background_alpha == 0.0),
            shot("shot-transparent"),
        ))
        QTimer.singleShot(900, lambda: app._apply_config(
            store.update({"background_color": "#11141c", "background_alpha": 0.82})
        ))

    def step_click_through() -> None:
        app._apply_config(store.update({"click_through": True}))
        QTimer.singleShot(700, lambda: (
            check("穿透时窗口不吃鼠标事件",
                  bool(app.window.windowFlags() & Qt.WindowTransparentForInput)),
            check("穿透时左上角把手可见", app.window.handle.isVisible()),
            shot("shot-clickthrough"),
        ))
        QTimer.singleShot(900, lambda: app._apply_config(store.update({"click_through": False})))
        QTimer.singleShot(1100, lambda: check(
            "关掉穿透后把手收起", not app.window.handle.isVisible()
        ))

    def step_single() -> None:
        drag_to(QSize(300, app.window.height()))  # 先把缩放拖回基准
        app._apply_config(store.update({"layout": "single", "font_size": 13}))
        QTimer.singleShot(900, lambda: (
            check("单行模式只占一行高", app.window.height() < 90, str(app.window.height())),
            check("跑马灯已装载内容", app.window.marquee._content_width > 0),
            shot("shot-single"),
        ))

    def step_webui() -> None:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                body = response.read().decode("utf-8")
            check("WebUI 设置页可访问", response.status == 200 and "行情组件设置" in body)
        except Exception as exc:
            check("WebUI 设置页可访问", False, str(exc))
        try:
            urllib.request.urlopen(url.split("?")[0], timeout=5)
            check("WebUI 拒绝无 token 访问", False, "居然放行了")
        except urllib.error.HTTPError as exc:
            check("WebUI 拒绝无 token 访问", exc.code == 403, f"HTTP {exc.code}")

    def step_webui_applies() -> None:
        """从 WebUI 改配置，桌面窗口必须跟着变——这是两边接通的关键路径。"""
        import json

        request = urllib.request.Request(
            f"{url.split('?')[0]}api/config?token={app.server.token}",
            data=json.dumps({"layout": "multi", "color_scheme": "us"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=5)
        QTimer.singleShot(800, lambda: (
            check("WebUI 改动已下发到窗口", app.window._config.color_scheme == "us"
                  and app.window._config.layout == "multi", str(app.window._config.layout)),
            check("配置已写入 JSON", json.loads(config_path.read_text())["color_scheme"] == "us"),
        ))

    steps = [
        step_multi,
        step_no_code,
        step_font,
        step_tile_one_row,
        step_tile_two_rows,
        step_row_style,
        step_compact,
        step_scale,
        step_background,
        step_click_through,
        step_single,
        step_webui,
        step_webui_applies,
    ]
    delay = 0.0
    for step in steps:
        delay += args.seconds
        QTimer.singleShot(int(delay * 1000), step)
    QTimer.singleShot(int((delay + args.seconds) * 1000), app.quit)

    print("启动检查")
    app.qt.exec()
    app.server.stop()

    if FAILURES:
        print(f"\n{len(FAILURES)} 项检查失败：{'、'.join(FAILURES)}")
        return 1
    print("\n全部检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
