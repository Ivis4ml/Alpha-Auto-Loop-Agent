"""应用装配：把数据根目录、市场描述、生成器与反馈策略组装为可运行对象。

市场身份到构建函数的映射只存在于本装配层；上层业务代码一律经
market_id 与注册的构建器工作。
"""

from __future__ import annotations

from datetime import date, datetime
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
from alphaloop.market.protocols import MarketSpec
from alphaloop.market.us_equity import build_us_equity_spec

__all__ = ["build_store", "build_generator", "build_policy", "StoreCalendarView"]


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


class StoreCalendarView:
    """由市场描述实现 contracts.MarketCalendarView 的生产适配器。

    新闻子系统经本适配器取得交易日历与收盘截止时刻，做情绪面板的
    point-in-time 归日，不直接依赖市场描述层。
    """

    def __init__(self, specs: dict[str, MarketSpec]) -> None:
        self._specs = specs

    def _spec(self, market_id: str) -> MarketSpec:
        try:
            return self._specs[market_id]
        except KeyError:
            raise KeyError(f"市场 {market_id} 未装配日历视图") from None

    def timezone(self, market_id: str) -> str:
        return self._spec(market_id).conventions.timezone

    def trading_days(self, market_id: str, start: date, end: date) -> list[date]:
        return self._spec(market_id).calendar.trading_days(start, end)

    def session_cutoff(self, market_id: str, day: date) -> datetime:
        label = self._spec(market_id).calendar.session_cutoff_label()
        return datetime(day.year, day.month, day.day, int(label[:2]), int(label[2:]))

    def next_trading_day(self, market_id: str, day: date) -> date:
        return self._spec(market_id).calendar.next_trading_day(day)
