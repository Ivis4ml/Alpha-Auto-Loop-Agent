"""订单意图：交易计划到可执行订单的确定性编译。

编译器是纯函数：给定计划、市场执行声明与名义资金，产出按交易单位
取整的订单意图；市场间的不对称约束（交易单位、最短持有、价格限制
规则）由执行声明注入，编译逻辑不含按市场分支的判断。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from alphaloop.execution.plan import TradePlan
from alphaloop.infra.hashing import content_hash
from alphaloop.market.protocols import ExecutionProfile

__all__ = ["OrderIntent", "compile_plan"]


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """一条订单意图。"""

    intent_id: str
    plan_id: str
    market_id: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    order_style: str
    time_in_force: str
    constraints: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def compile_plan(
    plan: TradePlan,
    execution: ExecutionProfile,
    *,
    notional: float,
) -> list[OrderIntent]:
    """把交易计划编译为订单意图列表。

    数量按 lot_size 向下取整；取整后为零的仓位被丢弃（资金不足以建立
    最小交易单位）。做空仓位要求市场声明支持做空。
    """
    if notional <= 0:
        raise ValueError(f"名义资金必须为正: {notional}")
    intents: list[OrderIntent] = []
    for position in plan.positions:
        if position.side == "short" and not execution.supports_short:
            raise ValueError(f"市场 {plan.market_id} 不支持做空: {position.symbol}")
        target_value = notional * position.target_weight
        raw_quantity = target_value / position.entry_reference_price
        lots = int(raw_quantity // execution.lot_size)
        quantity = lots * execution.lot_size
        if quantity <= 0:
            continue
        constraints: dict[str, Any] = {
            "min_holding_days": execution.min_holding_days,
            "lot_size": execution.lot_size,
            "stamp_tax_sell_bps": execution.stamp_tax_sell_bps,
        }
        if execution.price_limit_rule is not None:
            constraints["price_limit_rule"] = execution.price_limit_rule
        intent_id = content_hash(
            "order-intent", plan.plan_id, position.symbol, position.side, quantity
        )[:16]
        intents.append(
            OrderIntent(
                intent_id=intent_id,
                plan_id=plan.plan_id,
                market_id=plan.market_id,
                symbol=position.symbol,
                side="buy" if position.side == "long" else "sell",
                quantity=quantity,
                order_style=position.entry_style,
                time_in_force="day",
                constraints=constraints,
            )
        )
    return intents
