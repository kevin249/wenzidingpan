"""东方财富行情（push2 公开接口，免密钥，默认数据源）。

一次请求批量拉取，返回 JSON::

    {"rc": 0, "data": {"total": n, "diff": [{"f2": ..., "f12": ...}, ...]}}
"""

from __future__ import annotations

import time
from typing import Any

import requests

from ..symbols import Symbol, classify
from .base import REQUEST_TIMEOUT, USER_AGENT, Quote, describe_error

ENDPOINT = "https://push2.eastmoney.com/api/qt/ulist.np/get"
# 东财网页端公开使用的固定 ut 值，不是密钥，缺省时接口会拒绝请求。
UT = "fa5fd1943c7b386f172d6893dbfba10b"
FIELDS = "f1,f2,f3,f4,f12,f13,f14,f18"


def _secid(symbol: Symbol) -> str:
    """东财用 1 表示沪市，0 表示深市与北交所。"""
    return f"{1 if symbol.market == 'sh' else 0}.{symbol.code}"


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def detect_scale(rows: list[dict]) -> int:
    """判断这批数据的价格是不是被放大了 100 倍。

    push2 在不同参数组合下会把价格按 100 倍返回。涨跌幅是比值、与缩放无关，
    拿它和「按价格算出来的涨跌幅」对一次就能判定，不必写死。
    """
    scaled = plain = 0
    for row in rows:
        price, prev_close, pct = _num(row.get("f2")), _num(row.get("f18")), _num(row.get("f3"))
        if not price or not prev_close or pct is None:
            continue
        computed = (price - prev_close) / prev_close * 100
        if abs(computed - pct) <= abs(computed - pct / 100):
            plain += 1
        else:
            scaled += 1
    return 100 if scaled > plain else 1


class EastmoneyProvider:
    id = "eastmoney"
    label = "A 股 · 东方财富（默认，免密钥）"
    placeholder = "600519, 000001, 300750, sh601318"

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    def fetch(self, symbols: list[str]) -> list[Quote]:
        parsed = [classify(s) for s in symbols]
        valid = [s for s in parsed if s]
        by_secid: dict[str, dict] = {}
        scale = 1
        failure: str | None = None if valid else "代码格式不正确"

        if valid:
            try:
                response = self.session.get(
                    ENDPOINT,
                    params={
                        "ut": UT,
                        "fltt": 2,
                        "invt": 2,
                        "fields": FIELDS,
                        "secids": ",".join(_secid(s) for s in valid),
                        "_": int(time.time() * 1000),
                    },
                    headers={"User-Agent": USER_AGENT, "Referer": "https://quote.eastmoney.com/"},
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                rows = (response.json() or {}).get("data", {}).get("diff")
                if not isinstance(rows, list):
                    raise ValueError("返回格式异常")
                scale = detect_scale(rows)
                by_secid = {f"{row.get('f13')}.{row.get('f12')}": row for row in rows}
            except Exception as exc:  # 网络与解析问题都只降级，不往上抛
                failure = describe_error(exc)

        quotes = []
        for symbol, parsed_symbol in zip(symbols, parsed):
            row = by_secid.get(_secid(parsed_symbol)) if parsed_symbol else None
            if row is None:
                quotes.append(
                    Quote.failed(symbol, failure or ("无数据" if parsed_symbol else "代码格式不正确"))
                )
                continue
            price, prev_close = _num(row.get("f2")), _num(row.get("f18"))
            if price is None or prev_close is None:
                quotes.append(Quote.failed(symbol, "无数据"))
                continue
            name = row.get("f14")
            quotes.append(
                Quote.from_prices(
                    symbol,
                    name if isinstance(name, str) and name != "-" else symbol,
                    price / scale,
                    prev_close / scale,
                )
            )
        return quotes
