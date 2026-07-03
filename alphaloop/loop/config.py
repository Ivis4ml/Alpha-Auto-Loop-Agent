"""运行配置：一次研究循环运行的完整口径快照。

运行开始即写入产物目录，运行中只读。protocol_id 固化六个对照维度
（语法版本、字段范围、候选预算、算力预算、门控配置、切分配置），
任何"智能组件更强"的功效主张都要求对照各臂的 protocol_id 相等
（任务书 3.1 第 6 条）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal

from alphaloop.core.dsl.grammar import GRAMMAR_VERSION
from alphaloop.core.schema import SCHEMA_VERSION
from alphaloop.infra.hashing import content_hash

__all__ = ["SplitConfig", "GateConfig", "FeaturePanelRef", "RunConfig"]


@dataclass(frozen=True, slots=True)
class FeaturePanelRef:
    """一个外部特征面板的消费声明：路径、内容指纹与构建配置哈希。

    build_config_hash 来自面板 manifest，用于把"情绪产出被用于选股
    决策"这一事实登记进运行产物（可追溯要求）。
    """

    panel_id: str
    path: str
    fingerprint: str
    build_config_hash: str


@dataclass(frozen=True, slots=True)
class SplitConfig:
    """样本内与保留段的切分口径。日期均为 ISO 字符串，闭区间。"""

    train_start: str
    train_end: str
    embargo_days: int
    holdout_start: str
    holdout_end: str

    def __post_init__(self) -> None:
        train_end = date.fromisoformat(self.train_end)
        holdout_start = date.fromisoformat(self.holdout_start)
        if date.fromisoformat(self.train_start) >= train_end:
            raise ValueError("训练区间起止颠倒")
        if holdout_start >= date.fromisoformat(self.holdout_end):
            raise ValueError("保留区间起止颠倒")
        if self.embargo_days < 0:
            raise ValueError("隔离带天数不能为负")
        gap = (holdout_start - train_end).days
        if gap <= self.embargo_days:
            raise ValueError(
                f"保留段起点距训练段终点仅 {gap} 个日历日，未满足隔离带 {self.embargo_days} 日"
            )


@dataclass(frozen=True, slots=True)
class GateConfig:
    """统计与成本门控配置。dsr_min 为 Deflated Sharpe 概率下限，0 表示不启用。"""

    fdr_q: float = 0.10
    top_k: int = 5
    commission_bps: float = 2.5
    slippage_bps: float = 5.0
    min_train_ic_t: float = 2.0
    dsr_min: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 < self.fdr_q < 1.0:
            raise ValueError(f"fdr_q 必须在 (0, 1) 内: {self.fdr_q}")
        if self.top_k <= 0:
            raise ValueError("top_k 必须为正")
        if not 0.0 <= self.dsr_min < 1.0:
            raise ValueError(f"dsr_min 必须在 [0, 1) 内: {self.dsr_min}")


@dataclass(frozen=True, slots=True)
class RunConfig:
    """一次运行的完整配置。"""

    run_id: str
    root_seed: int
    mode: Literal["A", "B"]
    market_id: str
    dataset_fingerprint: str
    field_scope: tuple[str, ...]
    candidate_budget: int
    compute_budget: int
    universe_size: int
    generator_id: str
    split: SplitConfig
    gate: GateConfig
    schema_version: str = SCHEMA_VERSION
    grammar_version: str = GRAMMAR_VERSION
    n_rounds: int = 1
    llm_generator_model: str | None = None
    llm_reviewer_model: str | None = None
    prompt_version: str | None = None
    llm_cache_mode: str = "off"
    feature_panels: tuple[FeaturePanelRef, ...] = ()
    plan_notional: float = 1_000_000.0

    def __post_init__(self) -> None:
        if self.candidate_budget <= 0 or self.compute_budget <= 0:
            raise ValueError("候选预算与算力预算必须为正")
        if self.candidate_budget > self.compute_budget:
            raise ValueError("候选预算不能超过算力预算")
        if not self.field_scope:
            raise ValueError("字段范围为空")
        if self.n_rounds < 1:
            raise ValueError("轮数必须为正")
        if self.mode == "A" and self.n_rounds != 1:
            raise ValueError("模式 A 只允许单轮（仅先验推理、验证一次）")
        if self.mode == "B" and self.gate.dsr_min <= 0.0:
            raise ValueError("模式 B 必须启用 DSR 紧缩门控（gate.dsr_min > 0）")
        if self.candidate_budget % self.n_rounds != 0:
            raise ValueError("候选预算必须能被轮数整除，保证各轮预算一致")

    def protocol_id(self) -> str:
        """六维实验协议标识。"""
        return content_hash(
            "experiment-protocol",
            self.grammar_version,
            sorted(self.field_scope),
            self.candidate_budget,
            self.compute_budget,
            asdict(self.gate),
            asdict(self.split),
        )

    def freeze_rules(self) -> dict[str, Any]:
        """进入样本外验证前需要冻结的全部规则项。"""
        return {
            "schema_version": self.schema_version,
            "grammar_version": self.grammar_version,
            "field_scope": sorted(self.field_scope),
            "candidate_budget": self.candidate_budget,
            "compute_budget": self.compute_budget,
            "universe_size": self.universe_size,
            "generator_id": self.generator_id,
            "gate_config": asdict(self.gate),
            "split_config": asdict(self.split),
            "mode": self.mode,
            "root_seed": self.root_seed,
            "dataset_fingerprint": self.dataset_fingerprint,
            "n_rounds": self.n_rounds,
            "llm_generator_model": self.llm_generator_model,
            "llm_reviewer_model": self.llm_reviewer_model,
            "prompt_version": self.prompt_version,
            "feature_panels": [
                (ref.panel_id, ref.fingerprint, ref.build_config_hash)
                for ref in self.feature_panels
            ],
        }

    def to_json(self) -> dict[str, Any]:
        """转为可写盘的字典，附派生标识。"""
        payload = asdict(self)
        payload["protocol_id"] = self.protocol_id()
        return payload
