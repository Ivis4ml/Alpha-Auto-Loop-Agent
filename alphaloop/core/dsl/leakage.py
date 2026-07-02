"""动态前视检测：截断面板重算，比较历史值是否随未来数据的到来而改变。

这是泄漏防护的第三层（第一层是白名单本身不含前向算子，第二层是求值
实现只使用截至当前行的窗口）。每个进入研究循环的候选都必须通过本测试。
"""

from __future__ import annotations

import pandas as pd

from alphaloop.core.dsl.evaluate import evaluate, time_column
from alphaloop.core.dsl.grammar import FactorSpec

__all__ = ["no_lookahead_test"]

_TOLERANCE = 1e-9


def no_lookahead_test(spec: FactorSpec, panel: pd.DataFrame, *, n_cuts: int = 3) -> bool:
    """在若干截断点上验证因子无前视。

    做法：取时间轴上的 n_cuts 个截断点，把面板截断到该时点重算因子，
    与全量面板求值结果在截断点之前的取值逐一比对；任何一处差异超过
    容忍度即判失败。空值与空值视为一致。
    """
    column = time_column(panel)
    full = evaluate(spec, panel)
    times = sorted(pd.unique(panel[column]))
    if len(times) < 3:
        raise ValueError("面板时间点过少，无法做截断检测")
    cut_positions = [len(times) * (index + 1) // (n_cuts + 1) for index in range(n_cuts)]
    for position in cut_positions:
        cutoff = times[max(1, position)]
        mask = panel[column] <= cutoff
        truncated_panel = panel.loc[mask]
        truncated = evaluate(spec, truncated_panel)
        reference = full.loc[mask]
        both_nan = truncated.isna() & reference.isna()
        difference = (truncated - reference).abs() > _TOLERANCE
        if bool((difference & ~both_nan).any()) or bool(
            (truncated.isna() ^ reference.isna()).any()
        ):
            return False
    return True
