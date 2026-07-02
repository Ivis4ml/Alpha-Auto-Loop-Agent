"""规则冻结：进入样本外验证之前固定全部规则，之后任何改动使结果失效。

冻结对象是一组命名规则项（门控阈值、语法版本、字段范围、成本模型、
切分口径、生成器配置、提示词包等）的内容哈希汇总。样本外验证入口
重算比对；不一致即判 oos_eligible=False，且该判定不可逆（写入运行
产物，不提供任何解除接口）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alphaloop.infra.hashing import content_hash
from alphaloop.infra.jsonio import atomic_write_json, read_json

__all__ = ["FreezeViolation", "compute_freeze_hash", "write_freeze_manifest", "verify_freeze"]


class FreezeViolation(Exception):
    """冻结后规则发生改动，相应结果失去样本外资格。"""


def compute_freeze_hash(rules: dict[str, Any]) -> str:
    """对规则项字典计算冻结哈希；键为规则项名，值为可序列化内容。"""
    if not rules:
        raise ValueError("冻结规则项为空，冻结无意义")
    item_hashes = {
        name: content_hash("freeze-item", value) for name, value in sorted(rules.items())
    }
    return content_hash("freeze-manifest", item_hashes)


def write_freeze_manifest(path: Path, rules: dict[str, Any]) -> str:
    """写入冻结清单并返回 freeze_hash。清单已存在时拒绝覆盖。"""
    if path.exists():
        raise FreezeViolation(f"冻结清单已存在，拒绝覆盖: {path}")
    freeze_hash = compute_freeze_hash(rules)
    item_hashes = {
        name: content_hash("freeze-item", value) for name, value in sorted(rules.items())
    }
    atomic_write_json(
        path,
        {"freeze_hash": freeze_hash, "item_hashes": item_hashes, "rule_names": sorted(rules)},
    )
    return freeze_hash


def verify_freeze(path: Path, current_rules: dict[str, Any]) -> str:
    """比对当前规则与冻结清单；一致返回 freeze_hash，不一致抛 FreezeViolation。"""
    if not path.exists():
        raise FreezeViolation(f"冻结清单不存在: {path}")
    manifest = read_json(path)
    current_hash = compute_freeze_hash(current_rules)
    if current_hash != manifest["freeze_hash"]:
        current_items = {
            name: content_hash("freeze-item", value)
            for name, value in sorted(current_rules.items())
        }
        changed = sorted(
            name
            for name in set(current_items) | set(manifest["item_hashes"])
            if current_items.get(name) != manifest["item_hashes"].get(name)
        )
        raise FreezeViolation(f"冻结后规则发生改动，失去样本外资格；改动项: {changed}")
    return str(manifest["freeze_hash"])
