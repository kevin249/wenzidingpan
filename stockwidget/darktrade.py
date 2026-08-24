"""东方财富暗盘资金。

口径与字段映射对齐 ``gupiao_ztfx`` 的 ``src/dark_trade/eastmoney.py``：
接口返回的是「某个交易日的暗盘排行榜」，按暗盘资金降序分页，
所以这里翻页收集，直到自选股都命中或翻到页数上限为止。

返回体形如::

    {"errid": 0, "1": "20260817", "2": 总条数, "data": [{"4": 代码, "6": 暗盘资金, ...}]}
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

import requests

from .providers.base import USER_AGENT, describe_error

ENDPOINT = "https://quotederivates.eastmoney.com/datacenter/darktrade"
PAGE_SIZE = 100
# 东方财富的榜单已经可能超过 5000 条，50 页会漏掉尾部个股。
# 仍保留一个宽松的安全上限，避免接口总数异常时无限翻页。
MAX_PAGES = 100
PRICE_DIVISOR = 1000  # 接口里的价格放大了 1000 倍
CACHE_TTL_SECONDS = 10 * 60  # 日频数据，不必跟行情同频刷新
REQUEST_TIMEOUT = 10

# 接口字段是数字键，含义按 gupiao_ztfx 的映射表。
F_MARKET = "3"
F_CODE = "4"
F_DARK_FUND = "6"
F_OPEN_FUND = "7"
F_MAIN_NET_INFLOW = "8"
F_ACTIVITY = "11"
F_PRICE = "13"
F_CHANGE_PERCENT = "14"
F_NAME = "16"
F_RANK = "21"


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


@dataclass
class DarkRow:
    code: str
    name: str
    market: str
    dark_fund: float  # 单位：元
    open_fund: float
    main_net_inflow: float
    activity: float
    price: float | None
    change_percent: float | None
    rank: int | None


@dataclass
class DarkResult:
    trade_date: str = ""
    by_code: dict[str, DarkRow] = None  # type: ignore[assignment]
    error: str | None = None

    def __post_init__(self) -> None:
        if self.by_code is None:
            self.by_code = {}


def normalize_row(row: Any) -> DarkRow | None:
    if not isinstance(row, dict):
        return None
    digits = "".join(ch for ch in str(row.get(F_CODE) or "") if ch.isdigit())
    if not digits:
        return None
    price = _number(row.get(F_PRICE))
    return DarkRow(
        code=digits.zfill(6),
        name=str(row.get(F_NAME) or "").strip(),
        market="sh" if _number(row.get(F_MARKET)) == 1 else "sz",
        dark_fund=_number(row.get(F_DARK_FUND), 0.0) or 0.0,
        open_fund=_number(row.get(F_OPEN_FUND), 0.0) or 0.0,
        main_net_inflow=_number(row.get(F_MAIN_NET_INFLOW), 0.0) or 0.0,
        activity=_number(row.get(F_ACTIVITY), 0.0) or 0.0,
        price=None if price is None else price / PRICE_DIVISOR,
        change_percent=_number(row.get(F_CHANGE_PERCENT)),
        rank=int(_number(row.get(F_RANK), 0) or 0) or None,
    )


def format_date(value: Any) -> str:
    """``20260817`` -> ``2026-08-17``，拿不到就返回空串。"""
    text = str(value or "").replace("-", "")
    return f"{text[:4]}-{text[4:6]}-{text[6:]}" if len(text) == 8 and text.isdigit() else ""


class DarkTradeClient:
    """带缓存的暗盘资金查询。"""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self._cache: DarkResult | None = None
        self._cached_at = 0.0
        self._cached_codes: set[str] = set()

    def _request_page(self, date_stamp: str, page: int) -> dict:
        response = self.session.get(
            ENDPOINT,
            params={
                "version": 101,
                "cver": 100,
                "date": date_stamp,
                "StartPage": page,
                "NumPerPage": PAGE_SIZE,
                "sortflag": 6,
                "desc": 1,
                "market": "",
                "datetype": "",
            },
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://emrnweb.eastmoney.com/graymarket/rankList",
                "rnProjectId": "emrn.GrayMarketRank",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("返回格式异常")
        # 注意不能写成 `_number(...) or -1`：errid 正常就是 0，会被 or 吃掉。
        errid = _number(payload.get("errid"))
        if errid is None or int(errid) != 0:
            raise ValueError(str(payload.get("errmsg") or "接口返回错误")[:60])
        return payload

    def fetch(self, codes: Iterable[str], now: float | None = None) -> DarkResult:
        """取指定 6 位代码的暗盘资金；失败时返回带 error 的空结果。"""
        wanted = set(codes)
        now = time.time() if now is None else now
        if (
            self._cache is not None
            and now - self._cached_at < CACHE_TTL_SECONDS
            # 自选变了且新代码上次没覆盖到时，重新拉一次
            and wanted <= self._cached_codes
        ):
            return self._cache

        result = DarkResult()
        try:
            date_stamp = date.today().strftime("%Y%m%d")
            first = self._request_page(date_stamp, 1)
            result.trade_date = format_date(first.get("1"))
            total = int(_number(first.get("2"), 0) or 0)
            pages = min(MAX_PAGES, max(1, math.ceil(total / PAGE_SIZE)))

            def absorb(payload: dict) -> None:
                for raw in payload.get("data") or []:
                    if row := normalize_row(raw):
                        result.by_code[row.code] = row

            absorb(first)
            for page in range(2, pages + 1):
                if wanted <= result.by_code.keys():  # 自选全部命中就不必继续翻页
                    break
                absorb(self._request_page(date_stamp, page))
        except Exception as exc:
            result.error = describe_error(exc)

        self._cache, self._cached_at, self._cached_codes = result, now, wanted
        return result

    def reset_cache(self) -> None:
        """仅供测试。"""
        self._cache = None
        self._cached_codes = set()
