"""因子 DSL 的行为测试：白名单、标识、回看深度、求值正确性、泄漏防护。"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from alphaloop.core.dsl import (
    OPERATORS,
    FactorSpec,
    LintError,
    evaluate,
    lint_spec,
    no_lookahead_test,
)
from alphaloop.infra.seeds import derive_rng

FIELDS = frozenset({"open", "high", "low", "close", "volume"})


def make_panel(n_symbols: int = 4, n_days: int = 40, seed: int = 7) -> pd.DataFrame:
    rng = derive_rng(seed, "dsl-test-panel")
    days = [date(2024, 1, 1) + timedelta(days=index) for index in range(n_days)]
    rows = []
    for symbol_index in range(n_symbols):
        symbol = f"S{symbol_index:02d}"
        price = 100.0 * (1.0 + 0.1 * symbol_index)
        for day in days:
            price *= float(np.exp(rng.normal(0, 0.02)))
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": day,
                    "open": price * 0.995,
                    "high": price * 1.01,
                    "low": price * 0.99,
                    "close": price,
                    "volume": float(rng.integers(1_000, 100_000)),
                }
            )
    return (
        pd.DataFrame(rows).sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    )


class TestLint:
    def test_unknown_operator_rejected(self) -> None:
        with pytest.raises(LintError, match="白名单"):
            lint_spec({"op": "future_peek", "args": [{"field": "close"}]}, field_scope=FIELDS)

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(LintError, match="不在允许范围"):
            lint_spec({"field": "insider_flow"}, field_scope=FIELDS)

    def test_param_out_of_bounds_rejected(self) -> None:
        tree = {"op": "ts_mean", "args": [{"field": "close"}], "params": {"window": 100000}}
        with pytest.raises(LintError, match="超出边界"):
            lint_spec(tree, field_scope=FIELDS)

    def test_missing_param_rejected(self) -> None:
        tree = {"op": "ts_mean", "args": [{"field": "close"}], "params": {}}
        with pytest.raises(LintError, match="需要参数"):
            lint_spec(tree, field_scope=FIELDS)

    def test_wrong_arity_rejected(self) -> None:
        tree = {"op": "add", "args": [{"field": "close"}]}
        with pytest.raises(LintError, match="参数节点"):
            lint_spec(tree, field_scope=FIELDS)

    def test_nan_const_rejected(self) -> None:
        with pytest.raises(LintError, match="NaN"):
            lint_spec({"const": float("nan")}, field_scope=FIELDS)

    def test_valid_spec_passes(self) -> None:
        tree = {
            "op": "cs_rank",
            "args": [
                {"op": "ts_delta", "args": [{"field": "close"}], "params": {"window": 5}}
            ],
        }
        lint_spec(tree, field_scope=FIELDS)


class TestFactorId:
    def test_id_stable(self) -> None:
        tree = {"op": "ts_mean", "args": [{"field": "close"}], "params": {"window": 20}}
        assert FactorSpec(tree).factor_id() == FactorSpec(dict(tree)).factor_id()

    def test_param_change_yields_new_id(self) -> None:
        base = {"op": "ts_mean", "args": [{"field": "close"}], "params": {"window": 20}}
        other = {"op": "ts_mean", "args": [{"field": "close"}], "params": {"window": 21}}
        assert FactorSpec(base).factor_id() != FactorSpec(other).factor_id()

    def test_fields_collected(self) -> None:
        tree = {
            "op": "div_safe",
            "args": [{"field": "volume"}, {"op": "abs", "args": [{"field": "close"}]}],
        }
        assert FactorSpec(tree).fields() == frozenset({"volume", "close"})


class TestLookback:
    def test_nested_windows_accumulate(self) -> None:
        tree = {
            "op": "ts_mean",
            "args": [
                {"op": "ts_delta", "args": [{"field": "close"}], "params": {"window": 5}}
            ],
            "params": {"window": 10},
        }
        assert FactorSpec(tree).total_lookback() == 15

    @given(outer=st.integers(2, 30), inner=st.integers(2, 30))
    @settings(max_examples=30, deadline=None)
    def test_lookback_additivity_property(self, outer: int, inner: int) -> None:
        tree = {
            "op": "ts_std",
            "args": [
                {"op": "ts_mean", "args": [{"field": "close"}], "params": {"window": inner}}
            ],
            "params": {"window": outer},
        }
        assert FactorSpec(tree).total_lookback() == outer + inner

    def test_cold_start_rows_are_nan(self) -> None:
        panel = make_panel()
        tree = {"op": "ts_mean", "args": [{"field": "close"}], "params": {"window": 10}}
        values = evaluate(FactorSpec(tree), panel)
        first_symbol = panel[panel["symbol"] == "S00"].index
        assert values.loc[first_symbol[:9]].isna().all()
        assert values.loc[first_symbol[9:]].notna().all()


class TestEvaluation:
    def test_ts_mean_matches_manual(self) -> None:
        panel = make_panel(n_symbols=1, n_days=10)
        tree = {"op": "ts_mean", "args": [{"field": "close"}], "params": {"window": 3}}
        values = evaluate(FactorSpec(tree), panel)
        manual = panel["close"].rolling(3, min_periods=3).mean()
        pd.testing.assert_series_equal(values, manual, check_names=False)

    def test_cs_rank_uses_same_timestamp_only(self) -> None:
        panel = make_panel(n_symbols=3, n_days=5)
        values = evaluate(FactorSpec({"op": "cs_rank", "args": [{"field": "close"}]}), panel)
        frame = panel.assign(rank=values)
        for _, block in frame.groupby("trade_date"):
            observed = sorted(block["rank"].tolist())
            expected = [(index + 1) / len(block) for index in range(len(block))]
            assert observed == pytest.approx(expected)

    def test_div_safe_zero_denominator_is_nan(self) -> None:
        panel = make_panel(n_symbols=1, n_days=5)
        panel["volume"] = 0.0
        tree = {"op": "div_safe", "args": [{"field": "close"}, {"field": "volume"}]}
        values = evaluate(FactorSpec(tree), panel)
        assert values.isna().all()

    def test_unsorted_panel_rejected(self) -> None:
        panel = make_panel().sample(frac=1.0, random_state=1)
        with pytest.raises(ValueError, match="升序"):
            evaluate(FactorSpec({"field": "close"}), panel)

    def test_unknown_operator_rejected_at_eval(self) -> None:
        panel = make_panel()
        with pytest.raises(ValueError, match="白名单"):
            evaluate(FactorSpec({"op": "evil", "args": []}), panel)


class TestNoLookahead:
    @pytest.mark.parametrize("name", sorted(OPERATORS))
    def test_all_registered_operators_pass(self, name: str) -> None:
        panel = make_panel()
        definition = OPERATORS[name]
        params = {key: spec.minimum + 1 for key, spec in definition.params.items()}
        children: list[dict[str, object]] = [{"field": "close"}, {"field": "volume"}][
            : definition.arity
        ]
        tree: dict[str, object] = {"op": name, "args": children}
        if params:
            tree["params"] = params
        assert no_lookahead_test(FactorSpec(tree), panel)

    def test_leaky_factor_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """向白名单临时注入一个前视算子，验证动态检测能抓住它。"""
        from alphaloop.core.dsl.operators import OperatorDef

        def _leak(
            x: pd.Series, params: dict[str, int], symbol: pd.Series, ts: pd.Series
        ) -> pd.Series:
            return x.groupby(symbol.to_numpy(), sort=False).shift(-1)

        leaky = OperatorDef("leaky_future", "ts", 1, _leak)
        monkeypatch.setitem(OPERATORS, "leaky_future", leaky)
        panel = make_panel()
        tree = {"op": "leaky_future", "args": [{"field": "close"}]}
        assert not no_lookahead_test(FactorSpec(tree), panel)
