"""注入真值的密封存储。

真值（注入强度、信号规格）只存在于评估框架的私有目录，附 HMAC 密封
以检测事后篡改。声明的威胁模型：同机同用户环境下这是治理边界而非
密码学安全边界（密钥与数据同机），保证手段以静态依赖禁止与进程隔离
为主，密封用于事后审计。
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Any

from alphaloop.infra.hashing import canonical_json
from alphaloop.infra.jsonio import atomic_write_json, read_json

__all__ = ["TruthTampered", "write_sealed_truth", "read_sealed_truth"]


class TruthTampered(Exception):
    """真值文件与密封不符。"""


def _seal(secret: bytes, payload: dict[str, Any]) -> str:
    return hmac.new(secret, canonical_json(payload).encode("utf-8"), hashlib.sha256).hexdigest()


def write_sealed_truth(path: Path, payload: dict[str, Any], *, secret: bytes) -> None:
    """写入真值与密封。"""
    atomic_write_json(path, {"payload": payload, "seal": _seal(secret, payload)})


def read_sealed_truth(path: Path, *, secret: bytes) -> dict[str, Any]:
    """读取并校验真值；密封不符抛 TruthTampered。"""
    stored = read_json(path)
    payload = stored["payload"]
    if not hmac.compare_digest(_seal(secret, payload), str(stored["seal"])):
        raise TruthTampered(f"真值密封校验失败: {path}")
    return dict(payload)
