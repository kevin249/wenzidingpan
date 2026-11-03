"""股票搜索：代码、名称、拼音、首字母都能搜。

优先使用支持完整拼音的腾讯 smartbox，东方财富 suggest 作为备用。
记住最近可用的搜索源，避免每次输入都等待同一个故障接口超时。
两个源都搜不动时仍允许直接输入六位代码。
"""

from __future__ import annotations

import json
import logging
import re

import requests

from .providers.base import USER_AGENT, describe_error
from .providers.textquote import decode_gbk
from .symbols import classify

ENDPOINT = "https://searchapi.eastmoney.com/api/suggest/get"
TENCENT_ENDPOINT = "https://smartbox.gtimg.cn/s3/"
# 东财搜索页公开使用的固定 token，不是密钥。
TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"
REQUEST_TIMEOUT = 6
MAX_RESULTS = 12
LOG = logging.getLogger(__name__)
_TENCENT_PAYLOAD = re.compile(r'\bv_hint\s*=\s*("(?:\\.|[^"\\])*")\s*;?\s*$')
# Exclude indices, funds and B shares even when their market is Shanghai/Shenzhen.
_A_SHARE_CODE = re.compile(r"^(?:60|68|00|30|43|83|87|88|92)\d{4}$")

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
        if str(row.get("MktNum", "")) not in A_SHARE_MARKETS:
            continue
        if not _A_SHARE_CODE.fullmatch(code):
            continue
        if row.get("Classify") not in (None, "AStock"):
            continue
        symbol = classify(code)
        if symbol is None or symbol.code in seen:
            continue
        seen.add(symbol.code)
        results.append({"code": symbol.code, "name": name, "market": symbol.market})
        if len(results) >= MAX_RESULTS:
            break
    return results


def parse_tencent(text: str) -> list[dict[str, str]]:
    """Decode the JS string as data; never execute the returned script."""
    match = _TENCENT_PAYLOAD.search(text)
    if match is None:
        raise ValueError("腾讯搜索响应格式异常")
    payload = json.loads(match.group(1))
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in payload.split("^"):
        fields = row.split("~")
        if len(fields) < 5:
            continue
        market, code, name, _, kind = fields[:5]
        if (market not in {"sh", "sz", "bj"}
                or not _A_SHARE_CODE.fullmatch(code)
                or not (kind == "GP-A" or kind.startswith("GP-A-"))
                or code in seen):
            continue
        seen.add(code)
        results.append({"code": code, "name": name, "market": market})
        if len(results) >= MAX_RESULTS:
            break
    return results


class StockSearch:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self._preferred = "tencent"

    def _query_source(self, source: str, text: str) -> list[dict[str, str]]:
        if source == "tencent":
            response = self.session.get(
                TENCENT_ENDPOINT,
                params={"q": text, "t": "all", "v": 2},
                headers={"User-Agent": USER_AGENT, "Referer": "https://gu.qq.com/"},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return parse_tencent(decode_gbk(response.content))

        response = self.session.get(
            ENDPOINT,
            params={"input": text, "type": "14", "token": TOKEN, "count": MAX_RESULTS},
            headers={"User-Agent": USER_AGENT, "Referer": "https://quote.eastmoney.com/"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        table = payload.get("QuotationCodeTable") if isinstance(payload, dict) else None
        if (not isinstance(table, dict)
                or table.get("Status") not in (None, 0, "0")
                or not isinstance(table.get("Data"), (list, type(None)))):
            raise ValueError("东方财富搜索响应格式异常")
        return parse(payload)

    def query(self, text: str) -> dict:
        """返回 ``{"results": [...], "error": str|None}``，任何失败都不抛异常。"""
        text = (text or "").strip()
        if not text:
            return {"results": [], "error": None}

        sources = [self._preferred] + [
            source for source in ("tencent", "eastmoney") if source != self._preferred
        ]
        errors: list[str] = []
        for source in sources:
            try:
                results = self._query_source(source, text)
                if results:
                    self._preferred = source
                    return {"results": results, "error": None}
            except Exception as exc:
                errors.append(describe_error(exc))
                LOG.warning("Stock search %s failed: %s", source, errors[-1])

        # Also try the alternate source after a valid empty response: the two
        # suggest services differ in their support for full pinyin and names.
        symbol = classify(text)
        fallback = [{"code": symbol.code, "name": "", "market": symbol.market}] if symbol else []
        error = errors[0] if len(errors) == len(sources) else None
        return {"results": fallback, "error": error}
