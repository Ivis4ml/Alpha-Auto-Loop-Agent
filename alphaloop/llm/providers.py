"""补全后端实现。

缺省后端经本机 claude 命令行调用（订阅鉴权，无需 API key），在中性
临时目录运行并禁用全部工具，避免把项目上下文或工具能力泄漏给受限
角色。
"""

from __future__ import annotations

import json
import subprocess
import tempfile

__all__ = ["ClaudeCliProvider", "ProviderError"]


class ProviderError(Exception):
    """后端调用失败。"""


class ClaudeCliProvider:
    """经本机 claude -p 的补全后端。"""

    provider_id = "claude_cli"

    def __init__(self, *, timeout_seconds: int = 300) -> None:
        self.timeout_seconds = timeout_seconds

    def complete(self, *, model: str, system: str, prompt: str, temperature: float) -> str:
        del temperature  # 命令行通道不暴露温度参数，键中保留以防未来后端混淆
        with tempfile.TemporaryDirectory(prefix="alphaloop-llm-") as neutral_dir:
            command = [
                "claude",
                "-p",
                "--output-format",
                "json",
                "--model",
                model,
                "--append-system-prompt",
                system,
                "--disallowedTools",
                "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Agent,Task",
                prompt,
            ]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    cwd=neutral_dir,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except FileNotFoundError as error:
                raise ProviderError("本机未安装 claude 命令行") from error
            except subprocess.TimeoutExpired as error:
                raise ProviderError(f"claude 调用超时（{self.timeout_seconds}s）") from error
        if completed.returncode != 0:
            raise ProviderError(f"claude 退出码 {completed.returncode}: {completed.stderr[:300]}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise ProviderError(f"claude 输出不是合法 JSON: {completed.stdout[:200]}") from error
        result = payload.get("result")
        if not isinstance(result, str) or not result.strip():
            raise ProviderError("claude 输出缺少 result 字段")
        return result
