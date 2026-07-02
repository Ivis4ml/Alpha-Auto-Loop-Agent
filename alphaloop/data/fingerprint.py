"""数据集版本指纹。

快速级：目录树的 (相对路径, 大小, mtime) 指纹，秒级完成，用于运行配置
快照与封存键。严格级：另加每个 parquet 文件的行数与列 schema 哈希，
用于对快速级指纹相同但内容可疑的场景做仲裁，代价是全量读 footer。
"""

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq

from alphaloop.infra.hashing import content_hash, tree_fingerprint

__all__ = ["dataset_fingerprint"]


def dataset_fingerprint(root: Path, *, strict: bool = False) -> str:
    """计算数据集目录的版本指纹。

    指纹变化即视为新数据版本；旧封存记录按旧指纹继续有效，不做迁移。
    """
    fast = tree_fingerprint(root)
    if not strict:
        return fast
    footers: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*.parquet")):
        metadata = pq.ParquetFile(path)
        schema_repr = str(metadata.schema_arrow)
        footers.append(
            (path.relative_to(root).as_posix(), metadata.metadata.num_rows, schema_repr)
        )
    return content_hash("dataset-fingerprint-strict", fast, footers)
