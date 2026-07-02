"""事件总线：线程局部 sink、JSON Lines 追加写、按序号增量消费。

设计取自 minute_factor_agent 已验证的模式：事件种类收敛为固定小枚举，
深层代码经模块级 emit 写事件而无需传递 sink；写入失败静默吞掉，绝不
打断主流程；消费侧按 seq 增量读取，运行中轮询与运行后回放走同一路径。
"""

from __future__ import annotations

import contextlib
import json
import threading
from pathlib import Path
from typing import Any

__all__ = ["EVENT_KINDS", "EVENT_SCHEMA_VERSION", "EventSink", "emit", "read_events"]

EVENT_SCHEMA_VERSION = 1

EVENT_KINDS = frozenset({"phase", "think", "tool", "result", "gate", "artifact", "info", "warn"})

_local = threading.local()


class EventSink:
    """向单个 events.jsonl 文件追加事件的写入端。

    seq 自增且从已有文件的最大 seq 之后继续（truncate=False 时），
    因此续写不会产生重复序号。
    """

    def __init__(self, path: Path, *, truncate: bool = True) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        if truncate or not path.exists():
            path.write_text("", encoding="utf-8")
            self._seq = 0
        else:
            self._seq = self._max_existing_seq()

    def _max_existing_seq(self) -> int:
        max_seq = 0
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                seq = record.get("seq")
                if isinstance(seq, int) and seq > max_seq:
                    max_seq = seq
        return max_seq

    def write(self, event: dict[str, Any]) -> dict[str, Any]:
        """补齐 seq 与 schema_version 后追加一行；返回写入的完整事件。"""
        with self._lock:
            self._seq += 1
            record = {"schema_version": EVENT_SCHEMA_VERSION, "seq": self._seq, **event}
            line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
            return record

    def install(self) -> None:
        """把本 sink 绑定到当前线程，供 emit 使用。"""
        _local.sink = self

    def uninstall(self) -> None:
        """解除当前线程的 sink 绑定。"""
        if getattr(_local, "sink", None) is self:
            _local.sink = None


def emit(kind: str, title: str = "", **payload: Any) -> None:
    """向当前线程绑定的 sink 写一条事件。

    未绑定 sink 时静默丢弃；kind 不在枚举内时立即报错（枚举收敛是
    消费端可依赖的契约，不允许悄悄扩散）。写盘失败静默吞掉。
    """
    if kind not in EVENT_KINDS:
        raise ValueError(f"未知事件种类: {kind!r}，允许的种类: {sorted(EVENT_KINDS)}")
    sink = getattr(_local, "sink", None)
    if sink is None:
        return
    with contextlib.suppress(OSError):
        sink.write({"kind": kind, "title": title, "payload": payload})


def read_events(path: Path, *, since: int = 0) -> list[dict[str, Any]]:
    """读取 seq 大于 since 的事件，按 seq 升序返回；文件不存在返回空列表。"""
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            seq = record.get("seq")
            if isinstance(seq, int) and seq > since:
                events.append(record)
    events.sort(key=lambda record: record["seq"])
    return events
