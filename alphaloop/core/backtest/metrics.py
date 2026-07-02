"""因子评估指标：逐日横截面 IC 序列与 Fama-MacBeth 截面回归。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from alphaloop.core.stats.nw import NwResult, newey_west_mean_t

__all__ = ["forward_returns", "ic_series", "ic_significance", "fama_macbeth"]

_MIN_CROSS_SECTION = 5


def forward_returns(daily: pd.DataFrame, *, horizon: int = 1) -> pd.DataFrame:
    """由含 adj_factor 的日线面板计算前向 horizon 日收益，返回长表。

    输出列 [symbol, trade_date, fwd_return]，其中 fwd_return 是从
    trade_date 收盘到 horizon 个交易日后收盘的复权收益，供与同日因子
    值对齐（因子在 t 收盘可知，收益发生在 t 之后，方向正确）。
    """
    if horizon < 1:
        raise ValueError(f"horizon 必须为正: {horizon}")
    if "adj_factor" not in daily.columns:
        raise ValueError("面板缺少 adj_factor 列，先经公司行动模型处理")
    ordered = daily.sort_values(["symbol", "trade_date"]).copy()
    adj_close = ordered["close"] * ordered["adj_factor"]
    grouped = adj_close.groupby(ordered["symbol"].to_numpy(), sort=False)
    future = grouped.shift(-horizon)
    ordered["fwd_return"] = future / adj_close - 1.0
    if "suspect_gap" in ordered.columns:
        suspect_next = (
            ordered["suspect_gap"]
            .groupby(ordered["symbol"].to_numpy(), sort=False)
            .shift(-horizon)
            .astype("boolean")
            .fillna(False)
        )
        ordered.loc[suspect_next.to_numpy(), "fwd_return"] = np.nan
    return ordered[["symbol", "trade_date", "fwd_return"]]


def ic_series(scores: pd.DataFrame, forward: pd.DataFrame) -> pd.Series:
    """逐日 Spearman 秩相关 IC 序列。截面标的数不足的日期跳过。"""
    merged = scores.merge(forward, on=["symbol", "trade_date"], how="inner").dropna(
        subset=["score", "fwd_return"]
    )
    values: dict[object, float] = {}
    for day, block in merged.groupby("trade_date", sort=True):
        if len(block) < _MIN_CROSS_SECTION:
            continue
        rank_score = block["score"].rank()
        rank_return = block["fwd_return"].rank()
        correlation = rank_score.corr(rank_return)
        if not pd.isna(correlation):
            values[day] = float(correlation)
    return pd.Series(values, name="ic").sort_index()


def ic_significance(ic: pd.Series) -> NwResult:
    """IC 序列均值的自相关稳健检验。"""
    return newey_west_mean_t(ic.to_numpy())


def fama_macbeth(scores: pd.DataFrame, forward: pd.DataFrame) -> tuple[pd.Series, NwResult]:
    """逐日截面一元回归斜率序列及其自相关稳健检验。

    每日对 fwd_return 关于标准化后的 score 做一元最小二乘，斜率序列的
    均值检验用 Newey-West；返回 (斜率序列, 检验结果)。
    """
    merged = scores.merge(forward, on=["symbol", "trade_date"], how="inner").dropna(
        subset=["score", "fwd_return"]
    )
    slopes: dict[object, float] = {}
    for day, block in merged.groupby("trade_date", sort=True):
        if len(block) < _MIN_CROSS_SECTION:
            continue
        x = block["score"].to_numpy(dtype="float64")
        y = block["fwd_return"].to_numpy(dtype="float64")
        x_std = x.std()
        if x_std <= 0:
            continue
        x_normalized = (x - x.mean()) / x_std
        slope = float(np.dot(x_normalized, y - y.mean()) / np.dot(x_normalized, x_normalized))
        slopes[day] = slope
    series = pd.Series(slopes, name="fm_slope").sort_index()
    return series, newey_west_mean_t(series.to_numpy())
