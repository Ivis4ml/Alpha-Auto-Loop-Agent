"""应用装配：把数据根目录、市场描述、生成器与反馈策略组装为可运行对象。

市场身份到构建函数的映射只存在于本装配层；上层业务代码一律经
market_id 与注册的构建器工作。
"""

from __future__ import annotations

from pathlib import Path

from alphaloop.data.calendar_source import scan_trading_days
from alphaloop.data.instruments import scan_fund_like_symbols
from alphaloop.data.minute_store import MinuteStore
from alphaloop.loop.feedback import FeedbackPolicy, ModeAPolicy, ModeBPolicy
from alphaloop.loop.generators import (
    CandidateGenerator,
    RandomSearchGenerator,
    TemplateSearchGenerator,
)
from alphaloop.market.cn_ashare import build_cn_ashare_spec
from alphaloop.market.us_equity import build_us_equity_spec

__all__ = ["build_store", "build_generator", "build_policy"]


def build_store(market_id: str, root: Path) -> MinuteStore:
    """按市场 id 装配数据存取入口，交易日序列从数据推导。"""
    days = scan_trading_days(root)
    if market_id == "us_equity":
        spec = build_us_equity_spec(days, scan_fund_like_symbols(root))
    elif market_id == "cn_ashare":
        spec = build_cn_ashare_spec(days)
    else:
        raise ValueError(f"未注册的市场: {market_id!r}")
    return MinuteStore(root, spec, dataset_id=f"{market_id}_minute_db")


def build_generator(generator_id: str) -> CandidateGenerator:
    """按标识装配候选生成器。"""
    if generator_id == "template_v1":
        return TemplateSearchGenerator()
    if generator_id == "random_v1":
        return RandomSearchGenerator()
    raise ValueError(f"未注册的生成器: {generator_id!r}")


def build_policy(mode: str) -> FeedbackPolicy:
    """按模式装配反馈策略。"""
    if mode == "A":
        return ModeAPolicy()
    if mode == "B":
        return ModeBPolicy()
    raise ValueError(f"未知反馈模式: {mode!r}")
