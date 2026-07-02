"""基于注入交易日序列的通用日历实现。

交易日序列由数据层从行情数据推导（数据即权威，自动涵盖节假日与临时
休市），本实现只做纯查询；结算延迟（最早卖出日）以持有天数参数表达，
具体取值由各市场描述给出。
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from datetime import date

from alphaloop.market.protocols import SessionSpec

__all__ = ["ListedCalendar"]


class ListedCalendar:
    """交易日序列 + 时段声明 + 结算延迟的日历实现，满足 TradingCalendar 协议。"""

    def __init__(
        self,
        trading_days: Sequence[date],
        session: SessionSpec,
        *,
        settlement_lag_days: int,
        cutoff_label: str,
    ) -> None:
        if not trading_days:
            raise ValueError("交易日序列不能为空")
        ordered = sorted(set(trading_days))
        if settlement_lag_days < 0:
            raise ValueError("结算延迟不能为负")
        self._days: list[date] = ordered
        self._day_set = frozenset(ordered)
        self._session = session
        self._settlement_lag_days = settlement_lag_days
        self._cutoff_label = cutoff_label

    def trading_days(self, start: date, end: date) -> list[date]:
        """返回闭区间内的交易日序列，升序。"""
        lo = bisect.bisect_left(self._days, start)
        hi = bisect.bisect_right(self._days, end)
        return self._days[lo:hi]

    def is_trading_day(self, day: date) -> bool:
        """判断是否交易日。"""
        return day in self._day_set

    def next_trading_day(self, day: date) -> date:
        """返回严格晚于 day 的下一个交易日；超出已知范围时报错而不外推。"""
        index = bisect.bisect_right(self._days, day)
        if index >= len(self._days):
            raise ValueError(f"{day} 之后没有已知交易日，日历覆盖至 {self._days[-1]}")
        return self._days[index]

    def earliest_sell_day(self, buy_day: date) -> date:
        """返回 buy_day 买入后最早允许卖出的交易日。

        延迟为 0 表示当日可卖；为 n 表示顺延 n 个交易日。buy_day 必须是
        交易日，否则视为调用方错误。
        """
        if buy_day not in self._day_set:
            raise ValueError(f"{buy_day} 不是交易日，无法计算结算锚点")
        day = buy_day
        for _ in range(self._settlement_lag_days):
            day = self.next_trading_day(day)
        return day

    def session(self) -> SessionSpec:
        """返回常规交易时段声明。"""
        return self._session

    def session_cutoff_label(self) -> str:
        """返回收盘截止的 HHMM 标签。"""
        return self._cutoff_label
