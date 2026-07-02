"""注入评估编排：配对验证发现率与误报率。

对每个强度 beta（含 0 对照）各物化若干数据集，以子进程运行研究循环
（研究进程的配置与环境不含任何真值信息），汇总"发现率随强度单调
上升 + 对照误报率低"的配对报告。beta = 0 与注入组走同一物化管线。
"""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from alphaloop.data.fingerprint import dataset_fingerprint
from alphaloop.infra.jsonio import atomic_write_json, read_json
from alphaloop.loop.config import GateConfig, RunConfig, SplitConfig
from evalharness.inject import TRUE_SIGNAL_TREE, materialize_injected_dataset
from evalharness.truth import write_sealed_truth

__all__ = ["HonestyPoint", "HonestyReport", "InjectionHarness"]

_FORBIDDEN_CONFIG_TOKENS = ("beta", "truth", "inject")


@dataclass(frozen=True, slots=True)
class HonestyPoint:
    """单个强度点的汇总。"""

    beta: float
    n_runs: int
    n_discoveries: int

    @property
    def discovery_rate(self) -> float:
        return self.n_discoveries / self.n_runs if self.n_runs else 0.0


@dataclass(frozen=True, slots=True)
class HonestyReport:
    """配对诚实性报告。"""

    points: tuple[HonestyPoint, ...]
    false_positive_rate: float
    monotone_nondecreasing: bool

    def to_json(self) -> dict[str, object]:
        return {
            "points": [
                {**asdict(point), "discovery_rate": point.discovery_rate}
                for point in self.points
            ],
            "false_positive_rate": self.false_positive_rate,
            "monotone_nondecreasing": self.monotone_nondecreasing,
        }


class InjectionHarness:
    """信号注入评估框架。workspace 为框架私有目录，研究进程不接收其路径。"""

    def __init__(
        self,
        source_root: Path,
        market_id: str,
        workspace: Path,
        *,
        split: SplitConfig,
        universe_size: int = 5,
        candidate_budget: int = 16,
    ) -> None:
        self.source_root = source_root
        self.market_id = market_id
        self.workspace = workspace
        self.split = split
        self.universe_size = universe_size
        self.candidate_budget = candidate_budget
        self._secret = secrets.token_bytes(32)
        workspace.mkdir(parents=True, exist_ok=True)

    def evaluate(self, betas: tuple[float, ...], *, runs_per_beta: int) -> HonestyReport:
        """对强度网格执行配对评估。betas 必须包含 0 对照。"""
        if 0.0 not in betas:
            raise ValueError("强度网格必须包含 0 对照")
        points: list[HonestyPoint] = []
        run_index = 0
        for beta in betas:
            discoveries = 0
            for repetition in range(runs_per_beta):
                discovered = self._single_run(beta, run_index, repetition)
                discoveries += int(discovered)
                run_index += 1
            points.append(
                HonestyPoint(beta=beta, n_runs=runs_per_beta, n_discoveries=discoveries)
            )
        by_beta = sorted(points, key=lambda point: point.beta)
        rates = [point.discovery_rate for point in by_beta]
        report = HonestyReport(
            points=tuple(by_beta),
            false_positive_rate=next(
                point.discovery_rate for point in by_beta if point.beta == 0.0
            ),
            monotone_nondecreasing=all(
                rates[index] <= rates[index + 1] for index in range(len(rates) - 1)
            ),
        )
        atomic_write_json(self.workspace / "honesty_report.json", report.to_json())
        return report

    def _single_run(self, beta: float, run_index: int, repetition: int) -> bool:
        case_dir = self.workspace / f"case-{run_index:03d}"
        dataset_root = case_dir / "dataset"
        materialize_injected_dataset(self.source_root, dataset_root, beta=beta)
        write_sealed_truth(
            case_dir / "truth.json",
            {"beta": beta, "signal": TRUE_SIGNAL_TREE, "run_index": run_index},
            secret=self._secret,
        )
        config = RunConfig(
            run_id=f"subject-{run_index:03d}",
            root_seed=1000 + repetition,
            mode="A",
            market_id=self.market_id,
            dataset_fingerprint=dataset_fingerprint(dataset_root),
            field_scope=("close", "volume", "amount"),
            candidate_budget=self.candidate_budget,
            compute_budget=self.candidate_budget * 2,
            universe_size=self.universe_size,
            generator_id="template_v1",
            split=self.split,
            gate=GateConfig(top_k=3),
        )
        config_payload = config.to_json()
        self._assert_config_clean(config_payload)
        config_path = case_dir / "subject_config.json"
        atomic_write_json(config_path, config_payload)
        out_dir = case_dir / "run"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "apps.cli",
                "run-loop",
                "--config",
                str(config_path),
                "--data-root",
                str(dataset_root),
                "--out",
                str(out_dir),
                "--sealed-root",
                str(case_dir / "sealed"),
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
            timeout=600,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"研究子进程失败 (case {run_index}): {completed.stderr.strip()[:500]}"
            )
        summary = read_json(out_dir / "summary.json")
        return not bool(summary["no_findings"])

    @staticmethod
    def _assert_config_clean(payload: dict[str, object]) -> None:
        """研究进程可见的配置里不允许出现任何真值线索。"""
        text = json.dumps(payload, ensure_ascii=False).lower()
        for token in _FORBIDDEN_CONFIG_TOKENS:
            if token in text:
                raise ValueError(f"研究配置中出现真值线索片段: {token!r}")
