"""股票搜索：代码、名称、拼音、首字母都能搜。

拼音匹配交给东方财富的 suggest 接口——本地做拼音索引要背一份全市场
名称表还得维护，而这个接口本来就支持 ``gzmt`` → 贵州茅台 这种输入。
搜不动（限流、断网）时退回「输入的是不是六位代码」，至少不挡着人加自选。
"""

from __future__ import annotations

import requests

from .providers.base import USER_AGENT, describe_error
from .symbols import classify

ENDPOINT = "https://searchapi.eastmoney.com/api/suggest/get"
# 东财搜索页公开使用的固定 token，不是密钥。
TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"
REQUEST_TIMEOUT = 6
MAX_RESULTS = 12

# 只要 A 股，过滤掉指数、基金、港美股等
A_SHARE_MARKETS = {"0", "1"}


def _rows(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    table = payload.get("QuotationCodeTable")
    data = table.get("Data") if isinstance(table, dict) else None
    return [row for row in data or [] if isinstance(row, dict)]


def parse(payload: object) -> list[dict[str, str]]:
    """抽出 ``{code, name, market}``，只留 A 股且代码合法的。"""
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in _rows(payload):
        code = str(row.get("Code") or "").strip()
        name = str(row.get("Name") or "").strip()
        if str(row.get("MktNum") or "") not in A_SHARE_MARKETS:
            continue
        symbol = classify(code)
        if symbol is None or symbol.code in seen:
            continue
        seen.add(symbol.code)
        results.append({"code": symbol.code, "name": name, "market": symbol.market})
        if len(results) >= MAX_RESULTS:
            break
    return results


class StockSearch:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    def query(self, text: str) -> dict:
        """返回 ``{"results": [...], "error": str|None}``，任何失败都不抛异常。"""
        text = (text or "").strip()
        if not text:
            return {"results": [], "error": None}

        try:
            response = self.session.get(
                ENDPOINT,
                params={
                    "input": text,
                    "type": "14",  # 证券代码表
                    "token": TOKEN,
                    "count": MAX_RESULTS,
                },
                headers={"User-Agent": USER_AGENT, "Referer": "https://quote.eastmoney.com/"},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return {"results": parse(response.json()), "error": None}
        except Exception as exc:
            # 搜不了也得让人能手输代码，所以这里把「看起来就是个代码」的情况兜住。
            symbol = classify(text)
            fallback = [{"code": symbol.code, "name": "", "market": symbol.market}] if symbol else []
            return {"results": fallback, "error": describe_error(exc)}
