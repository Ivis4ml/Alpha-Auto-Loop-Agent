"""外部特征面板契约。

新闻情绪面板、Polymarket 特征表等外部数据以统一的 manifest 声明接入
研究循环；研究层只依据 manifest 消费 parquet 宽表，不 import 生产方代码。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

__all__ = ["FeaturePanelManifest", "FeaturePanelSource"]

PanelGrain = Literal["date", "date_minute", "date_symbol", "date_minute_symbol"]


@dataclass(frozen=True, slots=True)
class FeaturePanelManifest:
    """特征面板的接入声明。

    字段约定：

    - fingerprint 计入运行配置快照，面板内容变化即新版本。
    - visibility_ts_column 声明 point-in-time 依据的市场可见时刻列。
    - snapshot_start 之前的结构化标签不具备样本外资格（任务书 3.1 第 7 条）。
    - build_config_hash 供研究层在把面板用于选股决策时登记试验引用。
    """

    panel_id: str
    market_id: str
    grain: PanelGrain
    columns: tuple[str, ...]
    fingerprint: str
    visibility_ts_column: str
    snapshot_start: str
    no_lookahead_attested: bool
    build_config_hash: str
    coverage_notes: tuple[str, ...] = ()


class FeaturePanelSource(Protocol):
    """特征面板生产方需要实现的最小接口。"""

    def manifest(self) -> FeaturePanelManifest:
        """返回当前面板的接入声明。"""
        ...

    def panel_path(self) -> str:
        """返回面板 parquet 文件的路径。"""
        ...
