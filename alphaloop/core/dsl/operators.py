"""算子白名单。

每个算子声明类别、参数约束、回看行数与纯函数实现。实现约定：

- 时序类算子逐标的分组计算，窗口只覆盖截至当前行的历史（pandas 滚动
  窗口的默认右端语义），任何前向索引都不存在于本白名单；
- 截面类算子逐时间戳分组计算，只使用同一时刻的截面；
- 数值奇点（除零、负数取对数）一律产出空值，不抛异常也不产出无穷。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

__all__ = ["ParamSpec", "OperatorDef", "OPERATORS"]

_EPS = 1e-12
_MAX_WINDOW = 250


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """单个算子参数的取值约束（闭区间整数）。"""

    minimum: int
    maximum: int


def _zero_lookback(params: Mapping[str, int]) -> int:
    return 0


@dataclass(frozen=True, slots=True)
class OperatorDef:
    """算子定义。impl 的入参为按 args 顺序排列的子结果序列与参数字典。"""

    name: str
    category: Literal["unary", "binary", "ts", "ts2", "cs"]
    arity: int
    impl: Callable[..., pd.Series]
    params: Mapping[str, ParamSpec] = field(default_factory=dict)
    lookback: Callable[[Mapping[str, int]], int] = _zero_lookback


def _grouped_ts(series: pd.Series, group: pd.Series) -> Any:  # noqa: ANN401
    return series.groupby(group.to_numpy(), sort=False)


# ---------------------------------------------------------------------------
# 一元与二元
# ---------------------------------------------------------------------------


def _neg(x: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series) -> pd.Series:
    return -x


def _abs(x: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series) -> pd.Series:
    return x.abs()


def _sign(x: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series) -> pd.Series:
    return np.sign(x)


def _log_safe(
    x: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    return np.sign(x) * np.log1p(x.abs())


def _add(
    x: pd.Series, y: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    return x + y


def _sub(
    x: pd.Series, y: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    return x - y


def _mul(
    x: pd.Series, y: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    return x * y


def _div_safe(
    x: pd.Series, y: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    denominator = y.where(y.abs() > _EPS)
    return x / denominator


# ---------------------------------------------------------------------------
# 时序类（逐标的，窗口只含截至当前行的历史）
# ---------------------------------------------------------------------------


def _ts_delay(
    x: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    return _grouped_ts(x, symbol).shift(params["window"])


def _ts_delta(
    x: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    return x - _grouped_ts(x, symbol).shift(params["window"])


def _ts_mean(
    x: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    window = params["window"]
    return _grouped_ts(x, symbol).transform(
        lambda s: s.rolling(window, min_periods=window).mean()
    )


def _ts_std(
    x: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    window = params["window"]
    return _grouped_ts(x, symbol).transform(
        lambda s: s.rolling(window, min_periods=window).std()
    )


def _ts_min(
    x: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    window = params["window"]
    return _grouped_ts(x, symbol).transform(
        lambda s: s.rolling(window, min_periods=window).min()
    )


def _ts_max(
    x: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    window = params["window"]
    return _grouped_ts(x, symbol).transform(
        lambda s: s.rolling(window, min_periods=window).max()
    )


def _ts_rank(
    x: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    window = params["window"]

    def _rank_last(values: np.ndarray) -> float:
        last = values[-1]
        if np.isnan(last):
            return float("nan")
        return float((values <= last).sum()) / len(values)

    return _grouped_ts(x, symbol).transform(
        lambda s: s.rolling(window, min_periods=window).apply(_rank_last, raw=True)
    )


def _decay_linear(
    x: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    window = params["window"]
    weights = np.arange(1, window + 1, dtype="float64")
    weights /= weights.sum()

    def _weighted(values: np.ndarray) -> float:
        return float(np.dot(values, weights))

    return _grouped_ts(x, symbol).transform(
        lambda s: s.rolling(window, min_periods=window).apply(_weighted, raw=True)
    )


def _ts_corr(
    x: pd.Series, y: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    window = params["window"]
    frame = pd.DataFrame({"x": x, "y": y, "g": symbol.to_numpy()})
    result = frame.groupby("g", sort=False).apply(
        lambda block: block["x"].rolling(window, min_periods=window).corr(block["y"]),
        include_groups=False,
    )
    return result.reset_index(level=0, drop=True).reindex(x.index)


# ---------------------------------------------------------------------------
# 截面类（逐时间戳）
# ---------------------------------------------------------------------------


def _cs_rank(
    x: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    return x.groupby(ts.to_numpy(), sort=False).rank(pct=True)


def _cs_zscore(
    x: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    grouped = x.groupby(ts.to_numpy(), sort=False)
    mean = grouped.transform("mean")
    std = grouped.transform("std")
    return (x - mean) / std.where(std > _EPS)


def _cs_demean(
    x: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    return x - x.groupby(ts.to_numpy(), sort=False).transform("mean")


def _cs_winsor(
    x: pd.Series, params: Mapping[str, int], symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    sigmas = float(params["sigmas"])
    grouped = x.groupby(ts.to_numpy(), sort=False)
    mean = grouped.transform("mean")
    std = grouped.transform("std")
    lower = mean - sigmas * std
    upper = mean + sigmas * std
    return x.clip(lower=lower, upper=upper)


_WINDOW = {"window": ParamSpec(2, _MAX_WINDOW)}
_DELAY_WINDOW = {"window": ParamSpec(1, _MAX_WINDOW)}


def _window_lookback(params: Mapping[str, int]) -> int:
    return int(params["window"])


OPERATORS: dict[str, OperatorDef] = {
    definition.name: definition
    for definition in (
        OperatorDef("neg", "unary", 1, _neg),
        OperatorDef("abs", "unary", 1, _abs),
        OperatorDef("sign", "unary", 1, _sign),
        OperatorDef("log_safe", "unary", 1, _log_safe),
        OperatorDef("add", "binary", 2, _add),
        OperatorDef("sub", "binary", 2, _sub),
        OperatorDef("mul", "binary", 2, _mul),
        OperatorDef("div_safe", "binary", 2, _div_safe),
        OperatorDef("ts_delay", "ts", 1, _ts_delay, _DELAY_WINDOW, _window_lookback),
        OperatorDef("ts_delta", "ts", 1, _ts_delta, _DELAY_WINDOW, _window_lookback),
        OperatorDef("ts_mean", "ts", 1, _ts_mean, _WINDOW, _window_lookback),
        OperatorDef("ts_std", "ts", 1, _ts_std, _WINDOW, _window_lookback),
        OperatorDef("ts_min", "ts", 1, _ts_min, _WINDOW, _window_lookback),
        OperatorDef("ts_max", "ts", 1, _ts_max, _WINDOW, _window_lookback),
        OperatorDef("ts_rank", "ts", 1, _ts_rank, _WINDOW, _window_lookback),
        OperatorDef("decay_linear", "ts", 1, _decay_linear, _WINDOW, _window_lookback),
        OperatorDef("ts_corr", "ts2", 2, _ts_corr, _WINDOW, _window_lookback),
        OperatorDef("cs_rank", "cs", 1, _cs_rank),
        OperatorDef("cs_zscore", "cs", 1, _cs_zscore),
        OperatorDef("cs_demean", "cs", 1, _cs_demean),
        OperatorDef("cs_winsor", "cs", 1, _cs_winsor, {"sigmas": ParamSpec(1, 10)}),
    )
}
