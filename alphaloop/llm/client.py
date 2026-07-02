"""厂商无关的大语言模型补全客户端，带三态缓存。

三态语义：

- explore：每次实际调用并写缓存；
- replay：只读缓存，未命中抛 ReplayMissError，绝不发起实际调用——
  这使"同配置重跑结果含模型内容逐字相同"成为可被硬验证的性质；
- auto：命中即取，未命中实际调用并写缓存。

缓存键覆盖 provider、model、system、prompt 与温度，键即内容身份；
运行记录本次消费的键全集，供复现审计。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from alphaloop.infra.hashing import content_hash
from alphaloop.infra.jsonio import atomic_write_json, read_json

__all__ = ["CacheMode", "CompletionProvider", "ReplayMissError", "LlmClient"]

CacheMode = Literal["explore", "replay", "auto"]


class ReplayMissError(Exception):
    """回放模式下缓存未命中：历史结论无法离线复核，立即失败。"""


class CompletionProvider(Protocol):
    """实际补全后端。"""

    provider_id: str

    def complete(self, *, model: str, system: str, prompt: str, temperature: float) -> str:
        """返回补全文本；后端失败时抛异常。"""
        ...


class LlmClient:
    """带三态缓存的补全客户端。"""

    def __init__(
        self,
        provider: CompletionProvider,
        cache_dir: Path,
        *,
        mode: CacheMode = "auto",
    ) -> None:
        if mode not in ("explore", "replay", "auto"):
            raise ValueError(f"未知缓存模式: {mode!r}")
        self.provider = provider
        self.cache_dir = cache_dir
        self.mode = mode
        self.consumed_keys: list[str] = []
        self.stats = {"hit": 0, "miss": 0, "live": 0, "write": 0}
        cache_dir.mkdir(parents=True, exist_ok=True)

    def cache_key(self, *, model: str, system: str, prompt: str, temperature: float) -> str:
        """构造缓存键。"""
        return content_hash(
            "llm-completion", self.provider.provider_id, model, system, prompt, temperature
        )

    def complete(
        self, *, model: str, system: str, prompt: str, temperature: float = 0.0
    ) -> str:
        """按当前缓存模式取得补全文本。"""
        key = self.cache_key(model=model, system=system, prompt=prompt, temperature=temperature)
        self.consumed_keys.append(key)
        path = self.cache_dir / f"{key}.json"
        if self.mode in ("replay", "auto") and path.exists():
            self.stats["hit"] += 1
            return str(read_json(path)["response"])
        self.stats["miss"] += 1
        if self.mode == "replay":
            raise ReplayMissError(
                f"回放缓存未命中（键 {key[:16]}…），该运行无法离线复核；"
                "请先以 explore 或 auto 模式生成缓存"
            )
        response = self.provider.complete(
            model=model, system=system, prompt=prompt, temperature=temperature
        )
        self.stats["live"] += 1
        atomic_write_json(
            path,
            {
                "key": key,
                "provider": self.provider.provider_id,
                "model": model,
                "system": system,
                "prompt": prompt,
                "temperature": temperature,
                "response": response,
            },
        )
        self.stats["write"] += 1
        return response
