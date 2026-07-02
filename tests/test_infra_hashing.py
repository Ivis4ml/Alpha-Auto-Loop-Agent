"""infra.hashing 的行为测试。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from alphaloop.infra.hashing import canonical_json, content_hash, tree_fingerprint


def test_canonical_json_is_order_insensitive() -> None:
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_canonical_json_rejects_nan() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_content_hash_namespace_separates_key_spaces() -> None:
    assert content_hash("factor-id", "x") != content_hash("sealed-key", "x")


def test_content_hash_parts_are_unambiguous() -> None:
    assert content_hash("ns", "ab", "c") != content_hash("ns", "a", "bc")


def test_tree_fingerprint_changes_on_content_growth(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("1", encoding="utf-8")
    first = tree_fingerprint(tmp_path)
    time.sleep(0.01)
    (tmp_path / "a.txt").write_text("22", encoding="utf-8")
    assert tree_fingerprint(tmp_path) != first


def test_tree_fingerprint_requires_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        tree_fingerprint(tmp_path / "missing")
