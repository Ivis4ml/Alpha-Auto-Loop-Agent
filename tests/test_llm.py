"""LLM 客户端、解析、生成/评审角色与双模式多轮循环的测试。

全部测试使用确定性的假后端，不依赖网络与本机 claude 命令行。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alphaloop.core.dsl.grammar import FactorSpec
from alphaloop.data.minute_store import MinuteStore
from alphaloop.llm.client import LlmClient, ReplayMissError
from alphaloop.llm.parse import extract_json_objects
from alphaloop.loop.config import GateConfig, RunConfig, SplitConfig
from alphaloop.loop.engine import run_research_loop
from alphaloop.loop.feedback import ModeBPolicy
from alphaloop.loop.generators import ResearchContext
from alphaloop.loop.llm_roles import LlmGenerator, LlmReviewer


class FakeProvider:
    """确定性假后端：按 prompt 内容路由到预置回复，并记录全部调用。"""

    provider_id = "fake"

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[dict[str, str]] = []

    def complete(self, *, model: str, system: str, prompt: str, temperature: float) -> str:
        self.calls.append({"model": model, "system": system, "prompt": prompt})
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


class FailingProvider:
    provider_id = "fake"

    def complete(self, *, model: str, system: str, prompt: str, temperature: float) -> str:
        raise AssertionError("回放模式不允许触达后端")


_DELTA5 = {"op": "ts_delta", "args": [{"field": "close"}], "params": {"window": 5}}
_STD10 = {"op": "ts_std", "args": [{"field": "volume"}], "params": {"window": 10}}
VALID_SPECS = json.dumps(
    [
        {"op": "cs_rank", "args": [_DELTA5]},
        {"op": "cs_zscore", "args": [_STD10]},
        {"op": "future_peek", "args": [{"field": "close"}]},
    ]
)


class TestClientCache:
    def test_explore_writes_then_replay_reads(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        explorer = LlmClient(FakeProvider(["回答甲"]), cache, mode="explore")
        first = explorer.complete(model="m", system="s", prompt="p")
        replayer = LlmClient(FailingProvider(), cache, mode="replay")
        second = replayer.complete(model="m", system="s", prompt="p")
        assert first == second == "回答甲"
        assert replayer.stats["hit"] == 1

    def test_replay_miss_raises(self, tmp_path: Path) -> None:
        client = LlmClient(FailingProvider(), tmp_path / "cache", mode="replay")
        with pytest.raises(ReplayMissError, match="无法离线复核"):
            client.complete(model="m", system="s", prompt="未缓存")

    def test_auto_hits_after_first_call(self, tmp_path: Path) -> None:
        provider = FakeProvider(["回答乙"])
        client = LlmClient(provider, tmp_path / "cache", mode="auto")
        client.complete(model="m", system="s", prompt="p")
        client.complete(model="m", system="s", prompt="p")
        assert len(provider.calls) == 1
        assert client.stats["hit"] == 1

    def test_cache_key_sensitive_to_model(self, tmp_path: Path) -> None:
        client = LlmClient(FakeProvider(["x"]), tmp_path / "cache")
        key_a = client.cache_key(model="a", system="s", prompt="p", temperature=0.0)
        key_b = client.cache_key(model="b", system="s", prompt="p", temperature=0.0)
        assert key_a != key_b

    def test_consumed_keys_recorded(self, tmp_path: Path) -> None:
        client = LlmClient(FakeProvider(["x"]), tmp_path / "cache", mode="explore")
        client.complete(model="m", system="s", prompt="p1")
        client.complete(model="m", system="s", prompt="p2")
        assert len(client.consumed_keys) == 2
        assert client.consumed_keys[0] != client.consumed_keys[1]


class TestParse:
    def test_fenced_json_array(self) -> None:
        text = "以下是候选：\n```json\n[{\"a\": 1}, {\"b\": 2}]\n```"
        assert extract_json_objects(text) == [{"a": 1}, {"b": 2}]

    def test_wrapper_key(self) -> None:
        text = json.dumps({"candidates": [{"a": 1}]})
        assert extract_json_objects(text) == [{"a": 1}]

    def test_single_object(self) -> None:
        assert extract_json_objects('{"a": 1}') == [{"a": 1}]

    def test_ndjson(self) -> None:
        assert extract_json_objects('{"a": 1}\n{"b": 2}') == [{"a": 1}, {"b": 2}]

    def test_garbage_returns_empty(self) -> None:
        assert extract_json_objects("完全不是 JSON") == []


class TestLlmGenerator:
    def test_parses_and_dedupes_keeps_invalid_for_screening(self, tmp_path: Path) -> None:
        """解析出的规格全部算候选（含白名单外的），交规则引擎拒绝并计入试验。"""
        client = LlmClient(FakeProvider([VALID_SPECS]), tmp_path / "cache", mode="explore")
        generator = LlmGenerator(client, "test-model")
        specs = generator.propose(
            ResearchContext(field_scope=("close", "volume"), root_seed=1), budget=8
        )
        assert len(specs) == 3
        ops = [spec.tree.get("op") for spec in specs]
        assert "future_peek" in ops

    def test_prompt_contains_no_market_identity(self, tmp_path: Path) -> None:
        provider = FakeProvider([VALID_SPECS])
        client = LlmClient(provider, tmp_path / "cache", mode="explore")
        LlmGenerator(client, "test-model").propose(
            ResearchContext(field_scope=("close",), root_seed=1), budget=4
        )
        combined = provider.calls[0]["system"] + provider.calls[0]["prompt"]
        for forbidden in ("A 股", "A股", "美股", "T+1", "涨跌停"):
            assert forbidden not in combined


class TestLlmReviewer:
    def test_verdicts_applied_and_missing_defaults_to_pass(self, tmp_path: Path) -> None:
        spec_a = FactorSpec({"op": "cs_rank", "args": [{"field": "close"}]})
        spec_b = FactorSpec({"op": "cs_zscore", "args": [{"field": "volume"}]})
        verdict_text = json.dumps(
            [{"factor_id": spec_a.factor_id(), "approve": False, "reason_code": "动机薄弱"}]
        )
        client = LlmClient(FakeProvider([verdict_text]), tmp_path / "cache", mode="explore")
        verdicts = LlmReviewer(client, "review-model").review([spec_a, spec_b])
        assert verdicts[spec_a.factor_id()] == (False, "动机薄弱")
        assert verdicts[spec_b.factor_id()] == (True, "no_verdict")

    def test_reviewer_sees_no_statistics(self, tmp_path: Path) -> None:
        provider = FakeProvider(["[]"])
        client = LlmClient(provider, tmp_path / "cache", mode="explore")
        spec = FactorSpec({"op": "cs_rank", "args": [{"field": "close"}]})
        LlmReviewer(client, "review-model").review([spec])
        combined = provider.calls[0]["system"] + provider.calls[0]["prompt"]
        for forbidden in ("ic", "IC", "sharpe", "Sharpe", "p_value", "t_stat"):
            assert forbidden not in combined


SPLIT = SplitConfig(
    train_start="2024-01-02",
    train_end="2024-08-30",
    embargo_days=5,
    holdout_start="2024-09-10",
    holdout_end="2024-12-30",
)


def _llm_config(store: MinuteStore, *, mode: str, n_rounds: int, cache_mode: str) -> RunConfig:
    gate = GateConfig(top_k=3) if mode == "A" else GateConfig(top_k=3, dsr_min=0.90)
    return RunConfig(
        run_id=f"llm-{store.market.market_id}-{mode}",
        root_seed=7,
        mode=mode,  # type: ignore[arg-type]
        market_id=store.market.market_id,
        dataset_fingerprint=store.manifest().fingerprint,
        field_scope=("close", "volume", "amount"),
        candidate_budget=4 * n_rounds,
        compute_budget=16 * n_rounds,
        universe_size=5,
        generator_id="llm_v1",
        split=SPLIT,
        gate=gate,
        n_rounds=n_rounds,
        llm_generator_model="gen-model",
        llm_reviewer_model="review-model",
        prompt_version="1",
        llm_cache_mode=cache_mode,
    )


class TestDualModeLoop:
    def test_mode_b_multi_round_feedback_flows_to_prompt(
        self, cn_store: MinuteStore, tmp_path: Path
    ) -> None:
        provider = FakeProvider([VALID_SPECS, VALID_SPECS])
        client = LlmClient(provider, tmp_path / "cache", mode="explore")
        config = _llm_config(cn_store, mode="B", n_rounds=2, cache_mode="explore")
        run_research_loop(
            cn_store,
            config,
            LlmGenerator(client, "gen-model"),
            ModeBPolicy(),
            tmp_path / "run",
            sealed_root=tmp_path / "sealed",
        )
        assert len(provider.calls) == 2
        first_prompt = provider.calls[0]["prompt"]
        second_prompt = provider.calls[1]["prompt"]
        assert "结构化反馈" not in first_prompt
        assert "结构化反馈" in second_prompt
        assert "buckets" in second_prompt
        for numeric_token in ("ic_t", "p_value", "0.0"):
            assert numeric_token not in second_prompt.split("结构化反馈")[1]

    def test_replay_rerun_is_byte_identical(self, cn_store: MinuteStore, tmp_path: Path) -> None:
        """explore 生成缓存后，replay 模式断后端重跑产物逐字节一致。"""
        cache = tmp_path / "cache"
        config = _llm_config(cn_store, mode="A", n_rounds=1, cache_mode="explore")
        outputs: list[dict[str, bytes]] = []
        for label, client in (
            ("explore", LlmClient(FakeProvider([VALID_SPECS]), cache, mode="explore")),
            ("replay", LlmClient(FailingProvider(), cache, mode="replay")),
        ):
            out_dir = tmp_path / label
            run_research_loop(
                cn_store,
                config,
                LlmGenerator(client, "gen-model"),
                _policy_for(config.mode),
                out_dir,
                sealed_root=tmp_path / f"sealed-{label}",
            )
            outputs.append(
                {
                    name: (out_dir / name).read_bytes()
                    for name in ("gate_report.json", "summary.json", "trials.jsonl")
                }
            )
        assert outputs[0] == outputs[1]

    def test_reviewer_rejection_recorded(self, cn_store: MinuteStore, tmp_path: Path) -> None:
        gen_client = LlmClient(FakeProvider([VALID_SPECS]), tmp_path / "c1", mode="explore")
        generator = LlmGenerator(gen_client, "gen-model")
        specs = generator.propose(
            ResearchContext(field_scope=("close", "volume", "amount"), root_seed=7), 4
        )
        reject_all = json.dumps(
            [
                {"factor_id": spec.factor_id(), "approve": False, "reason_code": "过拟合风险"}
                for spec in specs
            ]
        )
        gen_client2 = LlmClient(FakeProvider([VALID_SPECS]), tmp_path / "c2", mode="explore")
        review_client = LlmClient(FakeProvider([reject_all]), tmp_path / "c3", mode="explore")
        config = _llm_config(cn_store, mode="A", n_rounds=1, cache_mode="explore")
        summary = run_research_loop(
            cn_store,
            config,
            LlmGenerator(gen_client2, "gen-model"),
            _policy_for("A"),
            tmp_path / "run",
            sealed_root=tmp_path / "sealed",
            reviewer=LlmReviewer(review_client, "review-model"),
        )
        assert summary["no_findings"] is True
        trials = (tmp_path / "run" / "trials.jsonl").read_text(encoding="utf-8")
        assert "rejected_review" in trials


def _policy_for(mode: str):  # noqa: ANN202
    from alphaloop.loop.feedback import ModeAPolicy

    return ModeAPolicy() if mode == "A" else ModeBPolicy()
