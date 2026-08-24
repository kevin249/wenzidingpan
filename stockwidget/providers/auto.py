"""自动数据源：挨个试，用第一个真出数的。

三个公开接口都可能被限流、被网络策略拦掉或临时抽风，让用户自己盯着切换很烦，
这里按顺序回退，并记住上次成功的那个，下次优先用它。
"""

from __future__ import annotations

from .base import Provider, Quote


class AutoProvider:
    id = "auto"
    label = "自动（东财 → 腾讯 → 新浪）"
    placeholder = "600519, 000001, 300750, sh601318"

    def __init__(self, chain: list[Provider]) -> None:
        self._chain = chain
        self._preferred: Provider | None = None

    @property
    def last_used(self) -> str | None:
        """最近一次真正出数的数据源 id，界面上会跟在「自动」后面显示。"""
        return self._preferred.id if self._preferred else None

    def _order(self) -> list[Provider]:
        if self._preferred is None:
            return list(self._chain)
        # 上次能用的排最前，其余保持原顺序。
        return [self._preferred] + [p for p in self._chain if p is not self._preferred]

    def fetch(self, symbols: list[str]) -> list[Quote]:
        last: list[Quote] | None = None
        for provider in self._order():
            try:
                quotes = provider.fetch(symbols)
            except Exception:  # 单个源抛异常也只是换下一个
                continue
            if any(q.error is None and q.price is not None for q in quotes):
                self._preferred = provider
                return quotes
            last = last or quotes
        # 全都没出数，把第一个源的错误原样带回去，让用户看到究竟卡在哪。
        self._preferred = None
        return last if last is not None else [Quote.failed(s, "没有可用的数据源") for s in symbols]
