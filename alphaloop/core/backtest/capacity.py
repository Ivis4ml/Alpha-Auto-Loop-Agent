"""容量估计：给定日均成交额与参与率上限，反推策略可容纳的名义规模。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

__all__ = ["CapacityEstimate", "estimate_capacity"]


@dataclass(frozen=True, slots=True)
class CapacityEstimate:
    """容量估计结果。amount_is_estimated 为 True 时绝对数值仅供量级参考。"""

    capacity_notional: float
    binding_symbol: str
    participation_cap: float
    amount_is_estimated: bool


def estimate_capacity(
    weights: dict[str, float],
    adv_amount: pd.Series,
    *,
    participation_cap: float = 0.02,
    amount_is_estimated: bool,
) -> CapacityEstimate:
    """按最紧标的反推容量：capacity = min_i (participation_cap * ADV_i / w_i)。

    adv_amount 以标的为索引、值为日均成交额（当地货币）。权重为组合
    占比；缺少成交额数据的标的直接使产生错误而不是被静默跳过。
    """
    if not weights:
        raise ValueError("权重为空，无法估计容量")
    if not 0.0 < participation_cap <= 1.0:
        raise ValueError(f"参与率上限必须在 (0, 1] 内: {participation_cap}")
    binding_symbol = ""
    capacity = float("inf")
    for symbol, weight in weights.items():
        if weight <= 0:
            raise ValueError(f"权重必须为正: {symbol}={weight}")
        if symbol not in adv_amount.index or pd.isna(adv_amount[symbol]):
            raise ValueError(f"标的 {symbol} 缺少日均成交额数据")
        symbol_capacity = participation_cap * float(adv_amount[symbol]) / weight
        if symbol_capacity < capacity:
            capacity = symbol_capacity
            binding_symbol = symbol
    return CapacityEstimate(
        capacity_notional=capacity,
        binding_symbol=binding_symbol,
        participation_cap=participation_cap,
        amount_is_estimated=amount_is_estimated,
    )
