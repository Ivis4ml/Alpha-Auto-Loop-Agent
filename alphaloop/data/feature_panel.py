"""外部特征面板装载：按 manifest 声明消费，指纹不符即拒绝。

特征面板（新闻情绪、宏观事件面等）以 parquet 宽表 + 指纹接入；本模块
验证内容指纹后并入日线规范面板，面板列进入因子字段范围（字段范围
本身属于六维实验协议，由运行配置声明）。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from alphaloop.contracts.feature_panel import panel_content_fingerprint

__all__ = ["FeaturePanelMismatch", "load_feature_panel", "join_feature_panel"]

_RESERVED_COLUMNS = frozenset({"market_id", "symbol", "trade_date"})


class FeaturePanelMismatch(Exception):
    """面板内容与声明指纹不符。"""


def load_feature_panel(path: Path, *, expected_fingerprint: str) -> pd.DataFrame:
    """读取面板并校验内容指纹；trade_date 统一为 date 对象。"""
    frame = pd.read_parquet(path)
    actual = panel_content_fingerprint(frame.to_csv(index=False))
    if actual != expected_fingerprint:
        raise FeaturePanelMismatch(
            f"特征面板 {path} 的内容指纹与声明不符（声明 {expected_fingerprint[:12]}…，"
            f"实际 {actual[:12]}…），拒绝消费"
        )
    out = frame.copy()
    out["trade_date"] = out["trade_date"].map(
        lambda value: value if isinstance(value, date) else date.fromisoformat(str(value))
    )
    out["symbol"] = out["symbol"].astype("string")
    return out


def join_feature_panel(daily: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """把面板特征列左连接到日线规范面板上，缺失即空值（不伪造）。"""
    feature_columns = [name for name in panel.columns if name not in _RESERVED_COLUMNS]
    overlap = set(feature_columns) & set(daily.columns)
    if overlap:
        raise ValueError(f"特征列与面板既有列重名: {sorted(overlap)}")
    return daily.merge(
        panel[["symbol", "trade_date", *feature_columns]],
        on=["symbol", "trade_date"],
        how="left",
    )
