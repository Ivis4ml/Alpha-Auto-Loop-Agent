"""两个 CI 检查脚本自身的正确性测试：已知违例必须报错，合法样例必须放行。"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import check_import_bypass  # noqa: E402
import check_market_neutral  # noqa: E402


class TestImportBypassChecker:
    def test_detects_dynamic_import(self, tmp_path: Path) -> None:
        source = "import importlib\nmod = importlib.import_module('os')\n"
        assert len(check_import_bypass.scan_source(tmp_path / "s.py", source)) == 1

    def test_detects_dunder_import(self, tmp_path: Path) -> None:
        source = "mod = __import__('os')\n"
        assert len(check_import_bypass.scan_source(tmp_path / "s.py", source)) == 1

    def test_detects_nested_import(self, tmp_path: Path) -> None:
        source = "def f():\n    from os import path\n    return path\n"
        assert len(check_import_bypass.scan_source(tmp_path / "s.py", source)) == 1

    def test_allows_marked_exception_with_reason(self, tmp_path: Path) -> None:
        source = (
            "def f():\n"
            "    import os  # import-bypass: allow(可选依赖，缺失时降级)\n"
            "    return os\n"
        )
        assert check_import_bypass.scan_source(tmp_path / "s.py", source) == []

    def test_rejects_marker_without_reason(self, tmp_path: Path) -> None:
        source = "def f():\n    import os  # import-bypass: allow( )\n    return os\n"
        assert len(check_import_bypass.scan_source(tmp_path / "s.py", source)) == 1

    def test_allows_top_level_import(self, tmp_path: Path) -> None:
        source = "import os\n\n\ndef f():\n    return os\n"
        assert check_import_bypass.scan_source(tmp_path / "s.py", source) == []

    def test_self_test_passes(self) -> None:
        check_import_bypass.self_test()

    def test_repository_is_clean(self) -> None:
        assert check_import_bypass.scan_repository(REPO_ROOT) == []


class TestMarketNeutralChecker:
    def test_detects_market_identity_prompt(self, tmp_path: Path) -> None:
        text = 'PROMPT = "你是 A 股分钟线因子研究员"\n'
        assert len(check_market_neutral.scan_text(tmp_path / "s.py", text)) == 1

    def test_detects_settlement_default(self, tmp_path: Path) -> None:
        text = "# 默认按 T+1 结算\n"
        assert len(check_market_neutral.scan_text(tmp_path / "s.py", text)) == 1

    def test_detects_six_digit_universe_literal(self, tmp_path: Path) -> None:
        text = 'DEFAULT_UNIVERSE = ["300308", "600519"]\n'
        assert len(check_market_neutral.scan_text(tmp_path / "s.py", text)) >= 1

    def test_detects_suffixed_symbol_literal(self, tmp_path: Path) -> None:
        text = 'symbol = "600088.SH"\n'
        assert len(check_market_neutral.scan_text(tmp_path / "s.py", text)) == 1

    def test_detects_benchmark_literal(self, tmp_path: Path) -> None:
        text = 'BENCHMARK = "SPY"\n'
        assert len(check_market_neutral.scan_text(tmp_path / "s.py", text)) == 1

    def test_allows_marked_exception_with_reason(self, tmp_path: Path) -> None:
        text = 'window = "202401"  # market-neutral: allow(年月字符串，非标的代码)\n'
        assert check_market_neutral.scan_text(tmp_path / "s.py", text) == []

    def test_allows_neutral_text(self, tmp_path: Path) -> None:
        text = "# 结算锚点由市场描述注入，本层不假设任何具体市场\n"
        assert check_market_neutral.scan_text(tmp_path / "s.py", text) == []

    def test_self_test_passes(self) -> None:
        check_market_neutral.self_test()

    def test_repository_is_clean(self) -> None:
        assert check_market_neutral.scan_repository(REPO_ROOT) == []
