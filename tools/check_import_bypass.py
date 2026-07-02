"""检测绕过 import-linter 静态分层校验的两类手法。

参考项目 AlphaForge 的分层契约曾被两种方式绕过：字符串动态 import
（importlib.import_module、__import__）与函数体内的延迟 import。本脚本
用 AST 扫描全仓库源码，命中即失败；确有必要的例外必须在同一行加
``# import-bypass: allow(理由)`` 标记，理由不能为空。

用法::

    python tools/check_import_bypass.py [--self-test]
"""

from __future__ import annotations

import ast
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

SCAN_PACKAGES = ("alphaloop", "news", "apps", "evalharness")
ALLOW_MARKER = re.compile(r"#\s*import-bypass:\s*allow\((?P<reason>[^)]+)\)")


@dataclass(frozen=True)
class Violation:
    path: Path
    lineno: int
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.lineno}: {self.message}"


def _line_allows(source_lines: list[str], lineno: int) -> bool:
    if lineno - 1 >= len(source_lines):
        return False
    match = ALLOW_MARKER.search(source_lines[lineno - 1])
    return match is not None and match.group("reason").strip() != ""


def _is_dynamic_import_call(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name) and func.id == "__import__":
        return "__import__ 动态导入"
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "import_module"
        and isinstance(func.value, ast.Name)
        and func.value.id == "importlib"
    ):
        return "importlib.import_module 动态导入"
    return None


def scan_source(path: Path, source: str) -> list[Violation]:
    """扫描单个源文件，返回违例列表。"""
    violations: list[Violation] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        return [Violation(path, error.lineno or 0, f"语法错误，无法分析: {error.msg}")]
    source_lines = source.splitlines()

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.function_depth = 0

        def _check_nested_import(self, node: ast.Import | ast.ImportFrom) -> None:
            if self.function_depth > 0 and not _line_allows(source_lines, node.lineno):
                violations.append(
                    Violation(
                        path,
                        node.lineno,
                        "函数体内的延迟 import 绕过静态分层校验；"
                        "确有必要请加 `# import-bypass: allow(理由)`",
                    )
                )

        def visit_Import(self, node: ast.Import) -> None:
            self._check_nested_import(node)
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            self._check_nested_import(node)
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            reason = _is_dynamic_import_call(node)
            if reason is not None and not _line_allows(source_lines, node.lineno):
                violations.append(
                    Violation(
                        path,
                        node.lineno,
                        f"{reason}绕过静态分层校验；确有必要请加 `# import-bypass: allow(理由)`",
                    )
                )
            self.generic_visit(node)

        def _enter_function(self, node: ast.AST) -> None:
            self.function_depth += 1
            self.generic_visit(node)
            self.function_depth -= 1

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._enter_function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._enter_function(node)

    Visitor().visit(tree)
    return violations


def scan_repository(repo_root: Path) -> list[Violation]:
    """扫描仓库内全部受管包的 Python 源码。"""
    violations: list[Violation] = []
    for package in SCAN_PACKAGES:
        package_dir = repo_root / package
        if not package_dir.is_dir():
            continue
        for path in sorted(package_dir.rglob("*.py")):
            violations.extend(scan_source(path, path.read_text(encoding="utf-8")))
    return violations


def self_test() -> None:
    """对内置的已知违例与合法样例断言检测行为正确。"""
    bad_dynamic = "import importlib\nmod = importlib.import_module('os')\n"
    bad_dunder = "mod = __import__('os')\n"
    bad_nested = "def f():\n    import os\n    return os\n"
    ok_marked = (
        "def f():\n"
        "    import os  # import-bypass: allow(可选依赖，缺失时降级)\n"
        "    return os\n"
    )
    ok_top = "import os\n\n\ndef f():\n    return os\n"
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "sample.py"
        cases: list[tuple[str, int]] = [
            (bad_dynamic, 1),
            (bad_dunder, 1),
            (bad_nested, 1),
            (ok_marked, 0),
            (ok_top, 0),
        ]
        for source, expected in cases:
            found = scan_source(fake, source)
            if len(found) != expected:
                raise AssertionError(
                    f"自测失败: 期望 {expected} 项违例，得到 {len(found)}\n源码:\n{source}"
                )
    print("check_import_bypass 自测通过")


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        self_test()
        return 0
    repo_root = Path(__file__).resolve().parent.parent
    violations = scan_repository(repo_root)
    if violations:
        print(f"发现 {len(violations)} 项绕过分层校验的 import：", file=sys.stderr)
        for violation in violations:
            print(f"  {violation.render()}", file=sys.stderr)
        return 1
    print("check_import_bypass 通过：未发现绕过分层校验的 import")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
