"""市场描述层的行为测试：日历、结算锚点、可交易性、复权、注册表。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from alphaloop.data.minute_store import MinuteStore
from alphaloop.market.calendar_base import ListedCalendar
from alphaloop.market.cn_ashare.rules import (
    CnNullAdjustment,
    CnTradability,
    price_limit_ratio,
)
from alphaloop.market.cn_ashare.rules import (
    is_index_symbol as cn_is_index,
)
from alphaloop.market.cn_ashare.spec import CN_SESSION
from alphaloop.market.protocols import MarketSpec, SessionSpec
from alphaloop.market.registry import clear_registry, get_market, list_markets, register_market
from alphaloop.market.us_equity.spec import US_SESSION

WEEK = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 8)]


class TestCalendar:
    def test_next_trading_day_skips_weekend(self) -> None:
        calendar = ListedCalendar(WEEK, CN_SESSION, settlement_lag_days=1, cutoff_label="1500")
        assert calendar.next_trading_day(date(2024, 1, 5)) == date(2024, 1, 8)

    def test_next_trading_day_beyond_range_raises(self) -> None:
        calendar = ListedCalendar(WEEK, CN_SESSION, settlement_lag_days=1, cutoff_label="1500")
        with pytest.raises(ValueError):
            calendar.next_trading_day(date(2024, 1, 8))

    def test_cn_settlement_is_next_day(self, cn_store: MinuteStore) -> None:
        calendar = cn_store.market.calendar
        assert calendar.earliest_sell_day(date(2024, 1, 5)) == date(2024, 1, 8)

    def test_us_settlement_is_same_day(self, us_store: MinuteStore) -> None:
        calendar = us_store.market.calendar
        assert calendar.earliest_sell_day(date(2024, 1, 5)) == date(2024, 1, 5)

    def test_session_specs(self) -> None:
        assert CN_SESSION.is_rth("0930")
        assert CN_SESSION.is_rth("1130")
        assert not CN_SESSION.is_rth("1300")
        assert CN_SESSION.is_rth("1301")
        assert CN_SESSION.is_rth("1500")
        assert not CN_SESSION.is_rth("1501")
        assert US_SESSION.is_rth("0931")
        assert US_SESSION.is_rth("1600")
        assert not US_SESSION.is_rth("0930")
        assert not US_SESSION.is_rth("1601")


class TestCnRules:
    def test_index_symbol_rule(self) -> None:
        assert cn_is_index("000300.SH")
        assert cn_is_index("399006.SZ")
        assert cn_is_index("899050.BJ")
        assert not cn_is_index("000001.SZ")
        assert not cn_is_index("600519.SH")
        assert not cn_is_index("688111.SH")
        assert not cn_is_index("430047.BJ")

    def test_price_limit_ratio_by_board(self) -> None:
        assert price_limit_ratio("600519.SH") == 0.10
        assert price_limit_ratio("300750.SZ") == 0.20
        assert price_limit_ratio("688111.SH") == 0.20
        assert price_limit_ratio("430047.BJ") == 0.30

    def test_one_bar_limit_up_blocks_buy(self) -> None:
        daily = pd.DataFrame(
            {
                "symbol": ["600001.SH"] * 3,
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                "open": [10.0, 11.0, 12.1],
                "high": [10.0, 11.0, 12.1],
                "low": [10.0, 10.5, 12.1],
                "close": [10.0, 11.0, 12.1],
                "volume": [1000, 1000, 100],
                "amount": [1e4, 1.1e4, 1.2e3],
                "amount_is_estimated": [False] * 3,
                "is_index": [False] * 3,
            }
        )
        flagged = CnTradability().attach_flags(daily)
        by_date = flagged.set_index("trade_date")
        assert bool(by_date.loc[date(2024, 1, 3), "buy_ok"])
        assert not bool(by_date.loc[date(2024, 1, 4), "buy_ok"])
        assert bool(by_date.loc[date(2024, 1, 4), "sell_ok"])

    def test_suspect_gap_flagged_beyond_limit(self) -> None:
        daily = pd.DataFrame(
            {
                "symbol": ["600001.SH"] * 2,
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
                "open": [20.0, 14.0],
                "high": [20.5, 14.5],
                "low": [19.8, 13.9],
                "close": [20.0, 14.2],
                "volume": [1000, 1200],
                "amount": [2e4, 1.7e4],
                "amount_is_estimated": [False] * 2,
                "is_index": [False] * 2,
            }
        )
        flagged = CnNullAdjustment().flag_suspect_gaps(daily)
        assert not bool(flagged["suspect_gap"].iloc[0])
        assert bool(flagged["suspect_gap"].iloc[1])

    def test_null_adjustment_factor_is_one(self) -> None:
        daily = pd.DataFrame(
            {
                "symbol": ["600001.SH"],
                "trade_date": [date(2024, 1, 2)],
                "open": [10.0],
                "high": [10.0],
                "low": [10.0],
                "close": [10.0],
                "volume": [100],
                "amount": [1e3],
                "amount_is_estimated": [False],
                "is_index": [False],
            }
        )
        adjusted = CnNullAdjustment().adjust(daily, pd.DataFrame())
        assert (adjusted["adj_factor"] == 1.0).all()


class TestUsAdjustment:
    """以 NVDA 2024-06-10 一拆十的真实事件校验复权因子语义。"""

    def test_split_factor_recovers_continuity(self, us_store: MinuteStore) -> None:
        daily = us_store.read_daily(["NVDA"], date(2024, 1, 2), date(2024, 12, 31))
        actions = us_store.read_corp_actions()
        adjusted = us_store.market.corporate_actions.adjust(daily, actions)
        adjusted = adjusted.sort_values("trade_date").reset_index(drop=True)
        before = adjusted[adjusted["trade_date"] < date(2024, 6, 10)].iloc[-1]
        after = adjusted[adjusted["trade_date"] >= date(2024, 6, 10)].iloc[0]
        factor_jump = before["adj_factor"] / after["adj_factor"]
        assert abs(factor_jump - 0.1) < 0.005
        raw_return = after["close"] / before["close"] - 1.0
        adj_return = (after["close"] * after["adj_factor"]) / (
            before["close"] * before["adj_factor"]
        ) - 1.0
        assert raw_return < -0.5
        assert abs(adj_return) < 0.10

    def test_us_no_suspect_gaps(self, us_store: MinuteStore) -> None:
        daily = us_store.read_daily(["NVDA"], date(2024, 6, 1), date(2024, 6, 30))
        flagged = us_store.market.corporate_actions.flag_suspect_gaps(daily)
        assert not flagged["suspect_gap"].any()


class TestRegistry:
    def test_register_and_get(self, us_store: MinuteStore, cn_store: MinuteStore) -> None:
        clear_registry()
        register_market(us_store.market)
        register_market(cn_store.market)
        assert list_markets() == ["cn_ashare", "us_equity"]
        assert get_market("us_equity").market_id == "us_equity"
        with pytest.raises(ValueError):
            register_market(us_store.market)
        clear_registry()

    def test_get_unknown_market(self) -> None:
        clear_registry()
        with pytest.raises(KeyError):
            get_market("mars_equity")


class TestSpecShape:
    """新增市场只需要一份描述：两份描述给出同一组协议对象（验收 7.1）。"""

    def test_both_specs_satisfy_protocol_surface(
        self, us_store: MinuteStore, cn_store: MinuteStore
    ) -> None:
        for spec in (us_store.market, cn_store.market):
            assert isinstance(spec, MarketSpec)
            assert isinstance(spec.calendar.session(), SessionSpec)
            assert spec.execution.lot_size >= 1
            assert spec.conventions.volume_multiplier >= 1
            assert callable(spec.index_symbol_rule)

    def test_execution_asymmetry_declared(
        self, us_store: MinuteStore, cn_store: MinuteStore
    ) -> None:
        assert us_store.market.execution.supports_live_adapter is True
        assert cn_store.market.execution.supports_live_adapter is False
        assert cn_store.market.execution.lot_size == 100
        assert cn_store.market.execution.min_holding_days == 1
        assert us_store.market.execution.min_holding_days == 0
