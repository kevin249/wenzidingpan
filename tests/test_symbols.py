"""A 股代码归一化。"""

import pytest

from stockwidget.symbols import classify


@pytest.mark.parametrize(
    "raw,code,market",
    [
        ("600519", "600519", "sh"),
        ("688981", "688981", "sh"),
        ("000001", "000001", "sz"),
        ("300750", "300750", "sz"),
        ("830799", "830799", "bj"),
        ("sh600000", "600000", "sh"),
        ("SZ000001", "000001", "sz"),
        ("600519.SH", "600519", "sh"),
        (" 600519 ", "600519", "sh"),
    ],
)
def test_classify(raw, code, market):
    symbol = classify(raw)
    assert symbol is not None
    assert (symbol.code, symbol.market) == (code, market)
    assert symbol.key == f"{market}{code}"


@pytest.mark.parametrize("raw", ["", None, "不是代码", "12345", "1234567", "AAPL"])
def test_invalid_symbols(raw):
    assert classify(raw) is None
