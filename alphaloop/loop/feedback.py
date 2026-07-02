"""反馈策略：验证结果向生成端回流什么信息，是双模式治理的切换点。

Mode A（仅先验推理）：生成端得不到任何验证统计，digest 恒返回 None。
Mode B（受限反馈）：只回流结构化失败类别与粗分位桶，绝不包含连续
统计量（IC、Sharpe、p 值的具体数值），奖励信息是离散多目标向量。
每次运行使用的模式记录在运行配置中。
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

__all__ = ["FeedbackPolicy", "ModeAPolicy", "ModeBPolicy"]

_ALLOWED_BUCKET_VALUES = frozenset(
    {
        "rejected_static",
        "rejected_train",
        "rejected_fdr",
        "rejected_cost",
        "accepted",
        "positive",
        "negative",
        "flat",
        "high",
        "mid",
        "low",
    }
)


class FeedbackPolicy(Protocol):
    """反馈策略协议。"""

    mode: Literal["A", "B"]

    def digest(self, gate_report: dict[str, Any]) -> dict[str, Any] | None:
        """把门控报告转换为可回流给生成端的信息。"""
        ...


class ModeAPolicy:
    """模式 A：零反馈。"""

    mode: Literal["A", "B"] = "A"

    def digest(self, gate_report: dict[str, Any]) -> dict[str, Any] | None:
        return None


class ModeBPolicy:
    """模式 B：离散桶反馈。输出经 schema 自检，杜绝连续统计量泄漏。"""

    mode: Literal["A", "B"] = "B"

    def digest(self, gate_report: dict[str, Any]) -> dict[str, Any] | None:
        buckets: dict[str, dict[str, str]] = {}
        for candidate in gate_report.get("candidates", []):
            factor_id = str(candidate["factor_id"])
            buckets[factor_id] = {
                "outcome": str(candidate["outcome"]),
                "ic_sign": _sign_bucket(candidate.get("train_ic_mean")),
                "turnover": _turnover_bucket(candidate.get("mean_turnover")),
            }
        feedback = {"mode": "B", "buckets": buckets}
        _assert_discrete(feedback)
        return feedback


def _sign_bucket(value: object) -> str:
    if not isinstance(value, int | float):
        return "flat"
    if value > 0.01:
        return "positive"
    if value < -0.01:
        return "negative"
    return "flat"


def _turnover_bucket(value: object) -> str:
    if not isinstance(value, int | float):
        return "low"
    if value > 0.6:
        return "high"
    if value > 0.2:
        return "mid"
    return "low"


def _assert_discrete(feedback: dict[str, Any]) -> None:
    """结构自检：桶值必须来自受控词表，不允许任何数值出现。"""
    for factor_id, bucket in feedback["buckets"].items():
        for key, value in bucket.items():
            if not isinstance(value, str) or value not in _ALLOWED_BUCKET_VALUES:
                raise ValueError(
                    f"模式 B 反馈中出现受控词表之外的值: {factor_id}.{key}={value!r}"
                )
