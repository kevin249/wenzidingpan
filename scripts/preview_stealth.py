"""用离线行情渲染真实 Qt 主题预览，不启动后台行情或改动用户配置。"""

from __future__ import annotations

import math
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from stockwidget.config import sanitize
from stockwidget.intraday import Trend
from stockwidget.providers.base import Quote
from stockwidget.ui.window import TickerWindow


def main() -> None:
    app = QApplication.instance() or QApplication([])
    if sys.platform == "win32":
        QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"))
        app.setFont(QFont("Microsoft YaHei"))
    root = Path(__file__).resolve().parents[1]
    quotes = [Quote.from_prices("603986", "兆易创新", 243.18, 241.15),
              Quote.from_prices("300364", "中文在线", 32.16, 31.49),
              Quote.from_prices("688561", "奇安信", 48.72, 48.90)]
    trends = {}
    for q in quotes:
        prices = [q.prev_close + q.change * i / 50 + math.sin(i * .5) * q.price * .0006 for i in range(51)]
        trends[q.symbol] = Trend(prices=prices, minutes=[f"10:{i:02}" for i in range(51)])
        q.dark_fund = 32000000 if q.change > 0 else -3000000
    sheet = QPixmap(760, 450)
    sheet.fill(QColor("#24272c"))
    for index, (theme, label) in enumerate((("depth", "主题 1 · 一行文字"), ("radar", "主题 2 · 贴边小列"))):
        window = TickerWindow(sanitize({"display_theme": theme, "font_family": "Microsoft YaHei"}))
        window.show()
        window._sync_rows(quotes, trends)
        app.processEvents()
        preview = QPixmap(640, 180)
        preview.fill(QColor("#24272c"))
        painter = QPainter(preview)
        painter.setPen(QColor("#a3abb5"))
        painter.setFont(QFont("Microsoft YaHei", 10))
        painter.drawText(22, 27, label)
        painter.drawPixmap(QPoint(24, 62), window.grab())
        painter.end()
        preview.save(str(root / "stockwidget" / "webui" / "static" / f"{theme}-preview.png"))
        painter = QPainter(sheet)
        painter.drawPixmap(20, index * 210 + 10, preview)
        painter.end()
        window.close()
    sheet.save(str(root / "docs" / "stealth-themes.png"))
    print("Rendered compact Qt themes to docs/stealth-themes.png")


if __name__ == "__main__":
    main()
