"""市场描述协议：市场之间的全部差异收敛于此，供上层以纯数据方式消费。

设计原则（任务书 3.2）：研究、回测、因子与治理逻辑不出现按市场分支的
判断；新增一个市场只需要新增一份市场描述。本模块自身保持市场中立，
具体市场的规则只允许出现在各市场子包内。

日历数据（交易日序列、分钟网格）由数据层从行情数据推导后注入，本层
不做任何 IO；这也保证了市场层的纯函数性质。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd

__all__ = [
    "DataConventions",
    "ExecutionProfile",
    "SessionSpec",
    "TradingCalendar",
    "TradabilityModel",
    "CorporateActionModel",
    "UniverseRule",
    "MarketSpec",
]


@dataclass(frozen=True, slots=True)
class DataConventions:
    """原始存储的口径声明，读取层据此归一到规范 schema。"""

    timezone: str
    volume_multiplier: int
    amount_native_minute: bool
    amount_native_daily: bool
    has_minute_vwap: bool
    has_trade_count: bool
    rth_includes_close_auction: bool
    currency: str


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    """执行侧约束声明，交易计划编译为订单意图时消费。"""

    supports_live_adapter: bool
    lot_size: int
    min_holding_days: int
    stamp_tax_sell_bps: float
    price_limit_rule: str | None
    supports_short: bool


@dataclass(frozen=True, slots=True)
class SessionSpec:
    """常规交易时段声明：若干个 (起始, 结束) 的 HHMM 闭区间，按 bar 收盘标签判定。"""

    buckets: tuple[tuple[str, str], ...]

    def is_rth(self, minute_label: str) -> bool:
        """判断 bar 收盘标签是否落在常规交易时段内。"""
        return any(start <= minute_label <= end for start, end in self.buckets)


class TradingCalendar(Protocol):
    """交易日历：交易日序列与结算锚点。日历数据由数据层推导后注入实现。"""

    def trading_days(self, start: date, end: date) -> list[date]:
        """返回闭区间内的交易日序列，升序。"""
        ...

    def is_trading_day(self, day: date) -> bool:
        """判断是否交易日。"""
        ...

    def next_trading_day(self, day: date) -> date:
        """返回严格晚于 day 的下一个交易日。"""
        ...

    def earliest_sell_day(self, buy_day: date) -> date:
        """返回 buy_day 买入后最早允许卖出的交易日（结算锚点）。"""
        ...

    def session(self) -> SessionSpec:
        """返回常规交易时段声明。"""
        ...

    def session_cutoff_label(self) -> str:
        """返回收盘截止的 HHMM 标签，供 point-in-time 归日。"""
        ...


class TradabilityModel(Protocol):
    """可交易性模型：把该市场的交易制度归约为逐行 buy_ok / sell_ok 标记。"""

    def attach_flags(self, daily: pd.DataFrame) -> pd.DataFrame:
        """在规范日线面板上追加 buy_ok、sell_ok 布尔列并返回新对象。"""
        ...


class CorporateActionModel(Protocol):
    """公司行动模型：复权因子或缺口标注。"""

    adjustment_available: bool

    def adjust(self, daily: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
        """在规范日线面板上追加 adj_factor 列（乘法因子）并返回新对象。

        无复权数据的市场恒置 1.0，并另行提供嫌疑标注（见 flag_suspect_gaps）。
        """
        ...

    def flag_suspect_gaps(self, daily: pd.DataFrame) -> pd.DataFrame:
        """追加 suspect_gap 布尔列：隔夜价差无法用正常交易解释、疑似除权的交易日。

        有复权数据的市场可恒置 False。该标注用于把嫌疑日收益从评估样本
        中剔除或隔离，属于显式标注缺口的一部分。
        """
        ...


class UniverseRule(Protocol):
    """股票池规则：从规范日线面板选出可交易标的集合。"""

    def select(self, daily: pd.DataFrame, as_of: date, size: int) -> list[str]:
        """按该市场口径选出 as_of 当日的头部流动性标的，返回代码列表。"""
        ...


@dataclass(frozen=True, slots=True)
class MarketSpec:
    """一个市场的完整描述。"""

    market_id: str
    calendar: TradingCalendar
    conventions: DataConventions
    execution: ExecutionProfile
    tradability: TradabilityModel
    corporate_actions: CorporateActionModel
    universe: UniverseRule
    benchmark_symbol: str
    price_tick: float
    index_symbol_rule: Callable[[str], bool]

    def normalize_symbol(self, raw: str) -> str:
        """代码归一：去空白并大写。两个已实现市场的原生代码经此即得规范形式。"""
        return raw.strip().upper()

    def is_index_symbol(self, symbol: str) -> bool:
        """判断标的是否指数类，规则由市场描述提供。"""
        return self.index_symbol_rule(symbol)
