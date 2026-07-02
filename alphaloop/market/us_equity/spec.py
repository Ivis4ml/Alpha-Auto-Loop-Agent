"""美股市场描述装配。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from alphaloop.market.calendar_base import ListedCalendar
from alphaloop.market.protocols import (
    DataConventions,
    ExecutionProfile,
    MarketSpec,
    SessionSpec,
)
from alphaloop.market.us_equity.rules import (
    UsCumulativeAdjustment,
    UsTopAmountUniverse,
    UsTradability,
    is_index_symbol,
)

__all__ = ["build_us_equity_spec", "US_SESSION"]

US_SESSION = SessionSpec(buckets=(("0931", "1600"),))


def build_us_equity_spec(
    trading_days: Sequence[date],
    fund_like_symbols: frozenset[str] = frozenset(),
) -> MarketSpec:
    """由数据层推导的交易日序列装配美股市场描述。

    常规交易时段以 bar 收盘标签 09:31 至 16:00 判定（390 根分钟 bar）；
    分钟数据含盘前盘后，is_rth 只打标不过滤。当日买入当日可卖
    （结算延迟 0），无涨跌停制度，支持做空。fund_like_symbols 为数据层
    按名称识别的基金类载体，选池予以排除。
    """
    calendar = ListedCalendar(
        trading_days,
        US_SESSION,
        settlement_lag_days=0,
        cutoff_label="1600",
    )
    conventions = DataConventions(
        timezone="America/New_York",
        volume_multiplier=1,
        amount_native_minute=False,
        amount_native_daily=False,
        has_minute_vwap=False,
        has_trade_count=True,
        rth_includes_close_auction=True,
        currency="USD",
    )
    execution = ExecutionProfile(
        supports_live_adapter=True,
        lot_size=1,
        min_holding_days=0,
        stamp_tax_sell_bps=0.0,
        price_limit_rule=None,
        supports_short=True,
    )
    return MarketSpec(
        market_id="us_equity",
        calendar=calendar,
        conventions=conventions,
        execution=execution,
        tradability=UsTradability(),
        corporate_actions=UsCumulativeAdjustment(),
        universe=UsTopAmountUniverse(excluded_symbols=fund_like_symbols),
        benchmark_symbol="SPY",
        price_tick=0.01,
        index_symbol_rule=is_index_symbol,
    )
