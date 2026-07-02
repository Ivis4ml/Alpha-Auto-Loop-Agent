"""确定性随机种子派生。

全平台禁止使用全局随机状态（np.random.seed、random.seed）；随机性一律
从运行配置的根种子按组件名派生独立的 Generator 实例，保证同一运行配置
重跑得到相同随机序列。
"""

from __future__ import annotations

import numpy as np

from alphaloop.infra.hashing import content_hash

__all__ = ["derive_seed", "derive_rng"]


def derive_seed(root_seed: int, component: str) -> int:
    """由根种子与组件名派生该组件的确定性子种子。"""
    digest = content_hash("seed-derivation", root_seed, component)
    return int(digest[:16], 16)


def derive_rng(root_seed: int, component: str) -> np.random.Generator:
    """由根种子与组件名派生独立的 numpy 随机数生成器。"""
    return np.random.default_rng(derive_seed(root_seed, component))
