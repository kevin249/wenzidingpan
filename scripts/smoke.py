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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, help="把截图写到这个目录")
    parser.add_argument("--seconds", type=float, default=4.0, help="每个界面停留多久")
    args = parser.parse_args()

    if os.environ.get("SMOKE_NESTED") != "1" and not ensure_display():
        return 0

    fake_dark_trade()

    from PySide6.QtCore import QTimer
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

    def shot(name: str) -> None:
        if args.shots:
            args.shots.mkdir(parents=True, exist_ok=True)
            app.window.grab().save(str(args.shots / f"{name}.png"))

    def step_multi() -> None:
        check("窗口已显示", app.window.isVisible())
        check("行情已渲染到窗口", len(app.window._rows) == 4, f"行数={len(app.window._rows)}")
        row = next(iter(app.window._rows.values()))
        check("价格已填充", bool(row.price_label.text().strip()), row.price_label.text())
        check("暗盘资金已显示", row.dark_value.isVisible() and "亿" in row.dark_value.text()
              or "万" in row.dark_value.text(), row.dark_value.text())
        check("窗口高度按行数自适应", 120 < app.window.height() < 420, str(app.window.height()))
        shot("shot-multi")

    def step_font() -> None:
        app._apply_config(store.update({"font_size": 17, "visible_rows": 2}))
        QTimer.singleShot(600, lambda: (
            check("改字号后窗口跟着缩放", app.window.height() < 260, str(app.window.height())),
            shot("shot-font-rows"),
        ))

    def step_single() -> None:
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

    steps = [step_multi, step_font, step_single, step_webui, step_webui_applies]
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
