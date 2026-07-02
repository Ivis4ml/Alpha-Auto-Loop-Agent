"""成本敏感性分析：同一策略在成本倍数网格上的净收益曲线。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from alphaloop.core.backtest.costs import CostModel
from alphaloop.core.backtest.engine import BacktestReport, run_backtest
from alphaloop.market.protocols import MarketSpec

__all__ = ["CostSensitivityPoint", "cost_sensitivity"]

DEFAULT_MULTIPLIERS = (0.0, 0.5, 1.0, 2.0)


@dataclass(frozen=True, slots=True)
class CostSensitivityPoint:
    """单个成本倍数下的净口径结果。"""

    multiplier: float
    net_mean_daily: float
    net_sharpe_annualized: float


def cost_sensitivity(
    scores: pd.DataFrame,
    daily: pd.DataFrame,
    actions: pd.DataFrame,
    market: MarketSpec,
    base_costs: CostModel,
    *,
    top_k: int,
    multipliers: tuple[float, ...] = DEFAULT_MULTIPLIERS,
) -> tuple[list[CostSensitivityPoint], BacktestReport]:
    """在成本倍数网格上重跑回测，返回敏感性曲线与基准（1 倍）报告。"""
    if 1.0 not in multipliers:
        raise ValueError("倍数网格必须包含 1.0 基准点")
    points: list[CostSensitivityPoint] = []
    baseline: BacktestReport | None = None
    for multiplier in multipliers:
        report = run_backtest(
            scores, daily, actions, market, base_costs.scaled(multiplier), top_k=top_k
        )
        points.append(
            CostSensitivityPoint(
                multiplier=multiplier,
                net_mean_daily=report.net_mean_daily,
                net_sharpe_annualized=report.net_sharpe_annualized,
            )
        )
        if multiplier == 1.0:
            baseline = report
    assert baseline is not None
    return points, baseline
