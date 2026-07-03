"""执行适配器：信号到订单的接口协议与当前阶段的记录型实现。

当前阶段不连接任何真实券商（任务书 3.6）。唯一实现 JournalingAdapter
把订单意图追加写入 JSON Lines 账本。适配器按市场注册；执行声明
supports_live_adapter 为 False 的市场只允许记录型适配器，未来接入
真实或模拟执行只需新增适配器实现，不触碰研究与决策层。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from alphaloop.execution.intents import OrderIntent
from alphaloop.infra.jsonio import append_jsonl, read_jsonl
from alphaloop.market.protocols import ExecutionProfile

__all__ = ["ExecutionReport", "ExecutionAdapter", "JournalingAdapter", "AdapterRegistry"]


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """一次适配器操作的结果。"""

    intent_id: str
    status: str
    detail: str


class ExecutionAdapter(Protocol):
    """执行适配器协议。"""

    adapter_id: str

    def submit(self, intent: OrderIntent) -> ExecutionReport:
        """提交订单意图。"""
        ...

    def cancel(self, intent_id: str) -> ExecutionReport:
        """撤销订单意图。"""
        ...


class JournalingAdapter:
    """记录型适配器：意图只落盘，不发生任何外部交互。"""

    adapter_id = "journaling_v1"

    def __init__(self, journal_path: Path) -> None:
        self.journal_path = journal_path

    def submit(self, intent: OrderIntent) -> ExecutionReport:
        append_jsonl(self.journal_path, {"action": "submit", **intent.to_json()})
        return ExecutionReport(intent_id=intent.intent_id, status="journaled", detail="已记录")

    def cancel(self, intent_id: str) -> ExecutionReport:
        recorded = {
            str(record.get("intent_id"))
            for record in read_jsonl(self.journal_path)
            if record.get("action") == "submit"
        }
        if intent_id not in recorded:
            return ExecutionReport(intent_id=intent_id, status="rejected", detail="意图不存在")
        append_jsonl(self.journal_path, {"action": "cancel", "intent_id": intent_id})
        return ExecutionReport(intent_id=intent_id, status="journaled", detail="撤销已记录")


class AdapterRegistry:
    """按市场注册适配器；不支持实盘适配的市场只允许记录型。"""

    def __init__(self) -> None:
        self._adapters: dict[str, ExecutionAdapter] = {}

    def register(
        self, market_id: str, execution: ExecutionProfile, adapter: ExecutionAdapter
    ) -> None:
        journaling_only = not execution.supports_live_adapter
        if journaling_only and adapter.adapter_id != JournalingAdapter.adapter_id:
            raise ValueError(
                f"市场 {market_id} 的执行声明不允许实盘适配器（当前合规环境限制），"
                f"拒绝注册 {adapter.adapter_id}"
            )
        if market_id in self._adapters:
            raise ValueError(f"市场 {market_id} 已注册适配器，禁止静默覆盖")
        self._adapters[market_id] = adapter

    def get(self, market_id: str) -> ExecutionAdapter:
        try:
            return self._adapters[market_id]
        except KeyError:
            raise KeyError(f"市场 {market_id} 未注册执行适配器") from None
