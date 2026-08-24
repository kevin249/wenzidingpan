"""数据源的公共约定。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Quote:
    """统一的行情结构，所有数据源都要返回它。"""

    symbol: str  # 用户填写的原始写法
    name: str
    price: float | None = None
    prev_close: float | None = None
    change: float | None = None
    change_percent: float | None = None
    currency: str = "CNY"
    halted: bool = False
    error: str | None = None  # 该代码取数失败的原因，成功时为 None
    dark_fund: float | None = None  # 东财暗盘资金，单位元
    dark_main_net_inflow: float | None = None

    @classmethod
    def failed(cls, symbol: str, error: str) -> "Quote":
        return cls(symbol=symbol, name=symbol, error=error)

    @classmethod
    def from_prices(cls, symbol: str, name: str, price: float, prev_close: float) -> "Quote":
        """停牌时现价为 0，退回昨收，避免显示成跌停 -100%。"""
        halted = price == 0
        effective = prev_close if halted else price
        change = effective - prev_close
        return cls(
            symbol=symbol,
            name=name or symbol,
            price=effective,
            prev_close=prev_close,
            change=change,
            change_percent=(change / prev_close * 100) if prev_close else None,
            halted=halted,
        )


class Provider(Protocol):
    """数据源接口：加一个新数据源只需实现这三个属性和一个方法。"""

    id: str
    label: str
    placeholder: str

    def fetch(self, symbols: list[str]) -> list[Quote]:
        ...


REQUEST_TIMEOUT = 8
USER_AGENT = "Mozilla/5.0"


def describe_error(exc: BaseException) -> str:
    """把异常压成一行能放进界面的短文本。"""
    import requests

    if isinstance(exc, requests.Timeout):
        return "请求超时"
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, requests.ConnectionError):
        return "网络不可达"
    return str(exc)[:60] or exc.__class__.__name__
