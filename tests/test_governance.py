"""治理四件套的行为测试：试验账本、冻结、一次性消费（含并发）、能力凭证。"""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from alphaloop.govern.freeze import (
    FreezeViolation,
    verify_freeze,
    write_freeze_manifest,
)
from alphaloop.govern.sealed import SealedConsumedError, SealedLedger, sealed_key
from alphaloop.govern.tokens import CapabilityDenied, CapabilityToken, KernelGate
from alphaloop.govern.trials import TrialLedger


class TestTrialLedger:
    def test_planned_then_realized(self, tmp_path: Path) -> None:
        ledger = TrialLedger(tmp_path / "trials.jsonl")
        first = ledger.register_planned("f1", {"generator": "template_v1"})
        second = ledger.register_planned("f2", {"generator": "template_v1"})
        assert (first, second) == (1, 2)
        ledger.register_realized(first, "rejected_static", {"reason": "lint"})
        assert ledger.total_planned() == 2

    def test_realized_without_planned_rejected(self, tmp_path: Path) -> None:
        ledger = TrialLedger(tmp_path / "trials.jsonl")
        with pytest.raises(ValueError, match="未曾登记"):
            ledger.register_realized(1, "accepted", {})

    def test_count_survives_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "trials.jsonl"
        ledger = TrialLedger(path)
        ledger.register_planned("f1", {})
        ledger.register_planned("f2", {})
        reopened = TrialLedger(path)
        assert reopened.total_planned() == 2
        assert reopened.register_planned("f3", {}) == 3


class TestFreeze:
    RULES = {"gate": {"fdr_q": 0.1}, "grammar_version": "1"}

    def test_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "freeze.json"
        freeze_hash = write_freeze_manifest(path, dict(self.RULES))
        assert verify_freeze(path, dict(self.RULES)) == freeze_hash

    def test_mutation_detected_with_item_name(self, tmp_path: Path) -> None:
        path = tmp_path / "freeze.json"
        write_freeze_manifest(path, dict(self.RULES))
        mutated = {"gate": {"fdr_q": 0.2}, "grammar_version": "1"}
        with pytest.raises(FreezeViolation, match="gate"):
            verify_freeze(path, mutated)

    def test_overwrite_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "freeze.json"
        write_freeze_manifest(path, dict(self.RULES))
        with pytest.raises(FreezeViolation, match="拒绝覆盖"):
            write_freeze_manifest(path, dict(self.RULES))

    def test_missing_manifest_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(FreezeViolation, match="不存在"):
            verify_freeze(tmp_path / "absent.json", dict(self.RULES))


class TestSealedLedger:
    def test_key_is_order_independent(self) -> None:
        forward = sealed_key("fh", "dh", ("2024-09-01", "2024-12-31"), ["a", "b", "c"])
        shuffled = sealed_key("fh", "dh", ("2024-09-01", "2024-12-31"), ["c", "a", "b"])
        assert forward == shuffled

    def test_key_changes_with_candidate_set(self) -> None:
        base = sealed_key("fh", "dh", ("2024-09-01", "2024-12-31"), ["a", "b"])
        other = sealed_key("fh", "dh", ("2024-09-01", "2024-12-31"), ["a", "b", "c"])
        assert base != other

    def test_second_consume_rejected(self, tmp_path: Path) -> None:
        ledger = SealedLedger(tmp_path / "sealed")
        ledger.consume("key1", {"consumed_for": "run-a"})
        assert ledger.was_consumed("key1")
        with pytest.raises(SealedConsumedError, match="run-a"):
            ledger.consume("key1", {"consumed_for": "run-b"})

    def test_concurrent_race_exactly_one_winner(self, tmp_path: Path) -> None:
        """8 进程并发争抢同一封存键，必须恰有一个成功。"""
        context = multiprocessing.get_context("spawn")
        queue: multiprocessing.Queue = context.Queue()
        barrier = context.Barrier(8)
        workers = [
            context.Process(
                target=_race_worker, args=(tmp_path / "sealed", "contended", barrier, queue)
            )
            for _ in range(8)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)
        outcomes = [queue.get(timeout=5) for _ in range(8)]
        assert outcomes.count("won") == 1
        assert outcomes.count("rejected") == 7


def _race_worker(
    root: Path, key: str, barrier: object, queue: object
) -> None:  # pragma: no cover - 子进程内执行
    ledger = SealedLedger(root)
    barrier.wait()  # type: ignore[attr-defined]
    try:
        ledger.consume(key, {"consumed_for": "racer"})
        queue.put("won")  # type: ignore[attr-defined]
    except SealedConsumedError:
        queue.put("rejected")  # type: ignore[attr-defined]


class TestCapabilityTokens:
    def test_valid_token_passes(self) -> None:
        gate = KernelGate()
        token = gate.issue("holdout_eval", run_id="run-1", freeze_hash="fh")
        gate.require(token, scope="holdout_eval", run_id="run-1", freeze_hash="fh")

    def test_wrong_scope_rejected(self) -> None:
        gate = KernelGate()
        token = gate.issue("research_only", run_id="run-1", freeze_hash="fh")
        with pytest.raises(CapabilityDenied, match="作用域"):
            gate.require(token, scope="sealed_eval", run_id="run-1", freeze_hash="fh")

    def test_tampered_field_rejected(self) -> None:
        gate = KernelGate()
        token = gate.issue("holdout_eval", run_id="run-1", freeze_hash="fh")
        forged = CapabilityToken(
            scope=token.scope,
            run_id=token.run_id,
            freeze_hash="another",
            signature=token.signature,
        )
        with pytest.raises(CapabilityDenied):
            gate.require(forged, scope="holdout_eval", run_id="run-1", freeze_hash="another")

    def test_foreign_gate_rejected(self) -> None:
        issuer = KernelGate(secret=b"issuer-secret-0000000000000000000")
        verifier = KernelGate(secret=b"another-secret-000000000000000000")
        token = issuer.issue("holdout_eval", run_id="run-1", freeze_hash="fh")
        with pytest.raises(CapabilityDenied, match="签名"):
            verifier.require(token, scope="holdout_eval", run_id="run-1", freeze_hash="fh")

    def test_handcrafted_signature_rejected(self) -> None:
        gate = KernelGate()
        forged = CapabilityToken(
            scope="sealed_eval", run_id="run-1", freeze_hash="fh", signature="00" * 32
        )
        with pytest.raises(CapabilityDenied, match="签名"):
            gate.require(forged, scope="sealed_eval", run_id="run-1", freeze_hash="fh")
