"""基线研究循环编排。

拓扑：数据审计、选池、候选提出、静态筛查（规则引擎）、样本内估计、
规则冻结、保留段验证（能力凭证 + 一次性消费）、统计与成本门控、
产物写盘。全程写事件流；全部产物确定性可复现（同配置重跑逐字节一致）。

诚实性约定：零候选通过门控是合法且被显式报告的结果（"未发现"优于
虚报，任务书 3.1 第 1 条）。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from alphaloop.core.backtest.capacity import estimate_capacity
from alphaloop.core.backtest.costs import CostModel
from alphaloop.core.backtest.engine import run_backtest
from alphaloop.core.backtest.metrics import forward_returns, ic_series
from alphaloop.core.dsl.evaluate import evaluate
from alphaloop.core.dsl.grammar import FactorSpec
from alphaloop.core.stats.fdr import bh_fdr
from alphaloop.core.stats.nw import newey_west_mean_t
from alphaloop.core.stats.sharpe import deflated_sharpe_probability
from alphaloop.data.feature_panel import join_feature_panel, load_feature_panel
from alphaloop.data.minute_store import MinuteStore
from alphaloop.data.universe import build_universe
from alphaloop.execution.adapters import JournalingAdapter
from alphaloop.execution.intents import compile_plan
from alphaloop.execution.plan import PlannedPosition, Provenance, TradePlan
from alphaloop.govern.freeze import verify_freeze, write_freeze_manifest
from alphaloop.govern.sealed import SealedLedger, sealed_key
from alphaloop.govern.tokens import CapabilityToken, KernelGate
from alphaloop.govern.trials import TrialLedger
from alphaloop.infra.events import EventSink, emit
from alphaloop.infra.hashing import content_hash
from alphaloop.infra.jsonio import atomic_write_json
from alphaloop.infra.seeds import derive_seed
from alphaloop.loop.config import RunConfig
from alphaloop.loop.feedback import FeedbackPolicy
from alphaloop.loop.generators import CandidateGenerator, ResearchContext
from alphaloop.loop.llm_roles import CandidateReviewer
from alphaloop.loop.rules import static_screen
from alphaloop.market.protocols import MarketSpec

__all__ = ["run_research_loop"]

_MIN_IC_DAYS = 20


def run_research_loop(
    store: MinuteStore,
    config: RunConfig,
    generator: CandidateGenerator,
    policy: FeedbackPolicy,
    out_dir: Path,
    *,
    sealed_root: Path,
    reviewer: CandidateReviewer | None = None,
) -> dict[str, Any]:
    """执行一次研究循环运行（模式 B 可多轮生成），返回摘要字典。"""
    if policy.mode != config.mode:
        raise ValueError(f"反馈策略模式 {policy.mode} 与运行配置 {config.mode} 不一致")
    if generator.generator_id != config.generator_id:
        raise ValueError("生成器与运行配置声明不一致")
    out_dir.mkdir(parents=True, exist_ok=True)
    sink = EventSink(out_dir / "events.jsonl")
    sink.install()
    try:
        return _run(store, config, generator, policy, out_dir, sealed_root, reviewer)
    finally:
        sink.uninstall()


def _run(
    store: MinuteStore,
    config: RunConfig,
    generator: CandidateGenerator,
    policy: FeedbackPolicy,
    out_dir: Path,
    sealed_root: Path,
    reviewer: CandidateReviewer | None,
) -> dict[str, Any]:
    atomic_write_json(out_dir / "config.json", config.to_json())
    market = store.market
    split = config.split
    train_start = date.fromisoformat(split.train_start)
    train_end = date.fromisoformat(split.train_end)
    holdout_start = date.fromisoformat(split.holdout_start)
    holdout_end = date.fromisoformat(split.holdout_end)

    # 一、数据审计
    emit("phase", "数据审计")
    manifest = store.manifest()
    if manifest.fingerprint != config.dataset_fingerprint:
        raise ValueError("数据集指纹与运行配置不一致，拒绝继续")
    train_days = market.calendar.trading_days(train_start, train_end)
    holdout_days = market.calendar.trading_days(holdout_start, holdout_end)
    audit = {
        "market_id": market.market_id,
        "dataset_fingerprint": manifest.fingerprint,
        "n_train_days": len(train_days),
        "n_holdout_days": len(holdout_days),
        "adjustment_available": manifest.adjustment_available,
    }
    atomic_write_json(out_dir / "audit.json", audit)
    emit("result", "审计完成", **audit)
    if len(train_days) < _MIN_IC_DAYS * 2 or len(holdout_days) < _MIN_IC_DAYS:
        raise ValueError("训练或保留段交易日不足，无法出具可信结论")

    # 二、选池（只用截至训练段终点的信息）
    emit("phase", "股票池")
    universe = build_universe(store, train_end, config.universe_size)
    emit("result", "选池完成", size=len(universe))
    if len(universe) < 3:
        raise ValueError("股票池过小")

    # 三、面板准备
    daily = store.read_daily(universe, train_start, holdout_end)
    actions = store.read_corp_actions()
    adjusted = market.corporate_actions.adjust(daily, actions)
    adjusted = market.corporate_actions.flag_suspect_gaps(adjusted)
    adjusted = adjusted.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    for panel_ref in config.feature_panels:
        feature_frame = load_feature_panel(
            Path(panel_ref.path), expected_fingerprint=panel_ref.fingerprint
        )
        adjusted = join_feature_panel(adjusted, feature_frame)
        emit(
            "artifact",
            "特征面板并入",
            panel_id=panel_ref.panel_id,
            build_config_hash=panel_ref.build_config_hash,
        )
    adjusted = adjusted.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    forward = forward_returns(adjusted)
    max_lookback = max(len(train_days) // 2, 5)

    # 四、多轮候选提出、静态筛查、评审与样本内估计
    ledger = TrialLedger(out_dir / "trials.jsonl")
    probe_symbols = universe[: min(len(universe), 5)]
    probe_panel = adjusted[
        adjusted["symbol"].isin(probe_symbols) & (adjusted["trade_date"] <= train_end)
    ].reset_index(drop=True)
    per_round_budget = config.candidate_budget // config.n_rounds
    trial_numbers: dict[str, int] = {}
    candidates: list[dict[str, Any]] = []
    survivors: list[FactorSpec] = []
    n_proposed_total = 0
    n_rejected_static_total = 0
    feedback: dict[str, Any] | None = None

    for round_no in range(config.n_rounds):
        emit("phase", f"候选提出（第 {round_no + 1} 轮）")
        context = ResearchContext(
            field_scope=config.field_scope,
            root_seed=derive_seed(config.root_seed, f"round-{round_no}"),
            feedback=feedback,
        )
        specs = generator.propose(context, per_round_budget)
        specs = [spec for spec in specs if spec.factor_id() not in trial_numbers]
        n_proposed_total += len(specs)
        for spec in specs:
            trial_numbers[spec.factor_id()] = ledger.register_planned(
                spec.factor_id(),
                {"generator": generator.generator_id, "spec": spec.tree, "round": round_no},
            )
        emit("result", "候选登记", n_proposed=len(specs), round=round_no)

        emit("phase", f"静态筛查（第 {round_no + 1} 轮）")
        screen = static_screen(
            specs,
            field_scope=frozenset(config.field_scope),
            probe_panel=probe_panel,
            max_lookback=max_lookback,
        )
        n_rejected_static_total += len(screen.rejected)
        round_records: list[dict[str, Any]] = []
        for factor_id, reason in screen.rejected:
            if factor_id in trial_numbers:
                ledger.register_realized(
                    trial_numbers[factor_id], "rejected_static", {"reason": reason}
                )
                round_records.append(
                    {"factor_id": factor_id, "outcome": "rejected_static"}
                )

        # 评审：只看到规格树，看不到任何验证统计（角色分离，防自证）。
        reviewed = screen.accepted
        if reviewer is not None and screen.accepted:
            emit("phase", f"候选评审（第 {round_no + 1} 轮）")
            verdicts = reviewer.review(screen.accepted)
            reviewed = []
            for spec in screen.accepted:
                approve, reason_code = verdicts[spec.factor_id()]
                if approve:
                    reviewed.append(spec)
                else:
                    ledger.register_realized(
                        trial_numbers[spec.factor_id()],
                        "rejected_review",
                        {"reason": reason_code},
                    )
                    round_records.append(
                        {"factor_id": spec.factor_id(), "outcome": "rejected_review"}
                    )
            emit("result", "评审完成", n_approved=len(reviewed))

        emit("phase", f"样本内估计（第 {round_no + 1} 轮）")
        for spec in reviewed:
            factor_id = spec.factor_id()
            stats = _ic_stats(spec, adjusted, forward, train_start, train_end)
            record: dict[str, Any] = {
                "factor_id": factor_id,
                "trial_no": trial_numbers[factor_id],
                "spec": spec.tree,
                "round": round_no,
                "train_ic_mean": stats["ic_mean"],
                "train_ic_t": stats["ic_t"],
                "train_n_days": stats["n_days"],
            }
            if stats["n_days"] < _MIN_IC_DAYS or abs(stats["ic_t"]) < config.gate.min_train_ic_t:
                record["outcome"] = "rejected_train"
                ledger.register_realized(
                    trial_numbers[factor_id],
                    "rejected_train",
                    {"ic_t": stats["ic_t"], "n_days": stats["n_days"]},
                )
            else:
                survivors.append(spec)
                record["outcome"] = "insample_pass"
            candidates.append(record)
            round_records.append(
                {
                    "factor_id": factor_id,
                    "outcome": record["outcome"],
                    "train_ic_mean": stats["ic_mean"],
                }
            )
        emit("result", "样本内完成", n_survivors=len(survivors), round=round_no)

        # 反馈只来自训练段结果，保留段信息不进入生成端。
        if round_no < config.n_rounds - 1:
            feedback = policy.digest({"candidates": round_records})

    # 七、规则冻结
    emit("phase", "规则冻结")
    freeze_path = out_dir / "freeze.json"
    freeze_hash = write_freeze_manifest(freeze_path, config.freeze_rules())
    emit("gate", "冻结完成", freeze_hash=freeze_hash)

    # 八、保留段验证（凭证 + 一次性消费）
    emit("phase", "保留段验证")
    verify_freeze(freeze_path, config.freeze_rules())
    gate_keeper = KernelGate()
    token = gate_keeper.issue("holdout_eval", run_id=config.run_id, freeze_hash=freeze_hash)
    survivor_ids = sorted(spec.factor_id() for spec in survivors)
    key = sealed_key(
        freeze_hash,
        config.dataset_fingerprint,
        (split.holdout_start, split.holdout_end),
        survivor_ids,
    )
    SealedLedger(sealed_root).consume(
        key, {"consumed_for": config.run_id, "n_candidates": len(survivor_ids)}
    )
    emit("gate", "封存消费", sealed_key=key)
    holdout_stats = _holdout_evaluation(
        survivors,
        adjusted,
        forward,
        holdout_start,
        holdout_end,
        token=token,
        gate_keeper=gate_keeper,
        run_id=config.run_id,
        freeze_hash=freeze_hash,
    )

    # 九、统计与成本门控（FDR 分母 = 全量 planned；模式 B 紧缩 q 减半）
    emit("phase", "门控")
    n_planned = ledger.total_planned()
    effective_fdr_q = config.gate.fdr_q * (0.5 if config.mode == "B" else 1.0)
    p_values = np.ones(n_planned, dtype="float64")
    holdout_by_trial: dict[int, dict[str, Any]] = {}
    for spec in survivors:
        factor_id = spec.factor_id()
        stats = holdout_stats[factor_id]
        holdout_by_trial[trial_numbers[factor_id]] = stats
        p_values[trial_numbers[factor_id] - 1] = stats["ic_p"]
    fdr_pass = bh_fdr(p_values, q=effective_fdr_q)
    costs = CostModel(
        commission_bps=config.gate.commission_bps,
        slippage_bps=config.gate.slippage_bps,
        stamp_tax_sell_bps=market.execution.stamp_tax_sell_bps,
    )
    final_pass: list[str] = []
    for record in candidates:
        factor_id = record["factor_id"]
        trial_no = record["trial_no"]
        if trial_no not in holdout_by_trial:
            continue
        stats = holdout_by_trial[trial_no]
        record["holdout_ic_mean"] = stats["ic_mean"]
        record["holdout_ic_p"] = stats["ic_p"]
        record["fdr_pass"] = bool(fdr_pass[trial_no - 1])
        if not record["fdr_pass"]:
            record["outcome"] = "rejected_fdr"
            ledger.register_realized(trial_no, "rejected_fdr", {"ic_p": stats["ic_p"]})
            continue
        spec = next(item for item in survivors if item.factor_id() == factor_id)
        backtest = _holdout_backtest(
            spec, adjusted, actions, market, costs, config, holdout_start, holdout_end
        )
        record["net_mean_daily_after_cost"] = backtest["net_mean_daily"]
        record["net_t_after_cost"] = backtest["net_t"]
        record["mean_turnover"] = backtest["mean_turnover"]
        record["n_suspect_dropped"] = backtest["n_suspect_dropped"]
        if backtest["net_t"] <= 0 or backtest["net_mean_daily"] <= 0:
            record["outcome"] = "rejected_cost"
            ledger.register_realized(trial_no, "rejected_cost", backtest)
            continue
        if config.gate.dsr_min > 0.0:
            dsr = deflated_sharpe_probability(
                backtest["net_sharpe_daily"],
                n_obs=backtest["n_days"],
                n_trials=n_planned,
                skewness=backtest["net_skew"],
                kurtosis=backtest["net_kurt"],
            )
            record["dsr_probability"] = dsr
            if dsr < config.gate.dsr_min:
                record["outcome"] = "rejected_fdr"
                ledger.register_realized(trial_no, "rejected_fdr", {"dsr": dsr})
                continue
        record["outcome"] = "accepted"
        final_pass.append(factor_id)
        ledger.register_realized(trial_no, "accepted", backtest)

    gate_report = {
        "run_id": config.run_id,
        "protocol_id": config.protocol_id(),
        "n_planned_total": n_planned,
        "fdr_q": config.gate.fdr_q,
        "effective_fdr_q": effective_fdr_q,
        "candidates": sorted(candidates, key=lambda item: item["trial_no"]),
        "final_pass": sorted(final_pass),
        "no_findings": len(final_pass) == 0,
        "feature_panels": [
            {"panel_id": ref.panel_id, "build_config_hash": ref.build_config_hash}
            for ref in config.feature_panels
        ],
    }
    atomic_write_json(out_dir / "gate_report.json", gate_report)
    feedback = policy.digest(gate_report)
    if feedback is not None:
        atomic_write_json(out_dir / "feedback.json", feedback)

    plan_written = False
    if final_pass:
        emit("phase", "交易计划")
        best_record = max(
            (record for record in candidates if record.get("outcome") == "accepted"),
            key=lambda record: record.get("net_t_after_cost", 0.0),
        )
        best_spec = next(
            spec for spec in survivors if spec.factor_id() == best_record["factor_id"]
        )
        plan = _build_trade_plan(
            best_spec,
            best_record,
            adjusted,
            market,
            config,
            freeze_hash=freeze_hash,
            sealed_key_value=key,
        )
        if plan is not None:
            atomic_write_json(out_dir / "trade_plan.json", plan.to_json())
            intents = compile_plan(plan, market.execution, notional=config.plan_notional)
            adapter = JournalingAdapter(out_dir / "order_intents.jsonl")
            for intent in intents:
                adapter.submit(intent)
            plan_written = True
            emit("artifact", "交易计划产出", plan_id=plan.plan_id, n_intents=len(intents))

    summary = {
        "run_id": config.run_id,
        "market_id": market.market_id,
        "trade_plan_written": plan_written,
        "mode": config.mode,
        "protocol_id": config.protocol_id(),
        "n_proposed": n_proposed_total,
        "n_rejected_static": n_rejected_static_total,
        "n_insample_pass": len(survivors),
        "n_final_pass": len(final_pass),
        "no_findings": len(final_pass) == 0,
        "freeze_hash": freeze_hash,
        "sealed_key": key,
    }
    atomic_write_json(out_dir / "summary.json", summary)
    emit(
        "result",
        "循环结束",
        n_final_pass=len(final_pass),
        no_findings=summary["no_findings"],
    )
    return summary


def _ic_stats(
    spec: FactorSpec,
    panel: pd.DataFrame,
    forward: pd.DataFrame,
    start: date,
    end: date,
) -> dict[str, Any]:
    values = evaluate(spec, panel)
    scores = panel[["symbol", "trade_date"]].assign(score=values)
    window = scores[(scores["trade_date"] >= start) & (scores["trade_date"] <= end)].dropna(
        subset=["score"]
    )
    ic = ic_series(window, forward)
    if len(ic) < 8:
        return {"ic_mean": 0.0, "ic_t": 0.0, "ic_p": 1.0, "n_days": len(ic)}
    result = newey_west_mean_t(ic.to_numpy())
    return {
        "ic_mean": result.mean,
        "ic_t": result.t_stat,
        "ic_p": result.p_value,
        "n_days": result.n_obs,
    }


def _holdout_evaluation(
    survivors: list[FactorSpec],
    panel: pd.DataFrame,
    forward: pd.DataFrame,
    start: date,
    end: date,
    *,
    token: CapabilityToken,
    gate_keeper: KernelGate,
    run_id: str,
    freeze_hash: str,
) -> dict[str, dict[str, Any]]:
    """保留段评估入口：先验凭证，后触碰保留段数据。"""
    gate_keeper.require(token, scope="holdout_eval", run_id=run_id, freeze_hash=freeze_hash)
    results: dict[str, dict[str, Any]] = {}
    for spec in survivors:
        results[spec.factor_id()] = _ic_stats(spec, panel, forward, start, end)
    return results


def _holdout_backtest(
    spec: FactorSpec,
    panel: pd.DataFrame,
    actions: pd.DataFrame,
    market: MarketSpec,
    costs: CostModel,
    config: RunConfig,
    start: date,
    end: date,
) -> dict[str, Any]:
    values = evaluate(spec, panel)
    scores = panel[["symbol", "trade_date"]].assign(score=values)
    scores = scores[(scores["trade_date"] >= start) & (scores["trade_date"] <= end)].dropna(
        subset=["score"]
    )
    report = run_backtest(scores, panel, actions, market, costs, top_k=config.gate.top_k)
    net = report.net_returns.to_numpy(dtype="float64")
    std = float(net.std(ddof=1))
    sharpe_daily = float(net.mean()) / std if std > 0 else 0.0
    if std > 0:
        centered = net - net.mean()
        skew = float((centered**3).mean() / std**3)
        kurt = float((centered**4).mean() / std**4)
    else:
        skew, kurt = 0.0, 3.0
    return {
        "net_mean_daily": report.net_mean_daily,
        "gross_mean_daily": report.gross_mean_daily,
        "net_t": report.net_newey_west.t_stat,
        "net_sharpe_daily": sharpe_daily,
        "net_skew": skew,
        "net_kurt": kurt,
        "n_days": int(report.n_days),
        "mean_turnover": report.mean_turnover,
        "n_suspect_dropped": report.n_suspect_dropped,
        "cost_adjusted": True,
    }


def _build_trade_plan(
    spec: FactorSpec,
    record: dict[str, Any],
    panel: pd.DataFrame,
    market: MarketSpec,
    config: RunConfig,
    *,
    freeze_hash: str,
    sealed_key_value: str,
) -> TradePlan | None:
    """由通过门控的最优候选构造交易计划；无可买标的时返回 None。

    选仓依据保留段最后一个交易日的因子得分与可交易性；预期净收益取
    保留段回测的成本后日均收益（bps 口径）；容量按 20 日均成交额与
    2% 参与率反推，成交额为估算值时随字段声明。
    """
    values = evaluate(spec, panel)
    scored = panel[["symbol", "trade_date", "close", "amount", "amount_is_estimated"]].assign(
        score=values
    )
    flagged = market.tradability.attach_flags(
        panel.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    )
    last_day = max(scored["trade_date"])
    latest = scored[scored["trade_date"] == last_day].dropna(subset=["score"])
    tradable = flagged[(flagged["trade_date"] == last_day) & flagged["buy_ok"]]["symbol"]
    latest = latest[latest["symbol"].isin(set(tradable))]
    latest = latest.sort_values("score", ascending=False).head(config.gate.top_k)
    if latest.empty:
        return None
    weight = 1.0 / config.gate.top_k
    window = scored[scored["trade_date"] >= sorted(scored["trade_date"].unique())[-20]]
    adv = window.groupby("symbol")["amount"].mean()
    amount_estimated = bool(panel["amount_is_estimated"].any())
    positions = []
    for row in latest.itertuples():
        capacity = estimate_capacity(
            {str(row.symbol): weight},
            adv,
            participation_cap=0.02,
            amount_is_estimated=amount_estimated,
        )
        positions.append(
            PlannedPosition(
                symbol=str(row.symbol),
                side="long",
                target_weight=weight,
                entry_reference_price=float(row.close),
                entry_style="next_session_close",
                exit_framework=(
                    f"最短持有 {market.execution.min_holding_days} 个交易日，"
                    "按信号衰减于次一交易日收盘再平衡（与回测口径一致）"
                ),
                expected_net_return_bps_after_cost=float(record["net_mean_daily_after_cost"])
                * 1e4,
                capacity_notional=capacity.capacity_notional,
                capacity_is_estimated=amount_estimated,
            )
        )
    plan_id = content_hash(
        "trade-plan", config.run_id, record["factor_id"], last_day.isoformat()
    )[:16]
    return TradePlan(
        plan_id=plan_id,
        market_id=market.market_id,
        as_of=last_day.isoformat(),
        positions=tuple(positions),
        cost_model_note=(
            f"预期净收益为成本后口径（佣金 {config.gate.commission_bps}bp、"
            f"滑点 {config.gate.slippage_bps}bp、卖出税费 "
            f"{market.execution.stamp_tax_sell_bps}bp）"
        ),
        provenance=Provenance(
            run_id=config.run_id,
            factor_ids=(str(record["factor_id"]),),
            gate_report_ref="gate_report.json",
            sealed_key=sealed_key_value,
            freeze_hash=freeze_hash,
        ),
    )
