"""日线组合回测引擎。

约定（显式声明，属于口径的一部分）：

- 信号在交易日 t 收盘生成，持仓于 t 收盘建立，赚取 t 到 t+1 的收盘间
  收益；这假设收盘时刻可按收盘价成交，属于研究口径的标准近似。
- 等权持有得分最高的 top_k 个可买标的；卖出受市场结算锚点与卖出
  可交易性约束，被约束的持仓顺延至最早允许日。
- 收益用复权后价格计算；无复权数据市场的除权嫌疑日持仓收益从样本
  中剔除并计数披露，绝不静默混入。
- 报告同时给出未扣成本与已扣成本两个口径，字段名显式区分。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from alphaloop.core.backtest.costs import CostModel
from alphaloop.core.stats.nw import NwResult, newey_west_mean_t
from alphaloop.market.protocols import MarketSpec

__all__ = ["BacktestReport", "run_backtest"]


@dataclass(frozen=True, slots=True)
class BacktestReport:
    """回测结果。gross 为未扣成本口径，net 为已扣成本口径。"""

    market_id: str
    n_days: int
    top_k: int
    gross_returns: pd.Series = field(repr=False)
    net_returns: pd.Series = field(repr=False)
    turnover: pd.Series = field(repr=False)
    gross_mean_daily: float
    net_mean_daily: float
    gross_sharpe_annualized: float
    net_sharpe_annualized: float
    net_newey_west: NwResult
    mean_turnover: float
    n_suspect_dropped: int
    cost_model: CostModel
    amount_is_estimated_anywhere: bool


def run_backtest(
    scores: pd.DataFrame,
    daily: pd.DataFrame,
    actions: pd.DataFrame,
    market: MarketSpec,
    costs: CostModel,
    *,
    top_k: int,
    periods_per_year: int = 252,
) -> BacktestReport:
    """执行日线组合回测。

    scores 为 [symbol, trade_date, score] 长表；daily 为规范日线面板；
    actions 为规范公司行动表。市场约束（可交易性、复权、除权嫌疑、
    结算锚点）经市场描述注入。
    """
    if top_k <= 0:
        raise ValueError(f"top_k 必须为正: {top_k}")
    prepared = market.tradability.attach_flags(daily)
    prepared = market.corporate_actions.adjust(prepared, actions)
    prepared = market.corporate_actions.flag_suspect_gaps(prepared)
    prepared = prepared[~prepared["is_index"]]

    adj_close = prepared.assign(adj=prepared["close"] * prepared["adj_factor"]).pivot_table(
        index="trade_date", columns="symbol", values="adj", aggfunc="first", observed=True
    )
    returns = adj_close / adj_close.shift(1) - 1.0
    symbols: list[str] = [str(name) for name in returns.columns]
    symbol_position = {name: index for index, name in enumerate(symbols)}
    date_position = {day: index for index, day in enumerate(returns.index)}
    returns_array = returns.to_numpy(dtype="float64")
    # 缺行（停牌、退市）按不可交易处理；除权嫌疑缺失按无嫌疑处理。
    buy_ok_array = _aligned_bool(prepared, "buy_ok", returns, default=False)
    sell_ok_array = _aligned_bool(prepared, "sell_ok", returns, default=False)
    suspect_array = _aligned_bool(prepared, "suspect_gap", returns, default=False)

    scores_by_day: dict[date, pd.Series] = {
        day: block.set_index("symbol")["score"].dropna().sort_values(ascending=False)
        for day, block in scores.groupby("trade_date", sort=True)
    }
    days: list[date] = [day for day in sorted(scores_by_day) if day in date_position]
    if len(days) < 3:
        raise ValueError("可回测交易日不足")

    holdings: dict[str, date] = {}
    gross_list: list[float] = []
    net_list: list[float] = []
    turnover_list: list[float] = []
    out_days: list[date] = []
    n_suspect_dropped = 0

    for position in range(len(days) - 1):
        today = days[position]
        next_day = days[position + 1]
        today_row = date_position[today]
        next_row = date_position[next_day]
        day_scores = scores_by_day[today]

        target = _select_targets(
            day_scores, buy_ok_array, today_row, symbol_position, top_k, set(holdings)
        )
        # 卖出判定：不在目标集合、已过结算锚点且当日允许卖出的持仓离场。
        kept: dict[str, date] = {}
        sells = 0
        for symbol, entry_day in holdings.items():
            must_keep = market.calendar.earliest_sell_day(entry_day) > today
            column = symbol_position.get(symbol)
            blocked = column is not None and not bool(sell_ok_array[today_row, column])
            if symbol in target or must_keep or blocked:
                kept[symbol] = entry_day
            else:
                sells += 1
        buys = 0
        for symbol in target:
            if symbol not in kept and len(kept) < top_k:
                kept[symbol] = today
                buys += 1
        holdings = kept
        if not holdings:
            gross_list.append(0.0)
            net_list.append(0.0)
            turnover_list.append(0.0)
            out_days.append(next_day)
            continue

        weight = 1.0 / max(len(holdings), top_k)
        day_gross = 0.0
        for symbol in holdings:
            column = symbol_position.get(symbol)
            if column is None:
                continue
            if suspect_array[next_row, column]:
                n_suspect_dropped += 1
                continue
            value = returns_array[next_row, column]
            if not np.isnan(value):
                day_gross += weight * value
        turnover_value = weight * (buys + sells)
        day_cost = weight * buys * costs.buy_rate + weight * sells * costs.sell_rate
        gross_list.append(day_gross)
        net_list.append(day_gross - day_cost)
        turnover_list.append(turnover_value)
        out_days.append(next_day)

    gross = pd.Series(gross_list, index=out_days, name="gross_return")
    net = pd.Series(net_list, index=out_days, name="net_return")
    turnover = pd.Series(turnover_list, index=out_days, name="turnover")
    return BacktestReport(
        market_id=market.market_id,
        n_days=len(out_days),
        top_k=top_k,
        gross_returns=gross,
        net_returns=net,
        turnover=turnover,
        gross_mean_daily=float(gross.mean()),
        net_mean_daily=float(net.mean()),
        gross_sharpe_annualized=_annualized_sharpe(gross, periods_per_year),
        net_sharpe_annualized=_annualized_sharpe(net, periods_per_year),
        net_newey_west=newey_west_mean_t(net.to_numpy()),
        mean_turnover=float(turnover.mean()),
        n_suspect_dropped=n_suspect_dropped,
        cost_model=costs,
        amount_is_estimated_anywhere=bool(daily["amount_is_estimated"].any()),
    )


def _select_targets(
    day_scores: pd.Series,
    buy_ok: np.ndarray,
    today_row: int,
    symbol_position: dict[str, int],
    top_k: int,
    current_holdings: set[str],
) -> list[str]:
    """按得分从高到低选出目标集合；新开仓要求当日可买，存量不受此限。"""
    target: list[str] = []
    for raw_symbol in day_scores.index:
        symbol = str(raw_symbol)
        if len(target) >= top_k:
            break
        if symbol in current_holdings:
            target.append(symbol)
            continue
        column = symbol_position.get(symbol)
        if column is not None and bool(buy_ok[today_row, column]):
            target.append(symbol)
    return target


def _aligned_bool(
    frame: pd.DataFrame, column: str, template: pd.DataFrame, *, default: bool
) -> np.ndarray:
    """把布尔标记列对齐到收益矩阵的 (日期 × 标的) 形状，缺失按 default 填。"""
    pivot = frame.pivot_table(
        index="trade_date", columns="symbol", values=column, aggfunc="first", observed=True
    )
    aligned = pivot.reindex(index=template.index, columns=template.columns)
    return aligned.astype("boolean").fillna(default).astype(bool).to_numpy()


def _annualized_sharpe(returns: pd.Series, periods_per_year: int) -> float:
    std = float(returns.std(ddof=1))
    if std <= 0 or np.isnan(std):
        return 0.0
    return float(returns.mean()) / std * float(np.sqrt(periods_per_year))
