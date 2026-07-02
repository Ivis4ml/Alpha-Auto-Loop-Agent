"""JSON 与 JSON Lines 的原子读写。

写入统一走临时文件加 os.replace 的原子替换；追加写用于 append-only
账本与事件流。本模块不理解写入内容的业务含义。
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["atomic_write_json", "read_json", "append_jsonl", "read_jsonl"]


def atomic_write_json(path: Path, obj: Any) -> None:
    """把对象以 JSON 原子写入 path，父目录不存在时创建。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(obj, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def read_json(path: Path) -> Any:
    """读取 JSON 文件并返回反序列化对象。"""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """向 JSON Lines 文件追加一条记录并刷盘。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取 JSON Lines 文件的全部记录；文件不存在时返回空列表。

    末尾可能存在因进程中断产生的不完整行，予以跳过，其余行必须合法。
    """
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        lines = handle.readlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            records.append(json.loads(stripped))
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                break
            raise
    return records
