"""Alpha-Auto-Loop-Agent 核心平台。

分层自底向上为 infra、contracts、core、market、data、(govern | llm | execution)、
loop，层间依赖由 pyproject.toml 中的 import-linter 契约机械校验。
"""

__version__ = "0.1.0"
