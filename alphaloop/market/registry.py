"""市场描述注册表。

市场描述在装配阶段构建（日历数据由数据层注入）后注册；上层代码一律
按 market_id 取用，不直接 import 具体市场子包。
"""

from __future__ import annotations

from alphaloop.market.protocols import MarketSpec

__all__ = ["register_market", "get_market", "list_markets", "clear_registry"]

_REGISTRY: dict[str, MarketSpec] = {}


def register_market(spec: MarketSpec) -> None:
    """注册市场描述；同 id 重复注册视为装配错误，立即报错。"""
    if spec.market_id in _REGISTRY:
        raise ValueError(f"市场 {spec.market_id!r} 已注册，禁止静默覆盖")
    _REGISTRY[spec.market_id] = spec


def get_market(market_id: str) -> MarketSpec:
    """按 id 取市场描述，未注册时报错并列出可用市场。"""
    try:
        return _REGISTRY[market_id]
    except KeyError:
        available = sorted(_REGISTRY) or ["<空>"]
        raise KeyError(f"市场 {market_id!r} 未注册，可用市场: {available}") from None


def list_markets() -> list[str]:
    """返回已注册市场 id 列表，升序。"""
    return sorted(_REGISTRY)


def clear_registry() -> None:
    """清空注册表，仅供测试装配使用。"""
    _REGISTRY.clear()
