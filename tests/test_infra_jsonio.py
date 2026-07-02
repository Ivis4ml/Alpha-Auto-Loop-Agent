"""infra.jsonio 的行为测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from alphaloop.infra.jsonio import append_jsonl, atomic_write_json, read_json, read_jsonl


def test_atomic_write_and_read_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.json"
    atomic_write_json(path, {"seed": 42, "市场": "由配置注入"})
    assert read_json(path) == {"seed": 42, "市场": "由配置注入"}


def test_append_jsonl_accumulates(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_jsonl(path, {"n": 1})
    append_jsonl(path, {"n": 2})
    assert [record["n"] for record in read_jsonl(path)] == [1, 2]


def test_read_jsonl_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_jsonl(tmp_path / "absent.jsonl") == []


def test_read_jsonl_skips_truncated_tail(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_jsonl(path, {"n": 1})
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"n": 2')
    assert [record["n"] for record in read_jsonl(path)] == [1]


def test_read_jsonl_rejects_corrupt_middle(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"n": 1}\nnot-json\n{"n": 3}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        read_jsonl(path)
