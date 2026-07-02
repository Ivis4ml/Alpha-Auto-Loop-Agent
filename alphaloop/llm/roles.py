"""生成与评审两个角色的提示词构造。

角色分离是任务书 3.1 附加条款一的要求：候选生成与候选评审使用不同的
系统提示词（可配不同模型），评审只输出结构化结论、无权改写候选，
避免自证。提示词保持市场中立：市场规则、字段范围与反馈内容全部由
运行配置注入，本模块不假设任何具体市场。
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["PROMPT_VERSION", "generator_prompts", "reviewer_prompts"]

PROMPT_VERSION = "1"

_GENERATOR_SYSTEM = (
    "你是量化因子研究员。你的任务是基于经济逻辑与先验知识提出横截面选股"
    "因子的候选表达式。表达式必须使用给定的算子白名单与字段范围，以 JSON"
    " 数组输出，每个元素是一棵规格树。市场制度与数据口径由调用方的运行"
    "配置决定，不要假设任何具体市场。只输出 JSON，不要输出解释文字。"
)

_REVIEWER_SYSTEM = (
    "你是量化研究评审。你的任务是独立评审候选因子的经济动机与统计风险，"
    "你无权修改候选，只能给出结构化结论。对每个候选输出 JSON 对象："
    '{"factor_id": 候选标识, "approve": true 或 false, "reason_code": 简短理由码}。'
    "评审依据经济逻辑合理性与过拟合风险，不以样本内表现好坏为准。"
    "只输出 JSON 数组，不要输出解释文字。"
)


def generator_prompts(
    *,
    field_scope: tuple[str, ...],
    operator_names: tuple[str, ...],
    budget: int,
    feedback: dict[str, Any] | None,
) -> tuple[str, str]:
    """返回 (system, prompt)。feedback 为 None 时提示词不含任何验证信息。"""
    sections = [
        f"可用字段: {json.dumps(sorted(field_scope), ensure_ascii=False)}",
        f"算子白名单: {json.dumps(sorted(operator_names), ensure_ascii=False)}",
        "规格树形态: {\"field\": 字段名} 或 {\"const\": 数值} 或"
        " {\"op\": 算子名, \"args\": [子节点], \"params\": {\"window\": 整数}}",
        "时序算子需要 params.window（2 到 250 的整数），截面算子无参数"
        "（cs_winsor 除外，需要 params.sigmas）。",
        f"请提出不超过 {budget} 个候选，多样化经济动机（动量、反转、量价、波动）。",
    ]
    if feedback is not None:
        sections.append(
            "上一轮的结构化反馈（离散类别，无任何统计数值）: "
            + json.dumps(feedback, ensure_ascii=False, sort_keys=True)
        )
    return _GENERATOR_SYSTEM, "\n".join(sections)


def reviewer_prompts(candidates: list[dict[str, Any]]) -> tuple[str, str]:
    """返回 (system, prompt)。candidates 只含规格与经济描述，不含任何验证统计。"""
    prompt = "候选列表:\n" + json.dumps(candidates, ensure_ascii=False, sort_keys=True)
    return _REVIEWER_SYSTEM, prompt
