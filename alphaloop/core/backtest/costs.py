"""交易成本模型：按市场执行侧声明参数化，逐笔按成交金额比例计。"""

from __future__ import annotations

from dataclasses import dataclass, replace

from alphaloop.market.protocols import ExecutionProfile

__all__ = ["CostModel", "cost_model_from_execution"]


@dataclass(frozen=True, slots=True)
class CostModel:
    """成交金额比例成本（基点）。买卖两侧分别计，卖出侧税费只计入卖出。"""

    commission_bps: float
    slippage_bps: float
    stamp_tax_sell_bps: float

    def __post_init__(self) -> None:
        for name in ("commission_bps", "slippage_bps", "stamp_tax_sell_bps"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 不能为负")

    @property
    def buy_rate(self) -> float:
        """买入侧成本占成交金额比例。"""
        return (self.commission_bps + self.slippage_bps) / 1e4

    @property
    def sell_rate(self) -> float:
        """卖出侧成本占成交金额比例。"""
        return (self.commission_bps + self.slippage_bps + self.stamp_tax_sell_bps) / 1e4

    def scaled(self, multiplier: float) -> CostModel:
        """按倍数缩放全部成本项，供成本敏感性分析。"""
        if multiplier < 0:
            raise ValueError(f"成本倍数不能为负: {multiplier}")
        return replace(
            self,
            commission_bps=self.commission_bps * multiplier,
            slippage_bps=self.slippage_bps * multiplier,
            stamp_tax_sell_bps=self.stamp_tax_sell_bps * multiplier,
        )


def cost_model_from_execution(
    execution: ExecutionProfile,
    *,
    commission_bps: float = 2.5,
    slippage_bps: float = 5.0,
) -> CostModel:
    """由市场执行侧声明构建成本模型；卖出侧税费取自市场声明，不由调用方假设。"""
    return CostModel(
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        stamp_tax_sell_bps=execution.stamp_tax_sell_bps,
    )
