"""回测引擎的已知答案测试：零 alpha 不显著、已知 alpha 恢复、成本单调、约束生效。"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from alphaloop.core.backtest import (
    CostModel,
    cost_model_from_execution,
    cost_sensitivity,
    run_backtest,
)
from alphaloop.core.backtest.capacity import estimate_capacity
from alphaloop.core.backtest.metrics import fama_macbeth, forward_returns, ic_series
from alphaloop.data.minute_store import MinuteStore
from alphaloop.infra.seeds import derive_rng
from alphaloop.market.cn_ashare import build_cn_ashare_spec
from alphaloop.market.us_equity import build_us_equity_spec

COSTS = CostModel(commission_bps=2.5, slippage_bps=5.0, stamp_tax_sell_bps=0.0)


def synthetic_market(n_symbols: int, n_days: int, *, alpha: float, seed: int) -> tuple:
    """构造合成日线面板与得分：alpha 控制得分对次日收益的真实预测力。"""
    rng = derive_rng(seed, "backtest-synthetic")
    start = date(2024, 1, 1)
    days = []
    day = start
    while len(days) < n_days:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    symbols = [f"S{index:02d}" for index in range(n_symbols)]
    future_returns = rng.normal(0.0, 0.02, (n_days, n_symbols))
    noise = rng.normal(0.0, 1.0, (n_days, n_symbols))
    rows = []
    score_rows = []
    prices = dict.fromkeys(symbols, 100.0)
    for day_index, current in enumerate(days):
        for symbol_index, symbol in enumerate(symbols):
            price = prices[symbol]
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": current,
                    "open": price,
                    "high": price * 1.01,
                    "low": price * 0.99,
                    "close": price,
                    "volume": 10_000,
                    "amount": price * 10_000.0,
                    "amount_is_estimated": False,
                    "is_index": False,
                }
            )
            if day_index < n_days - 1:
                next_return = future_returns[day_index + 1, symbol_index]
                score = alpha * next_return + (1.0 - alpha) * 0.02 * noise[day_index, symbol_index]
                score_rows.append(
                    {"symbol": symbol, "trade_date": current, "score": float(score)}
                )
                prices[symbol] = price * (1.0 + float(next_return))
    daily = pd.DataFrame(rows)
    scores = pd.DataFrame(score_rows)
    spec = build_us_equity_spec(days)
    actions = pd.DataFrame(columns=["symbol", "ex_date", "split_ratio", "cash_div"])
    return scores, daily, actions, spec


class TestHonestBaseline:
    def test_zero_alpha_never_significantly_positive(self) -> None:
        """零 alpha 下净收益不得显著为正；因成本拖累显著为负是正确结果。"""
        scores, daily, actions, spec = synthetic_market(20, 200, alpha=0.0, seed=11)
        report = run_backtest(scores, daily, actions, spec, COSTS, top_k=5)
        assert report.net_newey_west.t_stat < 2.0
        assert report.net_mean_daily < report.gross_mean_daily
        gross_only = run_backtest(scores, daily, actions, spec, COSTS.scaled(0.0), top_k=5)
        assert abs(gross_only.net_newey_west.t_stat) < 2.5

    def test_planted_alpha_recovered(self) -> None:
        scores, daily, actions, spec = synthetic_market(20, 200, alpha=0.9, seed=12)
        report = run_backtest(scores, daily, actions, spec, COSTS, top_k=5)
        assert report.net_newey_west.p_value < 0.001
        assert report.net_mean_daily > 0.005

    def test_gross_net_separation(self) -> None:
        scores, daily, actions, spec = synthetic_market(20, 100, alpha=0.5, seed=13)
        report = run_backtest(scores, daily, actions, spec, COSTS, top_k=5)
        assert report.net_mean_daily < report.gross_mean_daily
        assert report.mean_turnover > 0


class TestCostSensitivity:
    def test_net_return_monotone_in_costs(self) -> None:
        scores, daily, actions, spec = synthetic_market(20, 120, alpha=0.5, seed=14)
        points, baseline = cost_sensitivity(
            scores, daily, actions, spec, COSTS, top_k=5
        )
        means = [point.net_mean_daily for point in points]
        assert means == sorted(means, reverse=True)
        assert baseline.cost_model.commission_bps == COSTS.commission_bps

    def test_zero_cost_equals_gross(self) -> None:
        scores, daily, actions, spec = synthetic_market(10, 60, alpha=0.5, seed=15)
        report = run_backtest(scores, daily, actions, spec, COSTS.scaled(0.0), top_k=3)
        assert report.net_mean_daily == pytest.approx(report.gross_mean_daily)


class TestIcMetrics:
    def test_ic_positive_for_planted_alpha(self) -> None:
        scores, daily, actions, spec = synthetic_market(20, 150, alpha=0.9, seed=16)
        adjusted = spec.corporate_actions.adjust(daily, actions)
        forward = forward_returns(adjusted)
        ic = ic_series(scores, forward)
        assert ic.mean() > 0.3

    def test_ic_near_zero_without_alpha(self) -> None:
        scores, daily, actions, spec = synthetic_market(20, 150, alpha=0.0, seed=17)
        adjusted = spec.corporate_actions.adjust(daily, actions)
        forward = forward_returns(adjusted)
        ic = ic_series(scores, forward)
        assert abs(ic.mean()) < 0.1

    def test_fama_macbeth_recovers_slope_sign(self) -> None:
        scores, daily, actions, spec = synthetic_market(20, 150, alpha=0.9, seed=18)
        adjusted = spec.corporate_actions.adjust(daily, actions)
        slopes, result = fama_macbeth(scores, forward_returns(adjusted))
        assert result.mean > 0
        assert result.p_value < 0.01


class TestMarketConstraints:
    def test_cn_stamp_tax_reduces_net(self) -> None:
        """同一合成面板下，含印花税市场的净收益应低于零印花税市场。"""
        scores, daily, actions, _ = synthetic_market(20, 120, alpha=0.5, seed=19)
        days = sorted(daily["trade_date"].unique())
        cn_spec = build_cn_ashare_spec(days)
        us_spec = build_us_equity_spec(days)
        cn_costs = cost_model_from_execution(cn_spec.execution)
        us_costs = cost_model_from_execution(us_spec.execution)
        assert cn_costs.stamp_tax_sell_bps == 5.0
        assert us_costs.stamp_tax_sell_bps == 0.0
        cn_report = run_backtest(scores, daily, actions, cn_spec, cn_costs, top_k=5)
        us_report = run_backtest(scores, daily, actions, us_spec, us_costs, top_k=5)
        assert cn_report.net_mean_daily < us_report.net_mean_daily
        assert cn_report.gross_mean_daily == pytest.approx(us_report.gross_mean_daily)

    def test_suspect_gap_days_dropped_and_counted(self) -> None:
        scores, daily, actions, _ = synthetic_market(10, 60, alpha=0.5, seed=20)
        days = sorted(daily["trade_date"].unique())
        cn_spec = build_cn_ashare_spec(days)
        target_day = days[30]
        mask = (daily["symbol"] == "S00") & (daily["trade_date"] >= target_day)
        daily.loc[mask, ["open", "high", "low", "close"]] *= 0.5
        report = run_backtest(scores, daily, actions, cn_spec, COSTS, top_k=10)
        assert report.n_suspect_dropped >= 1

    def test_fixture_backtest_runs_on_both_markets(
        self, us_store: MinuteStore, cn_store: MinuteStore
    ) -> None:
        """金样本夹具上的端到端冒烟：双市场同一路径可回测。"""
        for store in (us_store, cn_store):
            listing = store.read_listing()
            symbols = listing[~listing["is_index"]]["symbol"].tolist()
            daily = store.read_daily(symbols, date(2024, 1, 2), date(2024, 6, 28))
            actions = store.read_corp_actions()
            momentum = daily.sort_values(["symbol", "trade_date"]).copy()
            momentum["score"] = momentum.groupby("symbol")["close"].pct_change(5)
            scores = momentum[["symbol", "trade_date", "score"]].dropna()
            report = run_backtest(
                scores, daily, actions, store.market,
                cost_model_from_execution(store.market.execution), top_k=3,
            )
            assert report.n_days > 80
            assert report.market_id == store.market.market_id


class TestCapacity:
    def test_binding_symbol_identified(self) -> None:
        adv = pd.Series({"A": 1e8, "B": 1e6})
        estimate = estimate_capacity(
            {"A": 0.5, "B": 0.5}, adv, participation_cap=0.02, amount_is_estimated=True
        )
        assert estimate.binding_symbol == "B"
        assert estimate.capacity_notional == pytest.approx(0.02 * 1e6 / 0.5)
        assert estimate.amount_is_estimated is True

    def test_missing_adv_rejected(self) -> None:
        adv = pd.Series({"A": 1e8})
        with pytest.raises(ValueError, match="缺少日均成交额"):
            estimate_capacity({"A": 0.5, "B": 0.5}, adv, amount_is_estimated=False)
