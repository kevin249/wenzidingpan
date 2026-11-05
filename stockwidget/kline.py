"""K 线数据：日 K / 周 K / 月 K / 1·5·15·30·60 分钟 K。

**取数一律走东方财富 → 腾讯 → 新浪三个公开 HTTP 接口，不经过 MCP。**
MCP 通道（``gupiao_ztfx`` gateway）只承担实时提醒、千档深度和当日 B/S markers，
行情图表不该被网关进程的可用性拖累；这条约束也写在项目 README 与协作约定里。

对外接口（其他本机脚本可直接 import）::

    from stockwidget.kline import fetch_kline, fetch_klines

    daily = fetch_kline("600519", period="1d", limit=120)
    print(daily.source, daily.name, len(daily.bars))
    for bar in daily.bars[-3:]:
        print(bar.time, bar.open, bar.high, bar.low, bar.close, bar.volume)

    minute = fetch_kline("sh600519", period="5m", limit=240)
    payload = minute.to_dict()          # 可直接 json.dumps 落盘
    batch = fetch_klines(["600519", "000001"], period="1d", limit=60)

数据源优先级由配置项 ``kline_source`` 决定：``auto``（默认，东财 → 腾讯 → 新浪）
或显式指定单个源。指定单源时不再降级，取不到就把该源的错误原因带回来。

各源代码口径差异（已在本模块内统一）：

============ ============================== ============== ==========
数据源        接口                            复权           成交量单位
============ ============================== ============== ==========
东方财富      ``push2his .../kline/get``      前复权（fqt=1） 手
腾讯          ``fqkline`` / ``mkline``        前复权（qfq）   手
新浪          ``CN_MarketData.getKLineData``  不复权          股 → 折手
============ ============================== ============== ==========

新浪接口不支持 1 分钟、周、月 K，缺这些周期时它会被跳过（``auto`` 下自动落到别的源）；
它是三个源里唯一不复权的，命中时 :attr:`Kline.adjust` 会记成 ``none``，便于调用方判断。
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import requests

from .config import KLINE_SOURCES
from .providers.base import REQUEST_TIMEOUT, USER_AGENT, describe_error
from .symbols import Symbol, classify

# ---------------------------------------------------------------- 常量

SOURCE_AUTO = "auto"
EASTMONEY = "eastmoney"
TENCENT = "tencent"
SINA = "sina"
# 降级顺序直接取配置里的声明顺序：东财字段最全 → 腾讯次之 → 新浪兜底。
AUTO_ORDER: tuple[str, ...] = tuple(name for name in KLINE_SOURCES if name != SOURCE_AUTO)
SOURCE_LABELS = {
    SOURCE_AUTO: "自动（东财 → 腾讯 → 新浪）",
    EASTMONEY: "东方财富",
    TENCENT: "腾讯",
    SINA: "新浪",
}
VALID_SOURCES: tuple[str, ...] = KLINE_SOURCES

PERIOD_1M = "1m"
PERIOD_5M = "5m"
PERIOD_15M = "15m"
PERIOD_30M = "30m"
PERIOD_60M = "60m"
PERIOD_DAY = "1d"
PERIOD_WEEK = "1w"
PERIOD_MONTH = "1mo"
PERIODS: tuple[str, ...] = (
    PERIOD_1M,
    PERIOD_5M,
    PERIOD_15M,
    PERIOD_30M,
    PERIOD_60M,
    PERIOD_DAY,
    PERIOD_WEEK,
    PERIOD_MONTH,
)
MINUTE_PERIODS = frozenset({PERIOD_1M, PERIOD_5M, PERIOD_15M, PERIOD_30M, PERIOD_60M})
PERIOD_LABELS = {
    PERIOD_1M: "1 分钟",
    PERIOD_5M: "5 分钟",
    PERIOD_15M: "15 分钟",
    PERIOD_30M: "30 分钟",
    PERIOD_60M: "60 分钟",
    PERIOD_DAY: "日 K",
    PERIOD_WEEK: "周 K",
    PERIOD_MONTH: "月 K",
}
PERIOD_ALIASES = {
    "1": PERIOD_1M,
    "1m": PERIOD_1M,
    "1min": PERIOD_1M,
    "m1": PERIOD_1M,
    "5": PERIOD_5M,
    "5m": PERIOD_5M,
    "5min": PERIOD_5M,
    "m5": PERIOD_5M,
    "15": PERIOD_15M,
    "15m": PERIOD_15M,
    "15min": PERIOD_15M,
    "m15": PERIOD_15M,
    "30": PERIOD_30M,
    "30m": PERIOD_30M,
    "30min": PERIOD_30M,
    "m30": PERIOD_30M,
    "60": PERIOD_60M,
    "60m": PERIOD_60M,
    "60min": PERIOD_60M,
    "m60": PERIOD_60M,
    "1h": PERIOD_60M,
    "d": PERIOD_DAY,
    "1d": PERIOD_DAY,
    "day": PERIOD_DAY,
    "daily": PERIOD_DAY,
    "w": PERIOD_WEEK,
    "1w": PERIOD_WEEK,
    "week": PERIOD_WEEK,
    "weekly": PERIOD_WEEK,
    "mo": PERIOD_MONTH,
    "1mo": PERIOD_MONTH,
    "month": PERIOD_MONTH,
    "monthly": PERIOD_MONTH,
}
# 月 K 的 ``1M`` 大小写敏感，单拎出来判，避免被 lower() 折成 1 分钟。
MONTH_EXACT_ALIASES = frozenset({"1M", "M"})

ADJUST_QFQ = "qfq"
ADJUST_NONE = "none"

EASTMONEY_ENDPOINT = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EASTMONEY_UT = "7eea3edcaed734bea9cbfc24409ed989"
TENCENT_KLINE_ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_MKLINE_ENDPOINT = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
SINA_ENDPOINT = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
)

EASTMONEY_KLT = {
    PERIOD_1M: 1,
    PERIOD_5M: 5,
    PERIOD_15M: 15,
    PERIOD_30M: 30,
    PERIOD_60M: 60,
    PERIOD_DAY: 101,
    PERIOD_WEEK: 102,
    PERIOD_MONTH: 103,
}
TENCENT_PERIODS = {
    PERIOD_1M: "m1",
    PERIOD_5M: "m5",
    PERIOD_15M: "m15",
    PERIOD_30M: "m30",
    PERIOD_60M: "m60",
    PERIOD_DAY: "day",
    PERIOD_WEEK: "week",
    PERIOD_MONTH: "month",
}
SINA_SCALES = {
    PERIOD_5M: 5,
    PERIOD_15M: 15,
    PERIOD_30M: 30,
    PERIOD_60M: 60,
    PERIOD_DAY: 240,
}

MINUTE_CACHE_TTL = 20
DAILY_CACHE_TTL = 60
DEFAULT_LIMIT = 120
MAX_LIMIT = 2000

# 腾讯分钟线形如 202609231500，东财/新浪用带分隔符的可读格式，统一成后者。
_COMPACT_MINUTE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})$")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_CLOCK_RE = re.compile(r"^[ T](\d{2}:\d{2})")
# 新浪 json_v2 返回的是未加引号键名的 JS 字面量，只补对象起始位置的键。
_SINA_KEY_RE = re.compile(r"([{,]\s*)([A-Za-z_]\w*)\s*:")


@dataclass(frozen=True)
class Bar:
    """一根 K 线。``volume`` 统一为手，``amount`` 单位为元。"""

    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": self.time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "amount": self.amount,
        }


@dataclass
class Kline:
    """一只股票某个周期的 K 线序列（升序：最旧 → 最新）。"""

    symbol: str = ""
    period: str = PERIOD_DAY
    source: str = ""
    adjust: str = ""
    name: str = ""
    bars: list[Bar] = field(default_factory=list)
    error: str | None = None
    fetched_at: float = 0.0

    def __bool__(self) -> bool:
        return bool(self.bars)

    @property
    def latest(self) -> Bar | None:
        """最新一根（未收盘的那根也在这里）。"""
        return self.bars[-1] if self.bars else None

    def to_dict(self, *, include_bars: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "symbol": self.symbol,
            "period": self.period,
            "period_label": PERIOD_LABELS.get(self.period, self.period),
            "source": self.source,
            "source_label": SOURCE_LABELS.get(self.source, self.source),
            "adjust": self.adjust,
            "name": self.name,
            "count": len(self.bars),
            "error": self.error,
            "fetched_at": self.fetched_at,
        }
        if include_bars:
            payload["bars"] = [bar.to_dict() for bar in self.bars]
        return payload


# ---------------------------------------------------------------- 工具函数


def normalize_period(value: Any) -> str | None:
    """把 ``1d`` / ``day`` / ``M5`` 之类的写法收敛成规范周期名。"""
    text = str(value or "").strip()
    if not text:
        return PERIOD_DAY
    if text in MONTH_EXACT_ALIASES:
        return PERIOD_MONTH
    return PERIOD_ALIASES.get(text.lower())


def normalize_source(value: Any) -> str:
    """把来源设置收敛成 ``auto`` 或三个源之一，非法值回落到 ``auto``。"""
    text = str(value or "").strip().lower()
    return text if text in VALID_SOURCES else SOURCE_AUTO


def normalize_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return int(min(MAX_LIMIT, max(1, parsed)))


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _secid(symbol: Symbol) -> str:
    """东财的 secid：1 为沪市，0 为深市 / 北交所。"""
    return f"{1 if symbol.market == 'sh' else 0}.{symbol.code}"


def _normalize_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if match := _COMPACT_MINUTE_RE.match(text):
        # 腾讯：202609231500 → 2026-09-23 15:00
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)} {match.group(4)}:{match.group(5)}"
    if match := _DAY_RE.match(text):
        day = match.group(0)
        clock = _CLOCK_RE.match(text[match.end():])
        return f"{day} {clock.group(1)}" if clock else day
    return text


def _make_bar(
    time_value: Any,
    open_value: Any,
    close_value: Any,
    high_value: Any,
    low_value: Any,
    volume_value: Any,
    amount_value: Any = None,
    *,
    volume_scale: float = 1.0,
) -> Bar | None:
    """按「时间,开,收,高,低」建一根 K 线；任一价格缺失就丢弃这一根。"""
    moment = _normalize_time(time_value)
    open_price = _number(open_value)
    close_price = _number(close_value)
    high_price = _number(high_value)
    low_price = _number(low_value)
    if not moment or None in (open_price, close_price, high_price, low_price):
        return None
    if close_price <= 0:
        return None
    volume = _number(volume_value) or 0.0
    amount = _number(amount_value)
    return Bar(
        time=moment,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=max(0.0, volume) * volume_scale,
        amount=amount if amount is not None and amount > 0 else None,
    )


def _finalize(bars: Iterable[Bar], limit: int) -> list[Bar]:
    """按时间升序排列、同一时刻只留最后一根，再截取末尾 limit 根。"""
    ordered = sorted((bar for bar in bars if bar), key=lambda bar: bar.time)
    deduped: dict[str, Bar] = {}
    for bar in ordered:
        deduped[bar.time] = bar
    result = sorted(deduped.values(), key=lambda bar: bar.time)
    return result[-limit:]


# ---------------------------------------------------------------- 各源解析


def parse_eastmoney(payload: object, *, limit: int) -> list[Bar]:
    """东财 ``klines``：``日期,开,收,高,低,成交量,成交额,涨跌幅,涨跌额,振幅,换手率``。"""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    bars: list[Bar] = []
    for row in data.get("klines") or []:
        parts = str(row or "").split(",")
        if len(parts) < 6:
            continue
        bar = _make_bar(
            parts[0],
            parts[1],
            parts[2],
            parts[3],
            parts[4],
            parts[5],
            parts[6] if len(parts) > 6 else None,
        )
        if bar is not None:
            bars.append(bar)
    return _finalize(bars, limit)


def eastmoney_name(payload: object) -> str:
    data = payload.get("data") if isinstance(payload, dict) else None
    return str(data.get("name") or "").strip() if isinstance(data, dict) else ""


def parse_tencent_minute(payload: object, code: str, period_key: str, *, limit: int) -> list[Bar]:
    """腾讯分钟线：``["202609231500", 开, 收, 高, 低, 量(手), {}, 涨跌幅]``。"""
    return _parse_tencent(payload, code, period_key, limit)


def parse_tencent_day(payload: object, code: str, period_key: str, *, limit: int) -> list[Bar]:
    """腾讯日 / 周 / 月线：``[["2026-09-23", 开, 收, 高, 低, 量(手)], ...]``。"""
    return _parse_tencent(payload, code, period_key, limit)


def _parse_tencent(payload: object, code: str, period_key: str, limit: int) -> list[Bar]:
    bars: list[Bar] = []
    for row in _tencent_rows(payload, code, period_key):
        if len(row) < 6:
            continue
        bar = _make_bar(row[0], row[1], row[2], row[3], row[4], row[5])
        if bar is not None:
            bars.append(bar)
    return _finalize(bars, limit)


def _tencent_block(payload: object, code: str) -> dict[str, Any] | None:
    """腾讯把数据挂在 ``data[代码]`` 下；键名偶尔带别的写法，退化取第一个股票块。"""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    block = data.get(code)
    if isinstance(block, dict):
        return block
    return next(
        (value for value in data.values() if isinstance(value, dict) and "qt" in value),
        None,
    )


def _tencent_rows(payload: object, code: str, period_key: str) -> list[list[Any]]:
    block = _tencent_block(payload, code)
    if not isinstance(block, dict):
        return []
    # 日/周/月线取前复权时键名是 qfqday / qfqweek，分钟线是 m1 / m5，逐个候选。
    for name in (f"qfq{period_key}", period_key, period_key.lower(), period_key.upper()):
        rows = block.get(name)
        if isinstance(rows, list) and rows:
            return [row for row in rows if isinstance(row, list)]
    return []


def parse_sina(text: object, *, limit: int) -> list[Bar]:
    """新浪 json_v2：键名未加引号的 JS 数组，成交量单位为股。"""
    if not isinstance(text, str):
        return []
    body = text.strip()
    if not body or body in {"null", "[]"}:
        return []
    try:
        payload = json.loads(body)
    except ValueError:
        repaired = _SINA_KEY_RE.sub(r'\1"\2":', body)
        try:
            payload = json.loads(repaired)
        except ValueError:
            return []
    if not isinstance(payload, list):
        return []
    bars: list[Bar] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        bar = _make_bar(
            row.get("day"),
            row.get("open"),
            row.get("close"),
            row.get("high"),
            row.get("low"),
            row.get("volume"),
            row.get("amount"),
            volume_scale=0.01,  # 新浪给的是股，统一折算成手
        )
        if bar is not None:
            bars.append(bar)
    return _finalize(bars, limit)


# ---------------------------------------------------------------- 客户端


class KlineClient:
    """按优先级取 K 线，带进程内 TTL 缓存；线程安全。"""

    def __init__(
        self,
        source: str | Callable[[], str] = SOURCE_AUTO,
        session: requests.Session | None = None,
    ) -> None:
        self._source = source
        self.session = session or requests.Session()
        self._cache: dict[tuple[str, str, str, int], tuple[float, Kline]] = {}
        self._lock = threading.Lock()

    @property
    def source(self) -> str:
        value = self._source() if callable(self._source) else self._source
        return normalize_source(value)

    def _order(self) -> tuple[str, ...]:
        source = self.source
        return AUTO_ORDER if source == SOURCE_AUTO else (source,)

    def _cache_ttl(self, period: str) -> float:
        return MINUTE_CACHE_TTL if period in MINUTE_PERIODS else DAILY_CACHE_TTL

    def fetch(
        self,
        raw_symbol: str,
        period: str = PERIOD_DAY,
        limit: int = DEFAULT_LIMIT,
        *,
        adjust: bool = True,
        now: float | None = None,
    ) -> Kline:
        """取一只股票某个周期的 K 线。

        ``period`` 支持 ``1d``/``day``/``5m``/``1M`` 等写法，见 :func:`normalize_period`；
        ``adjust=False`` 时东财/腾讯取不复权，新浪本身只有不复权。
        """
        name = normalize_period(period)
        if name is None:
            return Kline(
                symbol=str(raw_symbol or "").strip(),
                period=PERIOD_DAY,
                error=f"不支持的周期：{period}（可用：{', '.join(PERIODS)}）",
            )
        symbol = classify(raw_symbol)
        if symbol is None:
            return Kline(symbol=str(raw_symbol or "").strip(), period=name, error="代码格式不正确")

        count = normalize_limit(limit)
        adjust_key = ADJUST_QFQ if adjust else ADJUST_NONE
        key = (symbol.code, name, adjust_key, count)
        moment = time.time() if now is None else float(now)

        with self._lock:
            cached = self._cache.get(key)
            if cached and moment - cached[0] < self._cache_ttl(name):
                return cached[1]

        result = self._load(symbol, name, count, adjust)
        with self._lock:
            self._cache[key] = (moment, result)
        return result

    def _load(self, symbol: Symbol, period: str, limit: int, adjust: bool) -> Kline:
        last: Kline | None = None
        for source in self._order():
            try:
                bars, name, actual_adjust = self._fetch(source, symbol, period, limit, adjust)
            except Exception as exc:  # 单个源异常只换下一个
                if last is None:
                    last = Kline(
                        symbol=symbol.code,
                        period=period,
                        source=source,
                        error=describe_error(exc),
                    )
                continue
            if bars:
                return Kline(
                    symbol=symbol.code,
                    period=period,
                    source=source,
                    adjust=actual_adjust,
                    name=name or symbol.code,
                    bars=bars,
                    fetched_at=time.time(),
                )
            if last is None:
                last = Kline(symbol=symbol.code, period=period, source=source, error="无数据")
        # 全都没出数：把优先级最高那个源的原因原样带回去，让调用方看到究竟卡在哪。
        if last is None:
            last = Kline(symbol=symbol.code, period=period, error="没有可用的数据源")
        return last

    def _fetch(
        self,
        source: str,
        symbol: Symbol,
        period: str,
        limit: int,
        adjust: bool,
    ) -> tuple[list[Bar], str, str]:
        """返回 ``(bars, 名称, 实际复权口径)``；某源不支持该周期时抛 ``ValueError``。"""
        if source == EASTMONEY:
            payload = self._get_eastmoney(symbol, period, limit, adjust)
            return parse_eastmoney(payload, limit=limit), eastmoney_name(payload), (
                ADJUST_QFQ if adjust else ADJUST_NONE
            )
        if source == TENCENT:
            payload = self._get_tencent(symbol, period, limit, adjust)
            period_key = TENCENT_PERIODS[period]
            if period in MINUTE_PERIODS:
                bars = parse_tencent_minute(payload, symbol.key, period_key, limit=limit)
            else:
                bars = parse_tencent_day(payload, symbol.key, period_key, limit=limit)
            return bars, _tencent_name(payload, symbol.key), (
                ADJUST_QFQ if adjust else ADJUST_NONE
            )
        if source == SINA:
            text = self._get_sina(symbol, period, limit)
            return parse_sina(text, limit=limit), "", ADJUST_NONE
        raise ValueError(f"未知数据源：{source}")

    # ------------------------------------------------------------ 三个源的请求

    def _get_eastmoney(self, symbol: Symbol, period: str, limit: int, adjust: bool) -> object:
        response = self.session.get(
            EASTMONEY_ENDPOINT,
            params={
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "ut": EASTMONEY_UT,
                "klt": EASTMONEY_KLT[period],
                "fqt": 1 if adjust else 0,
                "secid": _secid(symbol),
                "beg": 0,
                "end": 20500101,
                "lmt": limit,
            },
            headers={"User-Agent": USER_AGENT, "Referer": "https://quote.eastmoney.com/"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def _get_tencent(self, symbol: Symbol, period: str, limit: int, adjust: bool) -> object:
        key = TENCENT_PERIODS[period]
        if period in MINUTE_PERIODS:
            endpoint = TENCENT_MKLINE_ENDPOINT
            param = f"{symbol.key},{key},,{limit}"
        else:
            # 形如 sh600519,day,,,320,qfq：起止日期留空表示「最近 N 根」。
            endpoint = TENCENT_KLINE_ENDPOINT
            param = f"{symbol.key},{key},,,{limit},{'qfq' if adjust else ''}"
        response = self.session.get(
            endpoint,
            params={"param": param},
            headers={"User-Agent": USER_AGENT, "Referer": "https://gu.qq.com/"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def _get_sina(self, symbol: Symbol, period: str, limit: int) -> str:
        scale = SINA_SCALES.get(period)
        if scale is None:
            raise ValueError(f"新浪不支持 {PERIOD_LABELS.get(period, period)}")
        response = self.session.get(
            SINA_ENDPOINT,
            params={"symbol": symbol.key, "scale": scale, "ma": "no", "datalen": limit},
            headers={"User-Agent": USER_AGENT, "Referer": "https://finance.sina.com.cn/"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        response.encoding = "utf-8"
        return response.text

    def reset_cache(self) -> None:
        with self._lock:
            self._cache.clear()


def _tencent_name(payload: object, code: str) -> str:
    """从 ``qt`` 块里取股票名，腾讯的 K 线响应顺手带了快照。"""
    block = _tencent_block(payload, code)
    if not isinstance(block, dict):
        return ""
    rows = block.get("qt")
    quote = rows.get(code) if isinstance(rows, dict) else None
    if not isinstance(quote, list) and isinstance(rows, dict) and rows:
        quote = next((value for value in rows.values() if isinstance(value, list)), None)
    return str(quote[1]).strip() if isinstance(quote, list) and len(quote) > 1 else ""


# ---------------------------------------------------------------- 模块级入口

_CLIENTS: dict[str, KlineClient] = {}
_CLIENTS_LOCK = threading.Lock()
_SOURCE_MEMO: tuple[float, str] | None = None
SOURCE_MEMO_SECONDS = 2.0
_SOURCE_MEMO_LOCK = threading.Lock()


def configured_source() -> str:
    """读取配置项的 K 线数据源；读不到（首次运行、配置损坏）时按 ``auto``。

    结果做 2 秒短缓存：批量取数时不必为每只股票各读一次配置文件，
    同时 WebUI 改完设置也能在秒级生效。
    """
    global _SOURCE_MEMO
    now = time.monotonic()
    with _SOURCE_MEMO_LOCK:
        if _SOURCE_MEMO is not None and now - _SOURCE_MEMO[0] < SOURCE_MEMO_SECONDS:
            return _SOURCE_MEMO[1]
    try:
        from .config import Store

        value = normalize_source(Store().get().kline_source)
    except Exception:
        value = SOURCE_AUTO
    with _SOURCE_MEMO_LOCK:
        _SOURCE_MEMO = (now, value)
    return value


def clear_source_memo() -> None:
    """清掉数据源设置的短缓存，配置刚变更时调用。"""
    global _SOURCE_MEMO
    with _SOURCE_MEMO_LOCK:
        _SOURCE_MEMO = None


def client(source: str | None = None) -> KlineClient:
    """取一个带缓存的客户端；不传 source 时跟随配置（配置改动即时生效）。"""
    if source is None:
        key = SOURCE_AUTO
        with _CLIENTS_LOCK:
            if key not in _CLIENTS:
                _CLIENTS[key] = KlineClient(source=configured_source)
            return _CLIENTS[key]
    resolved = normalize_source(source)
    with _CLIENTS_LOCK:
        if resolved not in _CLIENTS:
            _CLIENTS[resolved] = KlineClient(source=resolved)
        return _CLIENTS[resolved]


def fetch_kline(
    raw_symbol: str,
    period: str = PERIOD_DAY,
    limit: int = DEFAULT_LIMIT,
    *,
    adjust: bool = True,
    source: str | None = None,
    now: float | None = None,
) -> Kline:
    """取一只股票的 K 线；``source`` 留空则按配置的优先级自动降级。"""
    return client(source).fetch(raw_symbol, period, limit, adjust=adjust, now=now)


def fetch_klines(
    symbols: Iterable[str],
    period: str = PERIOD_DAY,
    limit: int = DEFAULT_LIMIT,
    *,
    adjust: bool = True,
    source: str | None = None,
) -> dict[str, Kline]:
    """批量取 K 线，返回 ``{代码: Kline}``；单只失败不影响其他。"""
    client_ = client(source)
    result: dict[str, Kline] = {}
    for raw in symbols:
        kline = client_.fetch(raw, period, limit, adjust=adjust)
        key = kline.symbol or str(raw or "").strip()
        result[key] = kline
    return result


def reset_cache() -> None:
    """清空所有客户端的缓存（配置切换数据源后调用）。"""
    with _CLIENTS_LOCK:
        clients = list(_CLIENTS.values())
    for item in clients:
        item.reset_cache()
