"""回测过拟合概率（PBO，CSCV 法，Bailey 等 2015）。

输入为候选组合的逐期收益矩阵（行=时期，列=候选）。把时间轴切成 S 段，
枚举一半段做训练、另一半做测试的全部组合：每种组合下取训练段表现最好
的候选，看它在测试段的相对排名；排名落入后一半的比例即 PBO。
"""

from __future__ import annotations

from itertools import combinations

import numpy as np

__all__ = ["probability_of_backtest_overfitting"]


def probability_of_backtest_overfitting(returns: np.ndarray, *, n_splits: int = 8) -> float:
    """计算 PBO，取值 [0, 1]，越高说明样本内选优越可能是过拟合。"""
    matrix = np.asarray(returns, dtype="float64")
    if matrix.ndim != 2:
        raise ValueError("收益矩阵必须是二维（时期 × 候选）")
    n_periods, n_candidates = matrix.shape
    if n_candidates < 2:
        raise ValueError("至少需要两个候选才能评估选优过拟合")
    if n_splits < 2 or n_splits % 2 != 0:
        raise ValueError(f"分段数必须是不小于 2 的偶数: {n_splits}")
    if n_periods < n_splits * 2:
        raise ValueError(f"时期数 {n_periods} 相对分段数 {n_splits} 过少")
    segments = np.array_split(np.arange(n_periods), n_splits)
    below_median = 0
    total = 0
    for train_indices in combinations(range(n_splits), n_splits // 2):
        train_rows = np.concatenate([segments[index] for index in train_indices])
        test_rows = np.concatenate(
            [segments[index] for index in range(n_splits) if index not in train_indices]
        )
        train_sharpe = _sharpe_by_column(matrix[train_rows])
        test_sharpe = _sharpe_by_column(matrix[test_rows])
        best = int(np.nanargmax(train_sharpe))
        rank = float((test_sharpe < test_sharpe[best]).sum()) / (n_candidates - 1)
        if rank < 0.5:
            below_median += 1
        total += 1
    return below_median / total


def _sharpe_by_column(matrix: np.ndarray) -> np.ndarray:
    mean = matrix.mean(axis=0)
    std = matrix.std(axis=0, ddof=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(std > 0, mean / std, np.nan)
