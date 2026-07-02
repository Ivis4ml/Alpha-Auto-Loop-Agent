"""A 股市场规则：代码判别、涨跌停幅度、可交易性、公司行动缺口标注、选池。"""

from __future__ import annotations

from datetime import date

import pandas as pd

__all__ = [
    "is_index_symbol",
    "price_limit_ratio",
    "CnTradability",
    "CnNullAdjustment",
    "CnTopAmountUniverse",
]

_LIMIT_TOLERANCE = 1e-4


def is_index_symbol(symbol: str) -> bool:
    """判别指数类标的。

    启发式规则，依据源数据的代码分布：上交所指数以 000、88、95 开头
    （个股为 60、68、9 开头），深交所指数以 399 开头，北交所指数以 899
    开头（北证 50 为 899050）。该规则覆盖源数据中约 530 个指数；如引入
    新的代码段需同步扩充。
    """
    code, _, suffix = symbol.partition(".")
    if suffix == "SH":
        return code.startswith(("000", "88", "95"))
    if suffix == "SZ":
        return code.startswith("399")
    if suffix == "BJ":
        return code.startswith("899")
    return False


def price_limit_ratio(symbol: str) -> float:
    """按板块返回涨跌停幅度比例。

    主板 10%，创业板（300/301/302 开头）与科创板（68 开头）20%，
    北交所 30%。风险警示（ST）股票的 5% 限制依赖名称信息，本规则
    不覆盖，属于已声明的近似；消费方不得将本值当作精确制度模型。
    """
    code, _, suffix = symbol.partition(".")
    if suffix == "BJ":
        return 0.30
    if code.startswith(("300", "301", "302")) and suffix == "SZ":
        return 0.20
    if code.startswith("68") and suffix == "SH":
        return 0.20
    return 0.10


class CnTradability:
    """以涨跌停封板为核心的可交易性标记。

    buy_ok 为 False 的情形：一字涨停（开盘即封死涨停且全天未打开，
    以 open == high == low == close 且触及涨停价近似判定），无法买入。
    sell_ok 对称处理一字跌停。停牌日在数据中无行，天然不可交易。
    """

    def attach_flags(self, daily: pd.DataFrame) -> pd.DataFrame:
        out = daily.sort_values(["symbol", "trade_date"]).copy()
        prev_close = out.groupby("symbol")["close"].shift(1)
        ratio = out["symbol"].map(price_limit_ratio)
        limit_up = (prev_close * (1.0 + ratio)).round(2)
        limit_dn = (prev_close * (1.0 - ratio)).round(2)
        one_bar = (
            (out["open"] == out["high"])
            & (out["high"] == out["low"])
            & (out["low"] == out["close"])
        )
        sealed_up = one_bar & (out["close"] >= limit_up * (1.0 - _LIMIT_TOLERANCE))
        sealed_dn = one_bar & (out["close"] <= limit_dn * (1.0 + _LIMIT_TOLERANCE))
        first_day = prev_close.isna()
        out["buy_ok"] = ~(sealed_up.fillna(False) & ~first_day) & ~out["is_index"]
        out["sell_ok"] = ~(sealed_dn.fillna(False) & ~first_day) & ~out["is_index"]
        return out


class CnNullAdjustment:
    """无复权数据市场的公司行动模型：因子恒 1，另给除权嫌疑标注。

    源数据不含拆股分红（corp_actions 为空表），跨除权日的收益不能用
    未复权价格直接计算。本模型把隔夜价差超出该板块涨跌停幅度（另加
    容忍带）的交易日标为 suspect_gap：正常交易无法产生这种缺口，最可能
    的解释是除权除息或长期停牌复牌。小额分红造成的限幅内缺口无法用
    价格数据检出，这是已声明的标注盲区（任务书待拍板项 2 的既定取舍）。
    """

    adjustment_available: bool = False
    _GAP_MARGIN = 0.02

    def adjust(self, daily: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
        out = daily.copy()
        out["adj_factor"] = 1.0
        return out

    def flag_suspect_gaps(self, daily: pd.DataFrame) -> pd.DataFrame:
        out = daily.sort_values(["symbol", "trade_date"]).copy()
        prev_close = out.groupby("symbol")["close"].shift(1)
        overnight = out["open"] / prev_close - 1.0
        ratio = out["symbol"].map(price_limit_ratio)
        beyond_limit = overnight.abs() > (ratio + self._GAP_MARGIN)
        out["suspect_gap"] = beyond_limit.fillna(False) & ~out["is_index"]
        return out


class CnTopAmountUniverse:
    """选池规则：截至 as_of 的近 20 个交易日中位真实成交额 top-N，剔除指数。"""

    window_days: int = 20

    def select(self, daily: pd.DataFrame, as_of: date, size: int) -> list[str]:
        eligible = daily[(~daily["is_index"]) & (daily["trade_date"] <= as_of)]
        recent_days = sorted(eligible["trade_date"].unique())[-self.window_days :]
        if not recent_days:
            return []
        window = eligible[eligible["trade_date"].isin(recent_days)]
        median_amount = window.groupby("symbol")["amount"].median().dropna()
        ranked = median_amount.sort_values(ascending=False)
        return [str(symbol) for symbol in ranked.head(size).index]
