"""Newey-West 自相关稳健的均值 t 检验。

用于 IC 序列与逐期截面回归斜率序列：这类序列普遍存在自相关，普通
t 检验会高估显著性。带宽默认按 Newey-West (1994) 的经验规则取
floor(4 * (n/100)^(2/9))。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = ["NwResult", "newey_west_mean_t"]


@dataclass(frozen=True, slots=True)
class NwResult:
    """均值检验结果。p 值为双侧正态近似。"""

    mean: float
    t_stat: float
    p_value: float
    n_obs: int
    lag: int


def newey_west_mean_t(values: np.ndarray, *, lag: int | None = None) -> NwResult:
    """对序列均值做 HAC（Bartlett 核）稳健 t 检验。

    输入中的空值被剔除；有效观测不足 8 个时报错，因为这种样本量下的
    结论没有意义。
    """
    array = np.asarray(values, dtype="float64")
    array = array[~np.isnan(array)]
    n = len(array)
    if n < 8:
        raise ValueError(f"有效观测不足（{n} < 8），拒绝出具检验结果")
    if lag is None:
        lag = int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0)))
    lag = max(0, min(lag, n - 2))
    mean = float(array.mean())
    demeaned = array - mean
    variance = float(np.dot(demeaned, demeaned)) / n
    for j in range(1, lag + 1):
        weight = 1.0 - j / (lag + 1.0)
        gamma = float(np.dot(demeaned[j:], demeaned[:-j])) / n
        variance += 2.0 * weight * gamma
    if variance <= 0:
        raise ValueError("长期方差非正，序列退化，拒绝出具检验结果")
    standard_error = math.sqrt(variance / n)
    t_stat = mean / standard_error
    p_value = 2.0 * (1.0 - _norm_cdf(abs(t_stat)))
    return NwResult(mean=mean, t_stat=t_stat, p_value=p_value, n_obs=n, lag=lag)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
