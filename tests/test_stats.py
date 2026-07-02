"""统计检验集合的已知答案测试。"""

from __future__ import annotations

import numpy as np
import pytest

from alphaloop.core.stats import (
    bh_fdr,
    deflated_sharpe_probability,
    newey_west_mean_t,
    probability_of_backtest_overfitting,
)
from alphaloop.infra.seeds import derive_rng


class TestNeweyWest:
    def test_pure_noise_not_significant(self) -> None:
        rng = derive_rng(1, "nw-noise")
        rejections = 0
        for _trial in range(50):
            series = rng.normal(0.0, 1.0, 250)
            result = newey_west_mean_t(series)
            if result.p_value < 0.05:
                rejections += 1
        assert rejections <= 8

    def test_strong_signal_significant(self) -> None:
        rng = derive_rng(2, "nw-signal")
        series = rng.normal(0.5, 1.0, 250)
        result = newey_west_mean_t(series)
        assert result.p_value < 0.001
        assert result.t_stat > 3.0

    def test_autocorrelation_widens_error(self) -> None:
        """强正自相关序列的 NW 标准误应显著大于普通标准误。"""
        rng = derive_rng(3, "nw-ar")
        noise = rng.normal(0.0, 1.0, 500)
        ar = np.zeros(500)
        for index in range(1, 500):
            ar[index] = 0.8 * ar[index - 1] + noise[index]
        naive_t = ar.mean() / (ar.std(ddof=1) / np.sqrt(len(ar)))
        result = newey_west_mean_t(ar)
        assert abs(result.t_stat) < abs(naive_t)

    def test_too_few_observations_rejected(self) -> None:
        with pytest.raises(ValueError, match="观测不足"):
            newey_west_mean_t(np.array([0.1, 0.2, 0.3]))


class TestBhFdr:
    def test_known_example(self) -> None:
        p_values = np.array([0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205])
        significant = bh_fdr(p_values, q=0.05)
        assert significant[:2].all()
        assert not significant[5:].any()

    def test_all_null_rarely_rejects(self) -> None:
        rng = derive_rng(4, "fdr-null")
        false_hits = 0
        for _trial in range(100):
            p_values = rng.uniform(0.0, 1.0, 40)
            false_hits += int(bh_fdr(p_values, q=0.10).sum() > 0)
        assert false_hits <= 20

    def test_nan_treated_as_insignificant(self) -> None:
        significant = bh_fdr(np.array([0.0001, float("nan")]), q=0.05)
        assert bool(significant[0])
        assert not bool(significant[1])


class TestDeflatedSharpe:
    def test_more_trials_lower_probability(self) -> None:
        one = deflated_sharpe_probability(0.1, n_obs=500, n_trials=1)
        many = deflated_sharpe_probability(0.1, n_obs=500, n_trials=200)
        assert many < one

    def test_noise_best_of_many_not_credible(self) -> None:
        """纯噪声下 200 次尝试的最大 Sharpe，DSR 不应给出高可信度。"""
        rng = derive_rng(5, "dsr-noise")
        n_obs = 250
        best_sharpe = max(
            float(np.mean(series) / np.std(series, ddof=1))
            for series in rng.normal(0.0, 0.01, (200, n_obs))
        )
        probability = deflated_sharpe_probability(best_sharpe, n_obs=n_obs, n_trials=200)
        assert probability < 0.95

    def test_real_signal_survives_deflation(self) -> None:
        probability = deflated_sharpe_probability(0.3, n_obs=500, n_trials=100)
        assert probability > 0.99


class TestPbo:
    def test_noise_selection_is_overfit(self) -> None:
        rng = derive_rng(6, "pbo-noise")
        returns = rng.normal(0.0, 0.01, (240, 20))
        pbo = probability_of_backtest_overfitting(returns, n_splits=8)
        assert pbo > 0.3

    def test_true_skill_is_not_overfit(self) -> None:
        rng = derive_rng(7, "pbo-skill")
        returns = rng.normal(0.0, 0.01, (240, 20))
        returns[:, 3] += 0.01
        pbo = probability_of_backtest_overfitting(returns, n_splits=8)
        assert pbo < 0.1

    def test_input_validation(self) -> None:
        with pytest.raises(ValueError, match="偶数"):
            probability_of_backtest_overfitting(np.zeros((100, 5)), n_splits=3)
