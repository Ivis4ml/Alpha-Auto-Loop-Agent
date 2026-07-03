"""交易计划：研究循环的最终结构化产物。

计划只描述意图与依据，不涉及任何真实下单；来源追溯（因子、门控报告、
封存键）随计划固化，保证每一份计划可回溯到产生它的运行与证据。
预期净收益字段恒为成本后口径（任务书 3.1 第 8 条）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

__all__ = ["Provenance", "PlannedPosition", "TradePlan"]


@dataclass(frozen=True, slots=True)
class Provenance:
    """计划来源追溯。"""

    run_id: str
    factor_ids: tuple[str, ...]
    gate_report_ref: str
    sealed_key: str
    freeze_hash: str


@dataclass(frozen=True, slots=True)
class PlannedPosition:
    """单个标的的计划仓位。"""

    symbol: str
    side: Literal["long", "short"]
    target_weight: float
    entry_reference_price: float
    entry_style: Literal["next_session_close", "limit_band"]
    exit_framework: str
    expected_net_return_bps_after_cost: float
    capacity_notional: float
    capacity_is_estimated: bool

    def __post_init__(self) -> None:
        if not 0.0 < self.target_weight <= 1.0:
            raise ValueError(f"目标权重必须在 (0, 1] 内: {self.target_weight}")
        if self.entry_reference_price <= 0:
            raise ValueError("参考价必须为正")


@dataclass(frozen=True, slots=True)
class TradePlan:
    """一份完整交易计划。"""

    plan_id: str
    market_id: str
    as_of: str
    positions: tuple[PlannedPosition, ...]
    cost_model_note: str
    provenance: Provenance

    def __post_init__(self) -> None:
        total = sum(position.target_weight for position in self.positions)
        if total > 1.0 + 1e-9:
            raise ValueError(f"目标权重合计超过 1: {total}")

    def to_json(self) -> dict[str, Any]:
        return asdict(self)
