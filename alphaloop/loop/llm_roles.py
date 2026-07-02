"""语言模型驱动的候选生成器与评审器。

生成与评审是两个独立角色（不同系统提示词，可配不同模型）；评审只
输出结构化结论、无权改写候选。语义合法性与泄漏防护不依赖两者，
一律由确定性规则引擎兜底。
"""

from __future__ import annotations

from typing import Protocol

from alphaloop.core.dsl.grammar import FactorSpec
from alphaloop.core.dsl.operators import OPERATORS
from alphaloop.llm.client import LlmClient
from alphaloop.llm.parse import extract_json_objects
from alphaloop.llm.roles import PROMPT_VERSION, generator_prompts, reviewer_prompts
from alphaloop.loop.generators import ResearchContext

__all__ = ["LlmGenerator", "CandidateReviewer", "LlmReviewer"]


class LlmGenerator:
    """语言模型候选生成器。

    解析后的每棵规格树都是一个正式候选（包括随后被规则引擎拒绝的），
    调用方负责全量登记试验。
    """

    generator_id = "llm_v1"

    def __init__(
        self, client: LlmClient, model: str, *, prompt_version: str = PROMPT_VERSION
    ) -> None:
        self.client = client
        self.model = model
        self.prompt_version = prompt_version

    def propose(self, context: ResearchContext, budget: int) -> list[FactorSpec]:
        system, prompt = generator_prompts(
            field_scope=context.field_scope,
            operator_names=tuple(OPERATORS),
            budget=budget,
            feedback=context.feedback,
        )
        text = self.client.complete(model=self.model, system=system, prompt=prompt)
        specs: list[FactorSpec] = []
        seen: set[str] = set()
        for item in extract_json_objects(text):
            tree = item.get("spec") if isinstance(item.get("spec"), dict) else item
            if not isinstance(tree, dict):
                continue
            spec = FactorSpec(tree)
            try:
                factor_id = spec.factor_id()
            except (TypeError, ValueError):
                continue
            if factor_id in seen:
                continue
            seen.add(factor_id)
            specs.append(spec)
            if len(specs) >= budget:
                break
        return specs


class CandidateReviewer(Protocol):
    """候选评审协议：输入规格，输出 factor_id 到 (是否通过, 理由码) 的映射。"""

    reviewer_id: str

    def review(self, specs: list[FactorSpec]) -> dict[str, tuple[bool, str]]:
        """评审候选。缺失结论按通过处理并标注理由码，硬性防护在规则引擎。"""
        ...


class LlmReviewer:
    """语言模型评审器：只看到规格树，看不到任何验证统计。"""

    reviewer_id = "llm_reviewer_v1"

    def __init__(self, client: LlmClient, model: str) -> None:
        self.client = client
        self.model = model

    def review(self, specs: list[FactorSpec]) -> dict[str, tuple[bool, str]]:
        if not specs:
            return {}
        candidates = [{"factor_id": spec.factor_id(), "spec": spec.tree} for spec in specs]
        system, prompt = reviewer_prompts(candidates)
        text = self.client.complete(model=self.model, system=system, prompt=prompt)
        verdicts: dict[str, tuple[bool, str]] = {}
        for item in extract_json_objects(text):
            factor_id = item.get("factor_id")
            if not isinstance(factor_id, str):
                continue
            verdicts[factor_id] = (
                bool(item.get("approve", False)),
                str(item.get("reason_code", "unspecified")),
            )
        for spec in specs:
            verdicts.setdefault(spec.factor_id(), (True, "no_verdict"))
        return verdicts
