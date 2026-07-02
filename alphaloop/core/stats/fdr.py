"""Benjamini-Hochberg 假发现率（FDR）多重检验修正。"""

from __future__ import annotations

import numpy as np

__all__ = ["bh_fdr"]


def bh_fdr(p_values: np.ndarray, *, q: float = 0.10) -> np.ndarray:
    """返回与输入等长的布尔数组：在假发现率 q 下判为显著的检验。

    p 值中的空值视为 1.0（不显著）。q 必须在 (0, 1) 内。
    """
    if not 0.0 < q < 1.0:
        raise ValueError(f"q 必须在 (0, 1) 内: {q}")
    array = np.asarray(p_values, dtype="float64").copy()
    array[np.isnan(array)] = 1.0
    if ((array < 0.0) | (array > 1.0)).any():
        raise ValueError("p 值必须在 [0, 1] 内")
    n = len(array)
    if n == 0:
        return np.zeros(0, dtype=bool)
    order = np.argsort(array)
    ranked = array[order]
    thresholds = q * (np.arange(1, n + 1) / n)
    passing = np.nonzero(ranked <= thresholds)[0]
    result = np.zeros(n, dtype=bool)
    if len(passing) > 0:
        cutoff = passing.max()
        result[order[: cutoff + 1]] = True
    return result
