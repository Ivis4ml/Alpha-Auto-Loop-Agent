"""封存段一次性消费账本。

键的全部构成量与调度顺序无关（候选集合取排序后的 factor_id 列表），
修复参考实现对并行完成顺序敏感的缺陷；消费动作是 O_CREAT | O_EXCL
的原子文件创建，文件系统原语保证跨进程并发下恰有一个赢家，重复消费
稳定抛错而不是静默放行。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from alphaloop.infra.hashing import canonical_json, content_hash
from alphaloop.infra.jsonio import read_json

__all__ = ["SealedConsumedError", "sealed_key", "SealedLedger"]


class SealedConsumedError(Exception):
    """封存评估已被消费，重复触发被拒绝。"""


def sealed_key(
    freeze_hash: str,
    dataset_fingerprint: str,
    eval_window: tuple[str, str],
    factor_ids: Sequence[str],
) -> str:
    """构造顺序无关的封存键。"""
    return content_hash(
        "sealed-eval", freeze_hash, dataset_fingerprint, list(eval_window), sorted(factor_ids)
    )


class SealedLedger:
    """以目录为账本：每个键对应一个文件，创建即消费。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def consume(self, key: str, payload: dict[str, Any]) -> None:
        """消费一个封存键；键已被消费时抛 SealedConsumedError。"""
        path = self._path(key)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = read_json(path) if path.stat().st_size > 0 else {}
            raise SealedConsumedError(
                f"封存键 {key} 已于先前被消费（记录: {existing.get('consumed_for', '未知')}），"
                "重复评估被拒绝"
            ) from None
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(canonical_json({"key": key, **payload}))
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            os.unlink(path)
            raise

    def was_consumed(self, key: str) -> bool:
        """查询一个键是否已被消费。"""
        return self._path(key).exists()
