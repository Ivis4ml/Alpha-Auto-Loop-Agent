"""试验账本：全量候选计数的权威记录。

任何显著性结论的多重检验分母都取自本账本的 planned 全量计数（任务书
3.1 第 4 条）：候选在生成之后、评估之前登记 planned；评估结束回填
realized 结果。循环重试、提示词换版、知识更新产生的候选一律计入，
不存在绕过登记直接评估的路径（评估入口要求 trial_no）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alphaloop.infra.jsonio import append_jsonl, read_jsonl

__all__ = ["TrialRecord", "TrialLedger"]


@dataclass(frozen=True, slots=True)
class TrialRecord:
    """单条试验记录。"""

    trial_no: int
    factor_id: str
    phase: str
    payload: dict[str, Any]


class TrialLedger:
    """append-only JSON Lines 试验账本。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._next_trial_no = self._scan_next_trial_no()

    def _scan_next_trial_no(self) -> int:
        records = read_jsonl(self.path)
        planned = [record["trial_no"] for record in records if record.get("phase") == "planned"]
        return (max(planned) + 1) if planned else 1

    def register_planned(self, factor_id: str, meta: dict[str, Any]) -> int:
        """登记一个计划中的候选，返回试验号。"""
        trial_no = self._next_trial_no
        self._next_trial_no += 1
        append_jsonl(
            self.path,
            {"trial_no": trial_no, "factor_id": factor_id, "phase": "planned", "meta": meta},
        )
        return trial_no

    def register_realized(self, trial_no: int, outcome: str, stats: dict[str, Any]) -> None:
        """回填一个候选的评估结果。outcome 为结构化结论码。"""
        if trial_no >= self._next_trial_no or trial_no < 1:
            raise ValueError(f"试验号 {trial_no} 未曾登记 planned，拒绝回填")
        append_jsonl(
            self.path,
            {"trial_no": trial_no, "phase": "realized", "outcome": outcome, "stats": stats},
        )

    def total_planned(self) -> int:
        """全量 planned 计数：多重检验修正的分母。"""
        return self._next_trial_no - 1

    def records(self) -> list[dict[str, Any]]:
        """返回全部原始记录（审计与报表用）。"""
        return read_jsonl(self.path)
