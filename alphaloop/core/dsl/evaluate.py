"""因子规格求值器：白名单算子上的递归下降解释。

面板约定：长表 DataFrame，必须含 symbol 列与时间列（分钟面板为
ts_local，日线面板为 trade_date），并按 (symbol, 时间) 升序排列；求值
结果是与面板等长、对齐索引的 float64 序列。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from alphaloop.core.dsl.grammar import FactorSpec
from alphaloop.core.dsl.operators import OPERATORS

__all__ = ["evaluate", "time_column"]


def time_column(panel: pd.DataFrame) -> str:
    """返回面板的时间列名：分钟面板为 ts_local，日线面板为 trade_date。"""
    if "ts_local" in panel.columns:
        return "ts_local"
    if "trade_date" in panel.columns:
        return "trade_date"
    raise ValueError("面板缺少时间列（ts_local 或 trade_date）")


def evaluate(spec: FactorSpec, panel: pd.DataFrame) -> pd.Series:
    """在面板上求值因子规格，返回 float64 序列。

    调用方必须先经 lint_spec 校验规格；本函数遇到白名单外算子同样
    抛错，属于纵深防御而非第一道防线。
    """
    if "symbol" not in panel.columns:
        raise ValueError("面板缺少 symbol 列")
    ts = panel[time_column(panel)]
    symbol = panel["symbol"]
    ordered = panel.sort_values(["symbol", time_column(panel)])
    if not ordered.index.equals(panel.index):
        raise ValueError("面板必须按 (symbol, 时间) 升序排列后再求值")
    result = _eval_node(spec.tree, panel, symbol, ts)
    return result.astype("float64")


def _eval_node(
    node: dict[str, Any], panel: pd.DataFrame, symbol: pd.Series, ts: pd.Series
) -> pd.Series:
    if "field" in node:
        name = str(node["field"])
        if name not in panel.columns:
            raise ValueError(f"面板缺少字段 {name!r}")
        return pd.to_numeric(panel[name], errors="coerce").astype("float64")
    if "const" in node:
        return pd.Series(float(node["const"]), index=panel.index, dtype="float64")
    name = str(node["op"])
    if name not in OPERATORS:
        raise ValueError(f"算子 {name!r} 不在白名单内")
    definition = OPERATORS[name]
    children = [_eval_node(child, panel, symbol, ts) for child in node.get("args", [])]
    params = node.get("params", {})
    return definition.impl(*children, params, symbol, ts)
