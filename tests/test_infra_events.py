"""infra.events 事件总线的行为测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from alphaloop.infra.events import EventSink, emit, read_events


def test_emit_without_sink_is_silent() -> None:
    emit("info", "无 sink 时静默丢弃")


def test_emit_rejects_unknown_kind(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "events.jsonl")
    sink.install()
    try:
        with pytest.raises(ValueError):
            emit("bogus", "未知种类必须立即报错")
    finally:
        sink.uninstall()


def test_events_roundtrip_with_incremental_read(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    sink = EventSink(path)
    sink.install()
    try:
        emit("phase", "数据审计")
        emit("result", "面板指纹", fingerprint="abc")
    finally:
        sink.uninstall()
    events = read_events(path)
    assert [event["seq"] for event in events] == [1, 2]
    assert events[1]["payload"]["fingerprint"] == "abc"
    assert read_events(path, since=1)[0]["kind"] == "result"


def test_sink_resumes_sequence_without_truncate(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    first = EventSink(path)
    first.write({"kind": "info", "title": "第一段", "payload": {}})
    second = EventSink(path, truncate=False)
    record = second.write({"kind": "info", "title": "续写", "payload": {}})
    assert record["seq"] == 2
    assert len(read_events(path)) == 2


def test_read_events_missing_file(tmp_path: Path) -> None:
    assert read_events(tmp_path / "absent.jsonl") == []
