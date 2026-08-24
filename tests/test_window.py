"""桌面网格布局回归测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

try:
    from PySide6.QtWidgets import QApplication
except (ImportError, OSError) as error:
    pytest.skip(f"Qt 运行库不可用：{error}", allow_module_level=True)

from stockwidget.config import Config
from stockwidget.ui.quote_row import QuoteRow
from stockwidget.ui.window import TickerWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_one_row_keeps_each_mini_chart_at_its_minimum_width(app):
    """一行放不下时应扩展内容区并横向滚动，不能挤扁 mini K 线。"""
    config = Config(visible_rows=1)
    window = TickerWindow(config)
    symbols = ["600519", "000001", "300750"]
    for symbol in symbols:
        row = QuoteRow(symbol)
        row.apply_config(config)
        row.name_label.setText("一只名字很长的股票")
        row.price_label.setText("1234.56")
        window._rows[symbol] = row

    window._lay_out_grid(symbols)

    cell_width = max(row.minimumSizeHint().width() for row in window._rows.values())
    assert window.rows_host.minimumWidth() >= cell_width * len(symbols)
    assert all(row.sparkline.minimumWidth() > 0 for row in window._rows.values())

    window.close()
