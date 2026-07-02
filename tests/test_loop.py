"""基线研究循环的端到端测试：双市场、可复现、封存保护、双模式反馈。"""

from __future__ import annotations

from pathlib import Path

import pytest

from alphaloop.data.minute_store import MinuteStore
from alphaloop.govern.sealed import SealedConsumedError
from alphaloop.infra.events import read_events
from alphaloop.infra.jsonio import read_json
from alphaloop.loop.config import GateConfig, RunConfig, SplitConfig
from alphaloop.loop.engine import run_research_loop
from alphaloop.loop.feedback import ModeAPolicy, ModeBPolicy
from alphaloop.loop.generators import (
    RandomSearchGenerator,
    ResearchContext,
    TemplateSearchGenerator,
)

SPLIT = SplitConfig(
    train_start="2024-01-02",
    train_end="2024-08-30",
    embargo_days=5,
    holdout_start="2024-09-10",
    holdout_end="2024-12-30",
)


def make_config(store: MinuteStore, *, mode: str = "A", seed: int = 42) -> RunConfig:
    gate = GateConfig(top_k=3) if mode == "A" else GateConfig(top_k=3, dsr_min=0.90)
    return RunConfig(
        run_id=f"test-{store.market.market_id}-{mode}-{seed}",
        root_seed=seed,
        mode=mode,  # type: ignore[arg-type]
        market_id=store.market.market_id,
        dataset_fingerprint=store.manifest().fingerprint,
        field_scope=("close", "volume", "amount"),
        candidate_budget=8,
        compute_budget=16,
        universe_size=5,
        generator_id="template_v1",
        split=SPLIT,
        gate=gate,
    )


class TestConfig:
    def test_embargo_violation_rejected(self) -> None:
        with pytest.raises(ValueError, match="隔离带"):
            SplitConfig(
                train_start="2024-01-02",
                train_end="2024-08-30",
                embargo_days=20,
                holdout_start="2024-09-05",
                holdout_end="2024-12-30",
            )

    def test_protocol_id_covers_six_dimensions(self, cn_store: MinuteStore) -> None:
        base = make_config(cn_store)
        assert base.protocol_id() == make_config(cn_store).protocol_id()
        loosened = RunConfig(
            **{**_as_kwargs(base), "gate": GateConfig(fdr_q=0.5, top_k=3)}
        )
        assert loosened.protocol_id() != base.protocol_id()
        bigger = RunConfig(**{**_as_kwargs(base), "candidate_budget": 9})
        assert bigger.protocol_id() != base.protocol_id()


def _as_kwargs(config: RunConfig) -> dict:
    return {
        "run_id": config.run_id,
        "root_seed": config.root_seed,
        "mode": config.mode,
        "market_id": config.market_id,
        "dataset_fingerprint": config.dataset_fingerprint,
        "field_scope": config.field_scope,
        "candidate_budget": config.candidate_budget,
        "compute_budget": config.compute_budget,
        "universe_size": config.universe_size,
        "generator_id": config.generator_id,
        "split": config.split,
        "gate": config.gate,
    }


class TestGenerators:
    def test_template_generator_deterministic(self) -> None:
        context = ResearchContext(field_scope=("close", "volume"), root_seed=1)
        first = TemplateSearchGenerator().propose(context, 8)
        second = TemplateSearchGenerator().propose(context, 8)
        assert [s.factor_id() for s in first] == [s.factor_id() for s in second]
        assert len(first) == 8

    def test_random_generator_seed_determinism(self) -> None:
        context = ResearchContext(field_scope=("close", "volume"), root_seed=9)
        first = RandomSearchGenerator().propose(context, 10)
        second = RandomSearchGenerator().propose(context, 10)
        assert [s.factor_id() for s in first] == [s.factor_id() for s in second]
        other = RandomSearchGenerator().propose(
            ResearchContext(field_scope=("close", "volume"), root_seed=10), 10
        )
        assert [s.factor_id() for s in first] != [s.factor_id() for s in other]


class TestLoopEndToEnd:
    def test_both_markets_complete(
        self, us_store: MinuteStore, cn_store: MinuteStore, tmp_path: Path
    ) -> None:
        for store in (us_store, cn_store):
            out_dir = tmp_path / store.market.market_id
            summary = run_research_loop(
                store,
                make_config(store),
                TemplateSearchGenerator(),
                ModeAPolicy(),
                out_dir,
                sealed_root=tmp_path / f"sealed-{store.market.market_id}",
            )
            assert summary["n_proposed"] == 8
            assert (out_dir / "config.json").exists()
            assert (out_dir / "gate_report.json").exists()
            gate_report = read_json(out_dir / "gate_report.json")
            assert gate_report["n_planned_total"] == 8
            assert isinstance(summary["no_findings"], bool)
            events = read_events(out_dir / "events.jsonl")
            phases = [event["title"] for event in events if event["kind"] == "phase"]
            assert phases[0] == "数据审计"
            assert "保留段验证" in phases

    def test_rerun_same_config_is_reproducible(
        self, cn_store: MinuteStore, tmp_path: Path
    ) -> None:
        config = make_config(cn_store)
        outputs = []
        for label in ("first", "second"):
            out_dir = tmp_path / label
            run_research_loop(
                cn_store,
                config,
                TemplateSearchGenerator(),
                ModeAPolicy(),
                out_dir,
                sealed_root=tmp_path / f"sealed-{label}",
            )
            outputs.append(
                {
                    name: (out_dir / name).read_bytes()
                    for name in ("gate_report.json", "summary.json", "trials.jsonl", "events.jsonl")
                }
            )
        assert outputs[0] == outputs[1]

    def test_sealed_ledger_blocks_second_run(
        self, cn_store: MinuteStore, tmp_path: Path
    ) -> None:
        config = make_config(cn_store)
        shared_sealed = tmp_path / "sealed-shared"
        run_research_loop(
            cn_store,
            config,
            TemplateSearchGenerator(),
            ModeAPolicy(),
            tmp_path / "first",
            sealed_root=shared_sealed,
        )
        with pytest.raises(SealedConsumedError):
            run_research_loop(
                cn_store,
                config,
                TemplateSearchGenerator(),
                ModeAPolicy(),
                tmp_path / "second",
                sealed_root=shared_sealed,
            )

    def test_mode_b_feedback_is_discrete(self, cn_store: MinuteStore, tmp_path: Path) -> None:
        config = make_config(cn_store, mode="B")
        run_research_loop(
            cn_store,
            config,
            TemplateSearchGenerator(),
            ModeBPolicy(),
            tmp_path / "mode-b",
            sealed_root=tmp_path / "sealed-b",
        )
        feedback = read_json(tmp_path / "mode-b" / "feedback.json")
        assert feedback["mode"] == "B"
        for bucket in feedback["buckets"].values():
            for value in bucket.values():
                assert isinstance(value, str)

    def test_policy_mode_mismatch_rejected(self, cn_store: MinuteStore, tmp_path: Path) -> None:
        config = make_config(cn_store, mode="B")
        with pytest.raises(ValueError, match="不一致"):
            run_research_loop(
                cn_store,
                config,
                TemplateSearchGenerator(),
                ModeAPolicy(),
                tmp_path / "mismatch",
                sealed_root=tmp_path / "sealed-m",
            )

    def test_fingerprint_mismatch_rejected(self, cn_store: MinuteStore, tmp_path: Path) -> None:
        config = make_config(cn_store)
        forged = RunConfig(**{**_as_kwargs(config), "dataset_fingerprint": "deadbeef"})
        with pytest.raises(ValueError, match="指纹"):
            run_research_loop(
                cn_store,
                forged,
                TemplateSearchGenerator(),
                ModeAPolicy(),
                tmp_path / "forged",
                sealed_root=tmp_path / "sealed-f",
            )
