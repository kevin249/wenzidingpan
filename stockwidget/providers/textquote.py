"""新浪与腾讯的行情接口共用的实现。

两家都是「一次请求批量返回一段 GBK 编码的 JS 赋值语句」，
差别只在 URL、变量名前缀、分隔符和字段下标，抽出来避免写两遍。
"""

from __future__ import annotations

import re

import requests

from ..symbols import classify
from .base import REQUEST_TIMEOUT, USER_AGENT, Quote, describe_error


def decode_gbk(payload: bytes) -> str:
    """行情文本是 GBK；解码失败时至少保住 ASCII 的价格字段。"""
    for encoding in ("gbk", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("latin1", errors="replace")


class TextQuoteProvider:
    """按位置取字段的文本行情源。"""

    pattern: re.Pattern[str]
    separator: str
    name_index: int
    price_index: int
    prev_close_index: int
    endpoint: str
    referer: str

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    def parse(self, text: str) -> dict[str, tuple[str, float, float] | None]:
        """解析成 ``{代码: (名称, 现价, 昨收)}``，字段缺失的置 ``None``。"""
        parsed: dict[str, tuple[str, float, float] | None] = {}
        for match in self.pattern.finditer(text):
            key, payload = match.group(1), match.group(2)
            fields = payload.split(self.separator)
            needed = max(self.name_index, self.price_index, self.prev_close_index)
            if len(fields) <= needed:
                parsed[key] = None
                continue
            try:
                price = float(fields[self.price_index])
                prev_close = float(fields[self.prev_close_index])
            except ValueError:
                parsed[key] = None
                continue
            parsed[key] = (fields[self.name_index], price, prev_close)
        return parsed

    def fetch(self, symbols: list[str]) -> list[Quote]:
        parsed_symbols = [classify(s) for s in symbols]
        valid = [s for s in parsed_symbols if s]
        rows: dict[str, tuple[str, float, float] | None] = {}
        failure: str | None = None if valid else "代码格式不正确"

        if valid:
            try:
                response = self.session.get(
                    self.endpoint + ",".join(s.key for s in valid),
                    headers={"User-Agent": USER_AGENT, "Referer": self.referer},
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                rows = self.parse(decode_gbk(response.content))
            except Exception as exc:
                failure = describe_error(exc)

        quotes = []
        for symbol, parsed_symbol in zip(symbols, parsed_symbols):
            row = rows.get(parsed_symbol.key) if parsed_symbol else None
            if not row:
                quotes.append(
                    Quote.failed(symbol, failure or ("无数据" if parsed_symbol else "代码格式不正确"))
                )
                continue
            name, price, prev_close = row
            quotes.append(Quote.from_prices(symbol, name, price, prev_close))
        return quotes


class SinaProvider(TextQuoteProvider):
    """``var hq_str_sh600000="浦发银行,10.00,10.01,10.20,...";``"""

    id = "sina"
    label = "A 股 · 新浪财经（免密钥）"
    placeholder = "600519, 000001, 300750, sh600000"
    endpoint = "https://hq.sinajs.cn/list="
    # 新浪要求带 Referer，否则返回 403。
    referer = "https://finance.sina.com.cn/"
    pattern = re.compile(r'var hq_str_(\w+)="([^"]*)"')
    separator = ","
    name_index = 0
    prev_close_index = 2
    price_index = 3


class TencentProvider(TextQuoteProvider):
    """``v_sh600000="1~浦发银行~600000~10.20~10.00~...";``"""

    id = "tencent"
    label = "A 股 · 腾讯行情（免密钥）"
    placeholder = "600519, 000001, 300750, sz002594"
    endpoint = "https://qt.gtimg.cn/q="
    referer = "https://gu.qq.com/"
    pattern = re.compile(r'v_(\w+)="([^"]*)"')
    separator = "~"
    name_index = 1
    price_index = 3
    prev_close_index = 4
