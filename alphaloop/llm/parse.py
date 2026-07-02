"""模型输出的容错解析：从补全文本提取 JSON 对象列表。

容忍常见漂移形态：围栏代码块、单对象、包裹键（candidates/specs/items）、
逐行 NDJSON。解析失败返回空列表交由调用方按"零候选"处理，不抛异常
打断循环；解析只做形态归一，语义合法性一律交确定性规则引擎。
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["extract_json_objects"]

_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_WRAPPER_KEYS = ("candidates", "specs", "items", "factors")


def extract_json_objects(text: str) -> list[dict[str, Any]]:
    """从文本提取对象列表。"""
    candidates: list[str] = []
    for match in _FENCE_PATTERN.finditer(text):
        candidates.append(match.group(1).strip())
    candidates.append(text.strip())
    for chunk in candidates:
        parsed = _try_parse(chunk)
        if parsed:
            return parsed
    ndjson = _try_ndjson(text)
    if ndjson:
        return ndjson
    return []


def _try_parse(chunk: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(chunk)
    except json.JSONDecodeError:
        return []
    return _coerce(value)


def _coerce(value: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in _WRAPPER_KEYS:
            inner = value.get(key)
            if isinstance(inner, list):
                return [item for item in inner if isinstance(item, dict)]
        return [value]
    return []


def _try_ndjson(text: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    return objects
