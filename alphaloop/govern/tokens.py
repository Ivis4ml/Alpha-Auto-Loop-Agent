"""能力凭证：进入受限评估阶段的签名令牌。

威胁模型是同进程或同机内未授权代码路径的误用，属于治理边界而非
密码学安全边界（密钥与校验方同机）。签名为 HMAC-SHA256，任一字段
被篡改即失效；异源密钥签发与手工构造均无法通过校验。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Literal

__all__ = ["Scope", "CapabilityDenied", "CapabilityToken", "KernelGate"]

Scope = Literal["research_only", "holdout_eval", "sealed_eval"]

_SCOPES: tuple[Scope, ...] = ("research_only", "holdout_eval", "sealed_eval")


class CapabilityDenied(Exception):
    """凭证校验失败，访问被拒绝。"""


@dataclass(frozen=True, slots=True)
class CapabilityToken:
    """能力令牌。signature 覆盖全部业务字段。"""

    scope: Scope
    run_id: str
    freeze_hash: str
    signature: str


def _sign(secret: bytes, scope: str, run_id: str, freeze_hash: str) -> str:
    message = "\x00".join((scope, run_id, freeze_hash)).encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


class KernelGate:
    """签发与校验能力令牌的门卫。密钥缺省为每进程随机值。"""

    def __init__(self, secret: bytes | None = None) -> None:
        self._secret = secret if secret is not None else secrets.token_bytes(32)

    def issue(self, scope: Scope, *, run_id: str, freeze_hash: str) -> CapabilityToken:
        """签发指定作用域的令牌。"""
        if scope not in _SCOPES:
            raise ValueError(f"未知作用域: {scope!r}")
        return CapabilityToken(
            scope=scope,
            run_id=run_id,
            freeze_hash=freeze_hash,
            signature=_sign(self._secret, scope, run_id, freeze_hash),
        )

    def require(
        self, token: CapabilityToken, *, scope: Scope, run_id: str, freeze_hash: str
    ) -> None:
        """校验令牌：作用域、运行标识、冻结哈希与签名四项全部匹配。"""
        if token.scope != scope:
            raise CapabilityDenied(f"作用域不符: 需要 {scope}，令牌为 {token.scope}")
        if token.run_id != run_id:
            raise CapabilityDenied("运行标识不符")
        if token.freeze_hash != freeze_hash:
            raise CapabilityDenied("冻结哈希不符")
        expected = _sign(self._secret, token.scope, token.run_id, token.freeze_hash)
        if not hmac.compare_digest(expected, token.signature):
            raise CapabilityDenied("签名无效（字段被篡改或异源密钥签发）")
