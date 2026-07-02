"""内容哈希与文件指纹。

全平台的标识符（factor_id、freeze_hash、sealed_key、缓存键）统一经由本模块
生成，保证同一逻辑对象在任何调用点得到相同标识。
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

__all__ = ["canonical_json", "content_hash", "tree_fingerprint"]


def canonical_json(obj: Any) -> str:
    """把可 JSON 序列化的对象规范化为确定性的字符串表示。

    键排序、紧凑分隔符、禁止 NaN，保证同一逻辑内容得到唯一文本形式。
    """
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def content_hash(namespace: str, *parts: Any) -> str:
    """对若干部件计算带命名空间的 sha256 十六进制摘要。

    每个部件先经 :func:`canonical_json` 规范化；部件之间以 NUL 分隔，
    避免拼接歧义。namespace 用于区分不同用途的键空间。
    """
    hasher = hashlib.sha256()
    hasher.update(namespace.encode("utf-8"))
    for part in parts:
        hasher.update(b"\x00")
        hasher.update(canonical_json(part).encode("utf-8"))
    return hasher.hexdigest()


def tree_fingerprint(root: Path, *, patterns: Iterable[str] = ("**/*",)) -> str:
    """目录树的快速指纹：sha256(排序后的 (相对路径, 大小, mtime_ns) 列表)。

    用于数据版本标识。指纹变化即视为新数据版本；不读取文件内容，
    严格校验（parquet footer 级）由数据层在其之上另行实现。
    """
    if not root.is_dir():
        raise FileNotFoundError(f"指纹目标不是目录: {root}")
    entries: list[tuple[str, int, int]] = []
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                stat = os.stat(path)
                entries.append((path.relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns))
    entries.sort()
    return content_hash("tree-fingerprint", entries)
