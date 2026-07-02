"""确定性规则引擎：候选进入评估之前的静态与动态筛查。

未来函数检查、字段合法性、参数边界这类关键判定一律由本模块的确定性
规则完成，不交给任何可能被诱导的生成模型（任务书 3.1 附加条款二）。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from alphaloop.core.dsl.grammar import FactorSpec
from alphaloop.core.dsl.leakage import no_lookahead_test
from alphaloop.core.dsl.lint import LintError, lint_spec

__all__ = ["ScreenResult", "static_screen"]


@dataclass(frozen=True, slots=True)
class ScreenResult:
    """筛查结论。"""

    accepted: list[FactorSpec]
    rejected: list[tuple[str, str]]


def static_screen(
    specs: list[FactorSpec],
    *,
    field_scope: frozenset[str],
    probe_panel: pd.DataFrame,
    max_lookback: int,
) -> ScreenResult:
    """依次执行：语法与边界校验、factor_id 去重、回看深度上限、动态前视检测。

    probe_panel 是用于动态检测的小面板（训练段子集即可）；被拒绝的
    候选以 (factor_id, 理由码) 返回，调用方负责登记 realized 结果。
    """
    accepted: list[FactorSpec] = []
    rejected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for spec in specs:
        factor_id = spec.factor_id()
        try:
            lint_spec(spec.tree, field_scope=field_scope)
        except LintError as error:
            rejected.append((factor_id, f"lint:{error}"))
            continue
        if factor_id in seen:
            rejected.append((factor_id, "duplicate"))
            continue
        seen.add(factor_id)
        if spec.total_lookback() > max_lookback:
            rejected.append((factor_id, f"lookback_exceeds:{spec.total_lookback()}>{max_lookback}"))
            continue
        if not no_lookahead_test(spec, probe_panel):
            rejected.append((factor_id, "lookahead_detected"))
            continue
        accepted.append(spec)
    return ScreenResult(accepted=accepted, rejected=rejected)
