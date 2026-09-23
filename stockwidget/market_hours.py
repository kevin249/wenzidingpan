"""A 股活跃时段判断：控制实时行情、K线和 MCP 深度的主动刷新。"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

# 把集合竞价纳入活跃窗口：9:15 起报价/盘口已经有调试和盯盘价值。
MORNING_START = time(9, 15)
MORNING_END = time(11, 30)
AFTERNOON_START = time(13, 0)
AFTERNOON_END = time(15, 0)

# 非交易时段线程只做本地时间检查，不发网络请求；30 秒内自动跟上开盘。
OFF_HOURS_WAKE_SECONDS = 30.0

# 非 Debug 模式在活跃时段只保留的低频兜底心跳。
# 常态由 MCP 推送事件驱动刷新，心跳仅用于发现「推送通道静默失效」这一盲区。
FALLBACK_HEARTBEAT_SECONDS = 300.0


def _shanghai_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(SHANGHAI_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=SHANGHAI_TZ)
    return now.astimezone(SHANGHAI_TZ)


def is_a_share_active_time(now: datetime | None = None) -> bool:
    """周一到周五的 A 股盘中/集合竞价活跃窗口。

    这里故意不额外引入交易日历依赖；节假日若落在工作日，时间窗内仍可能唤醒，
    但收盘后、午休和周末会完全停止主动实时请求。
    """
    current = _shanghai_now(now)
    if current.weekday() >= 5:
        return False

    clock = current.time().replace(tzinfo=None)
    return (
        MORNING_START <= clock <= MORNING_END
        or AFTERNOON_START <= clock <= AFTERNOON_END
    )


def active_updates_allowed(debug_mode: bool, now: datetime | None = None) -> bool:
    """循环轮询的许可窗口：Debug 全天，非 Debug 只允许 A 股活跃时段。

    注意这只是「定时轮询」的许可，不代表用户显式动作也要被拦。启动首次请求、
    手动刷新、MCP 推送触发的刷新都属于显式意图，绕过本判断直接执行。
    """
    return bool(debug_mode) or is_a_share_active_time(now)
