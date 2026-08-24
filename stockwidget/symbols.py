"""A 股代码归一化。

用户可以写 ``600519``、``sh600519``、``SH600519`` 或 ``600519.SH``，
统一解析成 :class:`Symbol`，各数据源再拼成自己需要的格式。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PREFIXED = re.compile(r"^(sh|sz|bj)(\d{6})$")
_SUFFIXED = re.compile(r"^(\d{6})\.(sh|sz|bj)$")
_CODE = re.compile(r"^\d{6}$")


@dataclass(frozen=True)
class Symbol:
    raw: str
    code: str
    market: str  # sh / sz / bj

    @property
    def key(self) -> str:
        """新浪、腾讯都用 ``sh600000`` 这种拼法。"""
        return f"{self.market}{self.code}"


def market_of(code: str) -> str:
    """只按代码号段判断交易所，规则来自沪深北三所的号段划分。"""
    if re.match(r"^(60|68|90|50|51|52|56|58)", code):
        return "sh"  # 主板 / 科创板 / B股 / 沪市 ETF
    if re.match(r"^(00|30|20|15|16|18|39)", code):
        return "sz"  # 主板 / 创业板 / B股 / 深市 ETF
    if re.match(r"^(43|83|87|88|92)", code):
        return "bj"  # 北交所
    return "sh"


def classify(value: str | None) -> Symbol | None:
    """解析单个代码，无法识别时返回 ``None``。"""
    raw = str(value or "").strip()
    if not raw:
        return None

    text = raw.lower().replace(" ", "")
    market: str | None = None
    code = text

    if match := _PREFIXED.match(text):
        market, code = match.group(1), match.group(2)
    elif match := _SUFFIXED.match(text):
        code, market = match.group(1), match.group(2)

    if not _CODE.match(code):
        return None
    return Symbol(raw=raw, code=code, market=market or market_of(code))
