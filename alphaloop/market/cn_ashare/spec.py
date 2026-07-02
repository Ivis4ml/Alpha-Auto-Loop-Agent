"""A 股市场描述装配。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from alphaloop.market.calendar_base import ListedCalendar
from alphaloop.market.cn_ashare.rules import (
    CnNullAdjustment,
    CnTopAmountUniverse,
    CnTradability,
    is_index_symbol,
)
from alphaloop.market.protocols import (
    DataConventions,
    ExecutionProfile,
    MarketSpec,
    SessionSpec,
)

__all__ = ["build_cn_ashare_spec", "CN_SESSION"]

CN_SESSION = SessionSpec(buckets=(("0930", "1130"), ("1301", "1500")))


def build_cn_ashare_spec(trading_days: Sequence[date]) -> MarketSpec:
    """由数据层推导的交易日序列装配 A 股市场描述。

    时段声明含 09:30 开盘集合竞价 bar 与 15:00 收盘集合竞价 bar（源数据
    构建时即不做时段过滤）；结算延迟 1 个交易日（买入次一交易日方可
    卖出）；印花税按 2023-08 之后的卖出单边 0.05% 计。
    """
    calendar = ListedCalendar(
        trading_days,
        CN_SESSION,
        settlement_lag_days=1,
        cutoff_label="1500",
    )
    conventions = DataConventions(
        timezone="Asia/Shanghai",
        volume_multiplier=100,
        amount_native_minute=True,
        amount_native_daily=True,
        has_minute_vwap=True,
        has_trade_count=False,
        rth_includes_close_auction=True,
        currency="CNY",
    )
    execution = ExecutionProfile(
        supports_live_adapter=False,
        lot_size=100,
        min_holding_days=1,
        stamp_tax_sell_bps=5.0,
        price_limit_rule="board_tiered",
        supports_short=False,
    )
    return MarketSpec(
        market_id="cn_ashare",
        calendar=calendar,
        conventions=conventions,
        execution=execution,
        tradability=CnTradability(),
        corporate_actions=CnNullAdjustment(),
        universe=CnTopAmountUniverse(),
        benchmark_symbol="000300.SH",
        price_tick=0.01,
        index_symbol_rule=is_index_symbol,
    )
