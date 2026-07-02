"""合成信号注入：在真实日线数据上按已知强度改写收益路径。

注入方案：以一个可被因子 DSL 表达的信号 s（缺省为成交量五日变化的
横截面分位）为真值，把次日收益改写为 r' = r + beta * (s - s 的截面均值)。
beta = 0 的对照组走完全相同的物化流程（收益按原值重建路径），研究
进程无法从数据形态上区分注入组与对照组（任务书 3.1 第 1 条的配对
验证前提）。

物化产物是一个与 minute_db 同构的目录（本阶段只重写日线；minute 目录
为空占位），拥有独立的数据指纹。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from alphaloop.core.dsl.evaluate import evaluate
from alphaloop.core.dsl.grammar import FactorSpec

__all__ = ["TRUE_SIGNAL_TREE", "materialize_injected_dataset"]

TRUE_SIGNAL_TREE: dict[str, Any] = {
    "op": "cs_rank",
    "args": [{"op": "ts_delta", "args": [{"field": "volume"}], "params": {"window": 5}}],
}


def materialize_injected_dataset(
    source_root: Path,
    target_root: Path,
    *,
    beta: float,
    signal_tree: dict[str, Any] | None = None,
) -> Path:
    """物化注入数据集，返回目标根目录。

    beta 为注入强度（单位截面居中信号对应的次日收益增量）；beta = 0
    时收益不变、路径按原收益重建，与注入组经过完全相同的计算。
    """
    if target_root.exists():
        raise FileExistsError(f"目标目录已存在，拒绝覆盖: {target_root}")
    tree = signal_tree if signal_tree is not None else TRUE_SIGNAL_TREE
    (target_root / "minute").mkdir(parents=True)
    (target_root / "daily").mkdir()
    for table in ("corp_actions.parquet", "listing.parquet"):
        source_table = source_root / table
        if source_table.exists():
            shutil.copyfile(source_table, target_root / table)

    daily = duckdb.sql(
        "SELECT * FROM read_parquet(?, union_by_name=true) ORDER BY symbol, trade_date",
        params=[(source_root / "daily" / "*.parquet").as_posix()],
    ).df()
    if daily.empty:
        raise ValueError(f"源日线为空: {source_root}")
    modified = _rewrite_close_path(daily, beta=beta, tree=tree)
    for symbol, block in modified.groupby("symbol", sort=True):
        out_path = target_root / "daily" / f"{symbol}.parquet"
        block.drop(columns=["_signal"]).to_parquet(out_path, index=False)
    return target_root


def _rewrite_close_path(daily: pd.DataFrame, *, beta: float, tree: dict[str, Any]) -> pd.DataFrame:
    frame = daily.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    frame["_signal"] = evaluate(FactorSpec(tree), frame)
    centered = (
        frame["_signal"] - frame.groupby("trade_date")["_signal"].transform("mean")
    ).fillna(0.0)

    symbol_key = frame["symbol"].to_numpy()
    base_returns = frame["close"] / frame.groupby("symbol")["close"].shift(1) - 1.0
    prev_centered = centered.groupby(symbol_key, sort=False).shift(1).fillna(0.0)
    new_returns = (base_returns + beta * prev_centered).copy()
    first_rows = frame.groupby("symbol", sort=False).head(1).index
    new_returns.loc[first_rows] = 0.0

    first_close = frame.groupby("symbol")["close"].transform("first")
    new_close = (1.0 + new_returns).groupby(symbol_key, sort=False).cumprod() * first_close
    scale = new_close / frame["close"]
    for column in ("open", "high", "low", "close"):
        frame[column] = frame[column] * scale
    return frame
