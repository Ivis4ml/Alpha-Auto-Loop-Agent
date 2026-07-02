"""因子规格的静态校验：确定性规则引擎的组成部分，不经任何语言模型。

校验内容：节点形态、算子在白名单内、参数完整且在边界内、元数（args
个数）正确、字段在允许范围内、树深度与节点数上限。生成端（无论人工、
模板还是语言模型）产出的规格一律先过本校验，不合法即拒绝。
"""

from __future__ import annotations

from typing import Any

from alphaloop.core.dsl.operators import OPERATORS

__all__ = ["LintError", "lint_spec"]

_MAX_DEPTH = 12
_MAX_NODES = 64


class LintError(ValueError):
    """规格静态校验失败。"""


def lint_spec(tree: dict[str, Any], *, field_scope: frozenset[str]) -> None:
    """校验规格树；不合法抛 LintError，合法静默返回。

    field_scope 是本次实验协议允许引用的字段集合，属于六维实验协议的
    组成部分（字段范围维度）。
    """
    counter = {"nodes": 0}
    _walk(tree, field_scope=field_scope, depth=0, counter=counter)


def _walk(
    node: Any,  # noqa: ANN401
    *,
    field_scope: frozenset[str],
    depth: int,
    counter: dict[str, int],
) -> None:
    if depth > _MAX_DEPTH:
        raise LintError(f"规格树深度超过上限 {_MAX_DEPTH}")
    counter["nodes"] += 1
    if counter["nodes"] > _MAX_NODES:
        raise LintError(f"规格树节点数超过上限 {_MAX_NODES}")
    if not isinstance(node, dict):
        raise LintError(f"节点必须是对象，得到 {type(node).__name__}")
    keys = set(node.keys())
    if "field" in keys:
        if keys != {"field"}:
            raise LintError(f"字段节点只允许 field 一个键，得到 {sorted(keys)}")
        name = node["field"]
        if not isinstance(name, str) or name not in field_scope:
            raise LintError(f"字段 {name!r} 不在允许范围内: {sorted(field_scope)}")
        return
    if "const" in keys:
        if keys != {"const"}:
            raise LintError(f"常量节点只允许 const 一个键，得到 {sorted(keys)}")
        value = node["const"]
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise LintError(f"常量必须是数值，得到 {value!r}")
        if value != value or value in (float("inf"), float("-inf")):
            raise LintError("常量不允许为 NaN 或无穷")
        return
    if "op" not in keys:
        raise LintError(f"未知节点形态，键为 {sorted(keys)}")
    if not keys <= {"op", "args", "params"}:
        raise LintError(f"算子节点只允许 op/args/params 键，得到 {sorted(keys)}")
    name = node["op"]
    if not isinstance(name, str) or name not in OPERATORS:
        raise LintError(f"算子 {name!r} 不在白名单内")
    definition = OPERATORS[name]
    args = node.get("args", [])
    if not isinstance(args, list) or len(args) != definition.arity:
        raise LintError(f"算子 {name} 需要 {definition.arity} 个参数节点，得到 {len(args)}")
    params = node.get("params", {})
    if not isinstance(params, dict):
        raise LintError(f"算子 {name} 的 params 必须是对象")
    expected = set(definition.params.keys())
    if set(params.keys()) != expected:
        raise LintError(f"算子 {name} 需要参数 {sorted(expected)}，得到 {sorted(params.keys())}")
    for key, spec in definition.params.items():
        value = params[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise LintError(f"算子 {name} 的参数 {key} 必须是整数，得到 {value!r}")
        if not spec.minimum <= value <= spec.maximum:
            raise LintError(
                f"算子 {name} 的参数 {key}={value} 超出边界 [{spec.minimum}, {spec.maximum}]"
            )
    for child in args:
        _walk(child, field_scope=field_scope, depth=depth + 1, counter=counter)
