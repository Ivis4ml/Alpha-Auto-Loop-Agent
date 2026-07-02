"""股票池构建：数据层提供流动性切片，选取规则由市场描述给出。"""

from __future__ import annotations

from datetime import date, timedelta

from alphaloop.data.minute_store import MinuteStore

__all__ = ["build_universe"]

_CALENDAR_BUFFER_DAYS = 45


def build_universe(store: MinuteStore, as_of: date, size: int) -> list[str]:
    """选出 as_of 当日的头部流动性标的。

    数据层负责取出覆盖选取窗口的日线流动性切片（近 45 个日历日足以
    覆盖 20 个交易日），排序规则（中位成交额 top-N、剔除指数）由市场
    描述的 UniverseRule 决定，本函数不含任何市场特有判断。
    """
    if size <= 0:
        raise ValueError(f"股票池规模必须为正: {size}")
    start = as_of - timedelta(days=_CALENDAR_BUFFER_DAYS)
    liquidity = store.daily_liquidity_frame(start, as_of)
    return store.market.universe.select(liquidity, as_of, size)
