"""因子规格：JSON 表述、规范化标识与静态回看深度。

规格节点三种形态：

- 字段引用：``{"field": "close"}``
- 常量：``{"const": 2.0}``
- 算子应用：``{"op": "ts_mean", "args": [<节点>], "params": {"window": 20}}``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from alphaloop.core.dsl.operators import OPERATORS
from alphaloop.infra.hashing import canonical_json, content_hash

__all__ = ["GRAMMAR_VERSION", "FactorSpec"]

GRAMMAR_VERSION = "1"


@dataclass(frozen=True, slots=True)
class FactorSpec:
    """一个因子的完整规格。tree 为已通过 lint 的 JSON 节点树。"""

    tree: dict[str, Any]

    def factor_id(self) -> str:
        """规范化哈希标识：键排序后的 JSON 树加语法版本，参数变化即新 id。"""
        return content_hash("factor-spec", GRAMMAR_VERSION, self.tree)

    def canonical(self) -> str:
        """规范化 JSON 文本表示。"""
        return canonical_json(self.tree)

    def total_lookback(self) -> int:
        """静态推导整棵树的回看行数；求值时冷启动期自然为空值。"""
        return _lookback(self.tree)

    def fields(self) -> frozenset[str]:
        """收集树中引用的全部字段名。"""
        found: set[str] = set()
        _collect_fields(self.tree, found)
        return frozenset(found)


def _lookback(node: dict[str, Any]) -> int:
    if "field" in node or "const" in node:
        return 0
    name = node["op"]
    definition = OPERATORS[name]
    children = [_lookback(child) for child in node.get("args", [])]
    child_max = max(children) if children else 0
    return child_max + definition.lookback(node.get("params", {}))


def _collect_fields(node: dict[str, Any], found: set[str]) -> None:
    if "field" in node:
        found.add(str(node["field"]))
        return
    if "const" in node:
        return
    for child in node.get("args", []):
        _collect_fields(child, found)
