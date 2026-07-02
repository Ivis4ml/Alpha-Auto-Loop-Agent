"""Deflated Sharpe Ratio（Bailey 与 López de Prado, 2014）。

在做过 n_trials 次尝试之后观察到的最大 Sharpe 需要与"纯噪声下 n 次
尝试的期望最大值"对比：DSR 给出观察 Sharpe 超过该噪声基准的概率。
n_trials 必须取全量试验计数（任务书 3.1 第 4 条），由治理层的试验
账本提供，不允许调用方自行缩小。
"""

from __future__ import annotations

import math

__all__ = ["deflated_sharpe_probability", "expected_max_sharpe"]

_EULER_MASCHERONI = 0.5772156649015329


def expected_max_sharpe(n_trials: int, variance_sharpe: float) -> float:
    """纯噪声下 n_trials 次尝试的期望最大 Sharpe（非年化，逐期口径）。"""
    if n_trials < 1:
        raise ValueError(f"试验次数必须为正: {n_trials}")
    if variance_sharpe <= 0:
        raise ValueError(f"Sharpe 估计方差必须为正: {variance_sharpe}")
    if n_trials == 1:
        return 0.0
    z1 = _norm_ppf(1.0 - 1.0 / n_trials)
    z2 = _norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(variance_sharpe) * ((1.0 - _EULER_MASCHERONI) * z1 + _EULER_MASCHERONI * z2)


def deflated_sharpe_probability(
    observed_sharpe: float,
    *,
    n_obs: int,
    n_trials: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    variance_sharpe: float | None = None,
) -> float:
    """返回 DSR：观测 Sharpe 超越噪声期望最大值的概率，越接近 1 越可信。

    observed_sharpe 为逐期（非年化）Sharpe；n_obs 为收益观测数；
    skewness 与 kurtosis 为收益分布的偏度与峰度（正态为 0 与 3）。
    variance_sharpe 缺省按 1/n_obs 近似（多次尝试的 Sharpe 估计方差）。
    """
    if n_obs < 8:
        raise ValueError(f"观测数不足（{n_obs} < 8），拒绝出具 DSR")
    if variance_sharpe is None:
        variance_sharpe = 1.0 / n_obs
    benchmark = expected_max_sharpe(n_trials, variance_sharpe)
    numerator = (observed_sharpe - benchmark) * math.sqrt(n_obs - 1.0)
    denominator = math.sqrt(
        1.0
        - skewness * observed_sharpe
        + (kurtosis - 1.0) / 4.0 * observed_sharpe**2
    )
    if denominator <= 0:
        raise ValueError("收益高阶矩导致方差项非正，拒绝出具 DSR")
    return _norm_cdf(numerator / denominator)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """标准正态分位数（Acklam 有理逼近，精度约 1e-9，足够本用途）。"""
    if not 0.0 < p < 1.0:
        raise ValueError(f"分位点必须在 (0, 1) 内: {p}")
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    p_low = 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    if p <= 1.0 - p_low:
        q = p - 0.5
        r = q * q
        return (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
            * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
        )
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
    )
