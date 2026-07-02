"""候选生成器：模板搜索与随机搜索两个无语言模型基线。

两者同时是功效对照实验的基线臂（任务书 3.1 第 6 条）；治理门控在
基线上先行跑通之后，语言模型生成器才作为第三个实现接入（任务书
陷阱 10 的顺序要求）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from alphaloop.core.dsl.grammar import FactorSpec
from alphaloop.core.dsl.lint import LintError, lint_spec
from alphaloop.infra.seeds import derive_rng

__all__ = [
    "ResearchContext",
    "CandidateGenerator",
    "TemplateSearchGenerator",
    "RandomSearchGenerator",
]


@dataclass(frozen=True, slots=True)
class ResearchContext:
    """生成器可见的全部信息。

    feedback 为反馈策略给出的结构化摘要：Mode A 恒为 None（生成端
    看不到任何验证统计），Mode B 为离散桶字典。
    """

    field_scope: tuple[str, ...]
    root_seed: int
    feedback: dict[str, Any] | None = None


class CandidateGenerator(Protocol):
    """候选生成器协议。"""

    generator_id: str

    def propose(self, context: ResearchContext, budget: int) -> list[FactorSpec]:
        """产出不超过 budget 个候选规格。"""
        ...


class TemplateSearchGenerator:
    """模板网格：对若干经济动机模板按字段与窗口做确定性枚举。"""

    generator_id = "template_v1"

    _WINDOWS = (2, 5, 10, 20)

    def propose(self, context: ResearchContext, budget: int) -> list[FactorSpec]:
        specs: list[FactorSpec] = []
        fields = [name for name in ("close", "volume", "amount") if name in context.field_scope]
        for window in self._WINDOWS:
            for field_name in fields:
                for tree in self._templates(field_name, window):
                    try:
                        lint_spec(tree, field_scope=frozenset(context.field_scope))
                    except LintError:
                        continue
                    specs.append(FactorSpec(tree))
                    if len(specs) >= budget:
                        return specs
        return specs

    @staticmethod
    def _templates(field_name: str, window: int) -> list[dict[str, Any]]:
        delta = {"op": "ts_delta", "args": [{"field": field_name}], "params": {"window": window}}
        mean = {"op": "ts_mean", "args": [{"field": field_name}], "params": {"window": window}}
        std = {"op": "ts_std", "args": [{"field": field_name}], "params": {"window": window}}
        return [
            {"op": "cs_rank", "args": [delta]},
            {"op": "neg", "args": [{"op": "cs_rank", "args": [delta]}]},
            {"op": "cs_zscore", "args": [std]},
            {"op": "cs_rank", "args": [{"op": "div_safe", "args": [{"field": field_name}, mean]}]},
        ]


class RandomSearchGenerator:
    """随机树搜索：从语法随机采样规格，静态不合法即重试。"""

    generator_id = "random_v1"

    _UNARY = ("neg", "abs", "sign", "log_safe")
    _BINARY = ("add", "sub", "mul", "div_safe")
    _TS = ("ts_delay", "ts_delta", "ts_mean", "ts_std", "ts_min", "ts_max", "ts_rank")
    _CS = ("cs_rank", "cs_zscore", "cs_demean")
    _WINDOWS = (2, 3, 5, 10, 20, 60)

    def propose(self, context: ResearchContext, budget: int) -> list[FactorSpec]:
        rng = derive_rng(context.root_seed, f"generator:{self.generator_id}")
        scope = frozenset(context.field_scope)
        specs: list[FactorSpec] = []
        seen: set[str] = set()
        attempts = 0
        while len(specs) < budget and attempts < budget * 50:
            attempts += 1
            tree = {"op": str(rng.choice(self._CS)), "args": [self._subtree(rng, scope, depth=2)]}
            try:
                lint_spec(tree, field_scope=scope)
            except LintError:
                continue
            spec = FactorSpec(tree)
            if spec.factor_id() in seen:
                continue
            seen.add(spec.factor_id())
            specs.append(spec)
        return specs

    def _subtree(self, rng: Any, scope: frozenset[str], *, depth: int) -> dict[str, Any]:  # noqa: ANN401
        fields = sorted(scope)
        if depth <= 0 or rng.random() < 0.3:
            return {"field": str(rng.choice(fields))}
        kind = rng.random()
        if kind < 0.5:
            return {
                "op": str(rng.choice(self._TS)),
                "args": [self._subtree(rng, scope, depth=depth - 1)],
                "params": {"window": int(rng.choice(self._WINDOWS))},
            }
        if kind < 0.75:
            return {
                "op": str(rng.choice(self._BINARY)),
                "args": [
                    self._subtree(rng, scope, depth=depth - 1),
                    self._subtree(rng, scope, depth=depth - 1),
                ],
            }
        return {
            "op": str(rng.choice(self._UNARY)),
            "args": [self._subtree(rng, scope, depth=depth - 1)],
        }
