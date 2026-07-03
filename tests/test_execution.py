"""交易计划、订单意图编译、记录型适配器与特征面板接入的测试。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from alphaloop.contracts.feature_panel import panel_content_fingerprint
from alphaloop.data.feature_panel import (
    FeaturePanelMismatch,
    join_feature_panel,
    load_feature_panel,
)
from alphaloop.data.minute_store import MinuteStore
from alphaloop.execution.adapters import AdapterRegistry, JournalingAdapter
from alphaloop.execution.intents import OrderIntent, compile_plan
from alphaloop.execution.plan import PlannedPosition, Provenance, TradePlan
from alphaloop.infra.jsonio import read_json, read_jsonl
from alphaloop.loop.config import FeaturePanelRef, GateConfig, RunConfig, SplitConfig
from alphaloop.loop.engine import run_research_loop
from alphaloop.loop.feedback import ModeAPolicy
from alphaloop.loop.generators import RandomSearchGenerator, TemplateSearchGenerator
from alphaloop.market.cn_ashare import build_cn_ashare_spec
from alphaloop.market.us_equity import build_us_equity_spec
from evalharness.inject import materialize_injected_dataset

FIXTURES = Path(__file__).resolve().parent / "fixtures"

SPLIT = SplitConfig(
    train_start="2024-01-02",
    train_end="2024-08-30",
    embargo_days=5,
    holdout_start="2024-09-10",
    holdout_end="2024-12-30",
)


def _plan(market_id: str, positions: tuple[PlannedPosition, ...]) -> TradePlan:
    return TradePlan(
        plan_id="plan0001",
        market_id=market_id,
        as_of="2024-12-30",
        positions=positions,
        cost_model_note="成本后口径",
        provenance=Provenance(
            run_id="run-1",
            factor_ids=("f1",),
            gate_report_ref="gate_report.json",
            sealed_key="sk",
            freeze_hash="fh",
        ),
    )


def _position(symbol: str, price: float, weight: float = 0.3) -> PlannedPosition:
    return PlannedPosition(
        symbol=symbol,
        side="long",
        target_weight=weight,
        entry_reference_price=price,
        entry_style="next_session_close",
        exit_framework="最短持有后按信号衰减退出",
        expected_net_return_bps_after_cost=12.0,
        capacity_notional=1e7,
        capacity_is_estimated=False,
    )


class TestPlanCompiler:
    def test_cn_lot_rounding_and_constraints(self) -> None:
        execution = build_cn_ashare_spec([date(2024, 1, 2)]).execution
        plan = _plan("cn_ashare", (_position("600519.SH", 1500.0, 0.5),))
        intents = compile_plan(plan, execution, notional=1_000_000)
        assert len(intents) == 1
        intent = intents[0]
        assert intent.quantity % 100 == 0
        assert intent.quantity == 300
        assert intent.constraints["min_holding_days"] == 1
        assert intent.constraints["price_limit_rule"] == "board_tiered"
        assert intent.constraints["stamp_tax_sell_bps"] == 5.0

    def test_us_lot_one_no_price_limit(self) -> None:
        execution = build_us_equity_spec([date(2024, 1, 2)]).execution
        plan = _plan("us_equity", (_position("NVDA", 137.5, 0.5),))
        intents = compile_plan(plan, execution, notional=1_000_000)
        assert intents[0].quantity == 3636
        assert "price_limit_rule" not in intents[0].constraints
        assert intents[0].constraints["min_holding_days"] == 0

    def test_short_rejected_when_unsupported(self) -> None:
        execution = build_cn_ashare_spec([date(2024, 1, 2)]).execution
        short = PlannedPosition(
            symbol="600519.SH",
            side="short",
            target_weight=0.5,
            entry_reference_price=1500.0,
            entry_style="next_session_close",
            exit_framework="x",
            expected_net_return_bps_after_cost=1.0,
            capacity_notional=1e7,
            capacity_is_estimated=False,
        )
        with pytest.raises(ValueError, match="不支持做空"):
            compile_plan(_plan("cn_ashare", (short,)), execution, notional=1_000_000)

    def test_sub_lot_position_dropped(self) -> None:
        execution = build_cn_ashare_spec([date(2024, 1, 2)]).execution
        plan = _plan("cn_ashare", (_position("600519.SH", 1500.0, 0.1),))
        intents = compile_plan(plan, execution, notional=100_000)
        assert intents == []


class TestAdapters:
    def test_journaling_submit_and_cancel(self, tmp_path: Path) -> None:
        adapter = JournalingAdapter(tmp_path / "journal.jsonl")
        intent = OrderIntent(
            intent_id="i1",
            plan_id="p1",
            market_id="cn_ashare",
            symbol="600519.SH",
            side="buy",
            quantity=100,
            order_style="next_session_close",
            time_in_force="day",
            constraints={},
        )
        assert adapter.submit(intent).status == "journaled"
        assert adapter.cancel("i1").status == "journaled"
        assert adapter.cancel("missing").status == "rejected"
        records = read_jsonl(tmp_path / "journal.jsonl")
        assert [record["action"] for record in records] == ["submit", "cancel"]

    def test_registry_blocks_live_adapter_for_plan_only_market(self, tmp_path: Path) -> None:
        class DummyLiveAdapter:
            adapter_id = "alpaca_stub"

            def submit(self, intent: OrderIntent):  # noqa: ANN202
                raise NotImplementedError

            def cancel(self, intent_id: str):  # noqa: ANN202
                raise NotImplementedError

        registry = AdapterRegistry()
        cn_execution = build_cn_ashare_spec([date(2024, 1, 2)]).execution
        us_execution = build_us_equity_spec([date(2024, 1, 2)]).execution
        with pytest.raises(ValueError, match="不允许实盘适配器"):
            registry.register("cn_ashare", cn_execution, DummyLiveAdapter())
        registry.register("us_equity", us_execution, DummyLiveAdapter())
        registry.register(
            "cn_ashare", cn_execution, JournalingAdapter(tmp_path / "journal.jsonl")
        )
        assert registry.get("cn_ashare").adapter_id == "journaling_v1"


class TestFeaturePanelIntegration:
    def _panel_frame(self) -> pd.DataFrame:
        days = pd.date_range("2024-01-02", "2024-12-30", freq="B").date
        rows = [
            {"symbol": symbol, "trade_date": day.isoformat(), "blended_score": 0.1}
            for symbol in ("600519.SH", "000001.SZ")
            for day in days
        ]
        return pd.DataFrame(rows)

    def test_load_rejects_fingerprint_mismatch(self, tmp_path: Path) -> None:
        frame = self._panel_frame()
        path = tmp_path / "panel.parquet"
        frame.to_parquet(path, index=False)
        with pytest.raises(FeaturePanelMismatch):
            load_feature_panel(path, expected_fingerprint="deadbeef")

    def test_join_adds_columns_without_fabrication(self, tmp_path: Path) -> None:
        frame = self._panel_frame()
        path = tmp_path / "panel.parquet"
        frame.to_parquet(path, index=False)
        fingerprint = panel_content_fingerprint(
            pd.read_parquet(path).to_csv(index=False)
        )
        loaded = load_feature_panel(path, expected_fingerprint=fingerprint)
        daily = pd.DataFrame(
            {
                "symbol": ["600519.SH", "999999.SH"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 2)],
                "close": [1500.0, 10.0],
            }
        )
        joined = join_feature_panel(daily, loaded)
        assert joined.loc[0, "blended_score"] == 0.1
        assert pd.isna(joined.loc[1, "blended_score"])

    def test_loop_records_panel_reference(self, cn_store: MinuteStore, tmp_path: Path) -> None:
        """研究循环消费情绪面板时，面板引用登记进门控报告（可追溯联动）。"""
        frame = self._panel_frame()
        path = tmp_path / "panel.parquet"
        frame.to_parquet(path, index=False)
        fingerprint = panel_content_fingerprint(pd.read_parquet(path).to_csv(index=False))
        ref = FeaturePanelRef(
            panel_id="sentiment_cn",
            path=path.as_posix(),
            fingerprint=fingerprint,
            build_config_hash="build-hash-001",
        )
        config = RunConfig(
            run_id="panel-int",
            root_seed=3,
            mode="A",
            market_id=cn_store.market.market_id,
            dataset_fingerprint=cn_store.manifest().fingerprint,
            field_scope=("close", "volume", "amount", "blended_score"),
            candidate_budget=6,
            compute_budget=12,
            universe_size=5,
            generator_id="random_v1",
            split=SPLIT,
            gate=GateConfig(top_k=3),
            feature_panels=(ref,),
        )
        run_research_loop(
            cn_store,
            config,
            RandomSearchGenerator(),
            ModeAPolicy(),
            tmp_path / "run",
            sealed_root=tmp_path / "sealed",
        )
        gate_report = read_json(tmp_path / "run" / "gate_report.json")
        assert gate_report["feature_panels"] == [
            {"panel_id": "sentiment_cn", "build_config_hash": "build-hash-001"}
        ]


class TestTradePlanEndToEnd:
    def test_injected_alpha_yields_plan_with_market_constraints(self, tmp_path: Path) -> None:
        """强注入信号下双市场各产出交易计划，约束差异正确。"""
        for market_id, fixture in (
            ("cn_ashare", "cn_equity"),
            ("us_equity", "us_equity"),
        ):
            dataset = materialize_injected_dataset(
                FIXTURES / fixture / "minute_db", tmp_path / f"data-{market_id}", beta=0.08
            )
            from apps.assembly import build_store

            store = build_store(market_id, dataset)
            config = RunConfig(
                run_id=f"plan-{market_id}",
                root_seed=5,
                mode="A",
                market_id=market_id,
                dataset_fingerprint=store.manifest().fingerprint,
                field_scope=("close", "volume", "amount"),
                candidate_budget=16,
                compute_budget=32,
                universe_size=5,
                generator_id="template_v1",
                split=SPLIT,
                gate=GateConfig(top_k=3),
            )
            out_dir = tmp_path / f"run-{market_id}"
            summary = run_research_loop(
                store,
                config,
                TemplateSearchGenerator(),
                ModeAPolicy(),
                out_dir,
                sealed_root=tmp_path / f"sealed-{market_id}",
            )
            assert summary["no_findings"] is False
            assert summary["trade_plan_written"] is True
            plan = read_json(out_dir / "trade_plan.json")
            assert plan["market_id"] == market_id
            assert "成本后口径" in plan["cost_model_note"]
            assert plan["provenance"]["sealed_key"] == summary["sealed_key"]
            intents = read_jsonl(out_dir / "order_intents.jsonl")
            assert intents
            for intent in intents:
                if market_id == "cn_ashare":
                    assert intent["quantity"] % 100 == 0
                    assert intent["constraints"]["min_holding_days"] == 1
                    assert intent["constraints"]["price_limit_rule"] == "board_tiered"
                else:
                    assert intent["constraints"]["min_holding_days"] == 0
                    assert "price_limit_rule" not in intent["constraints"]
