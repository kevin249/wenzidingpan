"""日K 异步取数：只有展开到日K 时才拉，且绝不阻塞界面线程。

K 线走 ``stockwidget.kline``（东财 → 腾讯 → 新浪，不经 MCP），单次请求最坏
几秒。直接放在点击回调里会把整个组件冻住，所以统一丢到线程池，取完再通过
信号交回界面线程；同一只股票同一周期在途时不会重复发请求。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..kline import DEFAULT_LIMIT, PERIOD_DAY, Kline, fetch_kline

# 组件很窄，几十根足够铺满；拉太多既慢又画不出差别。
UI_LIMIT = 60


class _KlineTask(QRunnable):
    def __init__(self, owner: "KlineLoader", symbol: str, period: str, limit: int) -> None:
        super().__init__()
        self._owner = owner
        self._symbol = symbol
        self._period = period
        self._limit = limit
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D102 - QRunnable 入口
        try:
            kline = fetch_kline(self._symbol, self._period, self._limit)
        except Exception as exc:  # 取数层已兜底，这里只防极端情况把线程带走
            kline = Kline(symbol=self._symbol, period=self._period, error=str(exc)[:80])
        self._owner._deliver(self._symbol, self._period, kline)


class KlineLoader(QObject):
    """线程池 + 去重 + 信号回主线程的 K 线取数器。"""

    loaded = Signal(str, str, object)  # symbol, period, Kline

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._inflight: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    def request(
        self,
        symbol: str,
        period: str = PERIOD_DAY,
        limit: int = UI_LIMIT,
    ) -> None:
        """发起一次取数；同一 (代码, 周期) 在途时直接忽略。"""
        if not symbol:
            return
        key = (symbol, period)
        with self._lock:
            if key in self._inflight:
                return
            self._inflight.add(key)
        self._pool.start(_KlineTask(self, symbol, period, max(1, limit or DEFAULT_LIMIT)))

    def _deliver(self, symbol: str, period: str, kline: Kline) -> None:
        with self._lock:
            self._inflight.discard((symbol, period))
        self.loaded.emit(symbol, period, kline)


_LOADER: KlineLoader | None = None
_LOADER_LOCK = threading.Lock()


def loader() -> KlineLoader:
    """进程内单例；必须在 QApplication 建好之后才会被首次调用。"""
    global _LOADER
    with _LOADER_LOCK:
        if _LOADER is None:
            _LOADER = KlineLoader()
        return _LOADER
