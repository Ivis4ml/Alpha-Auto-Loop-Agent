"""市场信息的只读视图协议。

新闻子系统需要交易日历与收盘截止时刻来做情绪面板的归日（point-in-time
归入下一可交易时点），但不允许 import 市场描述层的实现；由 apps 装配时
注入本协议的实现。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

__all__ = ["MarketCalendarView"]


class MarketCalendarView(Protocol):
    """跨市场统一的日历只读接口。"""

    def timezone(self, market_id: str) -> str:
        """返回该市场的 IANA 时区名，供 UTC 时间戳转本地墙钟。"""
        ...

    def trading_days(self, market_id: str, start: date, end: date) -> list[date]:
        """返回闭区间内的交易日序列。"""
        ...

    def session_cutoff(self, market_id: str, day: date) -> datetime:
        """返回该交易日的收盘截止时刻（该市场本地墙钟，tz-naive）。

        可见时刻晚于该截止点的新闻归入下一个可交易时点。
        """
        ...

    def next_trading_day(self, market_id: str, day: date) -> date:
        """返回严格晚于 day 的下一个交易日。"""
        ...
