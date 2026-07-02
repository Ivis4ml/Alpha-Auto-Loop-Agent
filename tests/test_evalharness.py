"""信号注入评估框架的行为测试：配对诚实性、对照一致性、真值密封、隔离。"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from alphaloop.loop.config import SplitConfig
from evalharness.harness import InjectionHarness
from evalharness.inject import materialize_injected_dataset
from evalharness.truth import TruthTampered, read_sealed_truth, write_sealed_truth

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CN_ROOT = FIXTURES / "cn_equity" / "minute_db"

SPLIT = SplitConfig(
    train_start="2024-01-02",
    train_end="2024-08-30",
    embargo_days=5,
    holdout_start="2024-09-10",
    holdout_end="2024-12-30",
)


def _read_daily_closes(root: Path, symbol: str) -> list[float]:
    frame = duckdb.sql(
        "SELECT close FROM read_parquet(?) ORDER BY trade_date",
        params=[(root / "daily" / f"{symbol}.parquet").as_posix()],
    ).df()
    return [float(value) for value in frame["close"]]


class TestMaterialization:
    def test_zero_beta_preserves_prices(self, tmp_path: Path) -> None:
        """beta=0 对照走同一物化管线，价格与源逐值一致（浮点容差内）。"""
        target = materialize_injected_dataset(CN_ROOT, tmp_path / "control", beta=0.0)
        source_closes = _read_daily_closes(CN_ROOT, "600519.SH")
        control_closes = _read_daily_closes(target, "600519.SH")
        assert len(source_closes) == len(control_closes)
        for source_value, control_value in zip(source_closes, control_closes, strict=True):
            assert source_value == pytest.approx(control_value, rel=1e-6)

    def test_positive_beta_changes_prices(self, tmp_path: Path) -> None:
        target = materialize_injected_dataset(CN_ROOT, tmp_path / "injected", beta=0.04)
        source_closes = _read_daily_closes(CN_ROOT, "600519.SH")
        injected_closes = _read_daily_closes(target, "600519.SH")
        differences = [
            abs(a - b) / a for a, b in zip(source_closes, injected_closes, strict=True)
        ]
        assert max(differences) > 0.01

    def test_existing_target_rejected(self, tmp_path: Path) -> None:
        materialize_injected_dataset(CN_ROOT, tmp_path / "once", beta=0.0)
        with pytest.raises(FileExistsError):
            materialize_injected_dataset(CN_ROOT, tmp_path / "once", beta=0.0)

    def test_layout_matches_minute_db(self, tmp_path: Path) -> None:
        target = materialize_injected_dataset(CN_ROOT, tmp_path / "layout", beta=0.0)
        assert (target / "minute").is_dir()
        assert (target / "daily").is_dir()
        assert (target / "corp_actions.parquet").exists()
        assert (target / "listing.parquet").exists()


class TestTruthSeal:
    def test_roundtrip(self, tmp_path: Path) -> None:
        secret = b"0" * 32
        write_sealed_truth(tmp_path / "truth.json", {"beta": 0.02}, secret=secret)
        assert read_sealed_truth(tmp_path / "truth.json", secret=secret) == {"beta": 0.02}

    def test_tamper_detected(self, tmp_path: Path) -> None:
        secret = b"0" * 32
        path = tmp_path / "truth.json"
        write_sealed_truth(path, {"beta": 0.02}, secret=secret)
        text = path.read_text(encoding="utf-8").replace("0.02", "0.00")
        path.write_text(text, encoding="utf-8")
        with pytest.raises(TruthTampered):
            read_sealed_truth(path, secret=secret)

    def test_foreign_secret_rejected(self, tmp_path: Path) -> None:
        write_sealed_truth(tmp_path / "truth.json", {"beta": 0.02}, secret=b"a" * 32)
        with pytest.raises(TruthTampered):
            read_sealed_truth(tmp_path / "truth.json", secret=b"b" * 32)


class TestIsolation:
    def test_config_with_truth_token_rejected(self) -> None:
        with pytest.raises(ValueError, match="真值线索"):
            InjectionHarness._assert_config_clean({"run_id": "x", "note": "beta=0.02"})

    def test_clean_config_passes(self) -> None:
        InjectionHarness._assert_config_clean({"run_id": "subject-001", "root_seed": 1})


class TestHonestyCurve:
    def test_paired_discovery_and_false_positive(self, tmp_path: Path) -> None:
        """配对验证（任务书 3.1 第 1 条）：发现率随强度单调上升，对照零误报。

        研究循环以子进程运行，其配置与环境不含任何真值信息。
        """
        harness = InjectionHarness(
            CN_ROOT, "cn_ashare", tmp_path / "workspace", split=SPLIT
        )
        report = harness.evaluate((0.0, 0.02, 0.04, 0.08), runs_per_beta=2)
        assert report.false_positive_rate == 0.0
        assert report.monotone_nondecreasing
        rates = {point.beta: point.discovery_rate for point in report.points}
        assert rates[0.08] == 1.0
        assert rates[0.0] < rates[0.08]
        assert (tmp_path / "workspace" / "honesty_report.json").exists()

    def test_grid_without_control_rejected(self, tmp_path: Path) -> None:
        harness = InjectionHarness(CN_ROOT, "cn_ashare", tmp_path / "ws", split=SPLIT)
        with pytest.raises(ValueError, match="0 对照"):
            harness.evaluate((0.02, 0.04), runs_per_beta=1)
