"""市场中立断言：市场无关层禁止出现具体市场的身份、规则与默认值。

参考项目 AlphaForge 的市场无关层残留了四处"A 股研究员"系统提示词、
一处 T+1 默认与一处六位代码默认股票池，证明"写代码时留意"并不可靠。
本脚本对市场无关层（alphaloop 除 market 子包外的全部源码与提示词模板）
扫描禁止模式，命中即失败；确有必要的例外必须在同一行加
``# market-neutral: allow(理由)`` 标记。

用法::

    python tools/check_market_neutral.py [--self-test]
"""

from __future__ import annotations

import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

SCAN_ROOTS = (
    "alphaloop/infra",
    "alphaloop/contracts",
    "alphaloop/core",
    "alphaloop/data",
    "alphaloop/govern",
    "alphaloop/llm",
    "alphaloop/execution",
    "alphaloop/loop",
)
SCAN_SUFFIXES = (".py", ".md", ".txt", ".yaml", ".yml", ".json")
ALLOW_MARKER = re.compile(r"#\s*market-neutral:\s*allow\((?P<reason>[^)]+)\)")

FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("具体市场身份", re.compile(r"A股|A 股|美股|沪深|上证|深证|北交所|创业板|科创板")),
    ("市场特有结算规则", re.compile(r"T\+1|T\+0|T \+ 1|T \+ 0")),
    ("市场特有交易制度", re.compile(r"涨跌停|集合竞价|熔断机制|印花税")),
    ("市场后缀代码字面量", re.compile(r"['\"]\w{1,10}\.(SH|SZ|BJ)['\"]")),
    ("六位数字代码字面量", re.compile(r"['\"][0-9]{6}['\"]")),
    ("具体基准标的字面量", re.compile(r"['\"](SPY|QQQ|000300|399300)['\"]")),
)


@dataclass(frozen=True)
class Violation:
    path: Path
    lineno: int
    category: str
    excerpt: str

    def render(self) -> str:
        return f"{self.path}:{self.lineno}: [{self.category}] {self.excerpt.strip()}"


def scan_text(path: Path, text: str) -> list[Violation]:
    """扫描单个文件文本，返回违例列表。"""
    violations: list[Violation] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        marker = ALLOW_MARKER.search(line)
        if marker is not None and marker.group("reason").strip() != "":
            continue
        for category, pattern in FORBIDDEN_PATTERNS:
            if pattern.search(line):
                violations.append(Violation(path, lineno, category, line))
                break
    return violations


def scan_repository(repo_root: Path) -> list[Violation]:
    """扫描全部市场无关层目录。"""
    violations: list[Violation] = []
    for root in SCAN_ROOTS:
        directory = repo_root / root
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix in SCAN_SUFFIXES:
                violations.extend(scan_text(path, path.read_text(encoding="utf-8")))
    return violations


def self_test() -> None:
    """对内置的已知违例与合法样例断言检测行为正确。"""
    cases: list[tuple[str, int]] = [
        ('PROMPT = "你是 A 股分钟线因子研究员"\n', 1),
        ('DEFAULT_UNIVERSE = ["300308", "600519"]\n', 1),
        ('BENCHMARK = "SPY"\n', 1),
        ("# 默认按 T+1 结算\n", 1),
        ('symbol = "600088.SH"\n', 1),
        ("# 结算锚点由市场描述注入，本层不假设任何具体市场\n", 0),
        ('window = "202401"  # market-neutral: allow(年月字符串，非标的代码)\n', 0),
        ('PROMPT = "你是量化因子研究员，市场规则由运行配置注入"\n', 0),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "sample.py"
        for text, expected in cases:
            found = scan_text(fake, text)
            if len(found) != expected:
                raise AssertionError(
                    f"自测失败: 期望 {expected} 项违例，得到 {len(found)}\n文本: {text!r}"
                )
    print("check_market_neutral 自测通过")


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        self_test()
        return 0
    repo_root = Path(__file__).resolve().parent.parent
    violations = scan_repository(repo_root)
    if violations:
        print(f"发现 {len(violations)} 项市场中立违例：", file=sys.stderr)
        for violation in violations:
            print(f"  {violation.render()}", file=sys.stderr)
        return 1
    print("check_market_neutral 通过：市场无关层未发现具体市场耦合")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
