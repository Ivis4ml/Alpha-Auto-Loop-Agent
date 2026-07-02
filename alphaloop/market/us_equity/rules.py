"""美股市场规则：可交易性、复权因子、选池。"""

from __future__ import annotations

from datetime import date

import pandas as pd

__all__ = ["UsTradability", "UsCumulativeAdjustment", "UsTopAmountUniverse", "is_index_symbol"]


def is_index_symbol(symbol: str) -> bool:
    """美股行情库不含指数行情（基准 SPY 为 ETF），恒为 False。"""
    return False


class UsTradability:
    """日线粒度的可交易性标记。

    美股无涨跌停制度；LULD 波动限制与盘中熔断发生在分钟内尺度，日线
    粒度不建模，属于已声明的近似。停牌日在数据中无行，天然不可交易。
    """

    def attach_flags(self, daily: pd.DataFrame) -> pd.DataFrame:
        out = daily.copy()
        out["buy_ok"] = ~out["is_index"]
        out["sell_ok"] = ~out["is_index"]
        return out


class UsCumulativeAdjustment:
    """由拆股与分红事件构建向后复权乘法因子。

    字段语义（依据源 corp_actions 表并经真实拆股事件校验）：split_ratio
    为一股旧股对应的新股数（例如 10 表示 1 拆 10），cash_div 为每股现金
    分红。因子定义：最新交易日因子为 1，向过去每跨过一个除权日 ex，
    该日之前全部日期的因子乘以 (prev_close - cash_div) / prev_close / split_ratio，
    其中 prev_close 为 ex 前一交易日未复权收盘价。adj_close = close * adj_factor
    的相邻比值即含分红再投资的总收益。
    """

    adjustment_available: bool = True

    def adjust(self, daily: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
        out = daily.sort_values(["symbol", "trade_date"]).copy()
        out["adj_factor"] = 1.0
        if actions.empty:
            return out
        for symbol, group_index in out.groupby("symbol").groups.items():
            symbol_actions = actions[actions["symbol"] == symbol]
            if symbol_actions.empty:
                continue
            block = out.loc[group_index]
            dates = block["trade_date"].tolist()
            closes = block["close"].tolist()
            factor = pd.Series(1.0, index=block.index)
            for action in symbol_actions.sort_values("ex_date").itertuples():
                ex_date = action.ex_date
                prior_positions = [i for i, day in enumerate(dates) if day < ex_date]
                if not prior_positions:
                    continue
                prev_close = closes[prior_positions[-1]]
                ratio = 1.0
                split = getattr(action, "split_ratio", None)
                if split is not None and not pd.isna(split) and float(split) > 0:
                    ratio /= float(split)
                div = getattr(action, "cash_div", None)
                if div is not None and not pd.isna(div) and float(div) > 0 and prev_close > 0:
                    ratio *= (prev_close - float(div)) / prev_close
                factor.iloc[: len(prior_positions)] *= ratio
            out.loc[block.index, "adj_factor"] = factor
        return out

    def flag_suspect_gaps(self, daily: pd.DataFrame) -> pd.DataFrame:
        """复权数据可得，无需嫌疑标注，恒为 False。"""
        out = daily.copy()
        out["suspect_gap"] = False
        return out


class UsTopAmountUniverse:
    """选池规则：截至 as_of 的近 20 个交易日中位成交额 top-N。

    美股日线成交额为估算值（close 乘 volume 聚合），用于排序可接受；
    估算标注由 amount_is_estimated 列透传，消费绝对数值时必须保留标注。
    excluded_symbols 承载按名称识别的基金类载体（ETF、ETN、信托），
    识别逻辑在数据层，本规则只做集合排除。
    """

    window_days: int = 20

    def __init__(self, excluded_symbols: frozenset[str] = frozenset()) -> None:
        self.excluded_symbols = excluded_symbols

    def select(self, daily: pd.DataFrame, as_of: date, size: int) -> list[str]:
        eligible = daily[
            (~daily["is_index"])
            & (daily["trade_date"] <= as_of)
            & ~daily["symbol"].isin(self.excluded_symbols)
        ]
        recent_days = sorted(eligible["trade_date"].unique())[-self.window_days :]
        if not recent_days:
            return []
        window = eligible[eligible["trade_date"].isin(recent_days)]
        median_amount = window.groupby("symbol")["amount"].median().dropna()
        ranked = median_amount.sort_values(ascending=False)
        return [str(symbol) for symbol in ranked.head(size).index]
