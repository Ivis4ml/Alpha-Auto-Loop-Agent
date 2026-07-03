/**
 * 产物渲染器注册表。
 *
 * 按产物 kind 查找对应的渲染组件；未注册的 kind 一律回退为格式化
 * JSON 视图，因此后端新增产物类型时前端框架不需要任何改动。
 */

import type { ComponentType } from "react";

import { Badge, type BadgeTone } from "./Badge";
import { JsonView } from "./JsonView";
import { JsonlTable } from "./JsonlTable";
import { KeyValueTable } from "./KeyValueTable";

export type ArtifactRenderer = ComponentType<{ data: unknown }>;

// ---------------------------------------------------------------------------
// 数据提取辅助：产物字段做前向兼容处理，主字段名以后端数据类为准。
// ---------------------------------------------------------------------------

type Row = Record<string, unknown>;

/** 从产物数据中取出记录数组：本身是数组，或对象中指定键下的数组。 */
function extractRows(data: unknown, keys: string[]): Row[] | null {
  if (Array.isArray(data)) {
    return data as Row[];
  }
  if (typeof data === "object" && data !== null) {
    const record = data as Row;
    for (const key of keys) {
      if (Array.isArray(record[key])) {
        return record[key] as Row[];
      }
    }
  }
  return null;
}

/** 依次尝试候选字段名，返回首个数值。 */
function pickNumber(row: Row, keys: string[]): number | null {
  for (const key of keys) {
    const value = row[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }
  return null;
}

function pickString(row: Row, keys: string[]): string | null {
  for (const key of keys) {
    const value = row[key];
    if (typeof value === "string" && value.length > 0) {
      return value;
    }
  }
  return null;
}

function pickBoolean(row: Row, keys: string[]): boolean | null {
  for (const key of keys) {
    const value = row[key];
    if (typeof value === "boolean") {
      return value;
    }
  }
  return null;
}

function formatNumber(value: number | null, digits: number): string {
  return value === null ? "" : value.toFixed(digits);
}

// ---------------------------------------------------------------------------
// gate_report：候选因子表格
// ---------------------------------------------------------------------------

/** outcome 字符串到徽标色调的映射；未知取值渲染为中性。 */
function outcomeTone(outcome: string): BadgeTone {
  if (outcome === "accepted") {
    return "pass";
  }
  if (outcome === "insample_pass") {
    return "accent";
  }
  if (outcome.startsWith("rejected")) {
    return "reject";
  }
  return "neutral";
}

function GateReportView({ data }: { data: unknown }) {
  const rows = extractRows(data, ["candidates", "rows", "items"]);
  if (rows === null) {
    return <JsonView data={data} />;
  }
  if (rows.length === 0) {
    return <p className="muted">本次运行没有候选因子记录。</p>;
  }
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th className="num">序号</th>
            <th>因子标识</th>
            <th>结局</th>
            <th className="num">训练 IC</th>
            <th className="num">留出 IC</th>
            <th className="num">成本后 t 值</th>
            <th>FDR</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const factorId = pickString(row, ["factor_id", "id"]) ?? "";
            const outcome = pickString(row, ["outcome", "status"]) ?? "";
            const fdrPass = pickBoolean(row, ["fdr_pass"]);
            return (
              <tr key={`${factorId}-${index}`}>
                <td className="num muted">{pickNumber(row, ["trial_no"]) ?? index + 1}</td>
                <td>
                  <span className="truncate-id" title={factorId}>
                    {factorId}
                  </span>
                </td>
                <td>{outcome ? <Badge tone={outcomeTone(outcome)}>{outcome}</Badge> : null}</td>
                <td className="num">
                  {formatNumber(pickNumber(row, ["train_ic_mean", "train_ic"]), 4)}
                </td>
                <td className="num">
                  {formatNumber(pickNumber(row, ["holdout_ic_mean", "holdout_ic"]), 4)}
                </td>
                <td className="num">
                  {formatNumber(pickNumber(row, ["net_t_after_cost", "net_t"]), 2)}
                </td>
                <td>
                  {fdrPass === null ? null : (
                    <Badge tone={fdrPass ? "pass" : "reject"} dot>
                      {fdrPass ? "通过" : "未过"}
                    </Badge>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// trade_plan：持仓卡片
// ---------------------------------------------------------------------------

function TradePlanView({ data }: { data: unknown }) {
  const positions = extractRows(data, ["positions", "holdings"]);
  if (positions === null) {
    return <JsonView data={data} />;
  }
  const plan = (typeof data === "object" && data !== null ? data : {}) as Row;
  const costNote = pickString(plan, ["cost_model_note"]);
  return (
    <div>
      <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", marginBottom: "var(--space-2)" }}>
        {pickString(plan, ["plan_id"]) ? (
          <span className="mono muted">计划 {pickString(plan, ["plan_id"])}</span>
        ) : null}
        {pickString(plan, ["market_id"]) ? (
          <Badge tone="neutral">{pickString(plan, ["market_id"])}</Badge>
        ) : null}
        {pickString(plan, ["as_of"]) ? (
          <span className="muted num">基准时点 {pickString(plan, ["as_of"])}</span>
        ) : null}
      </div>
      {positions.length === 0 ? (
        <p className="muted">计划不含任何仓位。</p>
      ) : (
        <div className="plan-grid">
          {positions.map((position, index) => {
            const symbol = pickString(position, ["symbol", "ticker"]) ?? `#${index + 1}`;
            const side = pickString(position, ["side"]);
            const weight = pickNumber(position, ["target_weight", "weight"]);
            const price = pickNumber(position, ["entry_reference_price", "ref_price", "price"]);
            const netBps = pickNumber(position, [
              "expected_net_return_bps_after_cost",
              "expected_net_bps",
            ]);
            const capacity = pickNumber(position, ["capacity_notional", "capacity"]);
            const estimated = pickBoolean(position, ["capacity_is_estimated"]);
            return (
              <div className="card plan-card" key={`${symbol}-${index}`}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span className="plan-symbol">{symbol}</span>
                  {side ? (
                    <Badge tone={side === "long" ? "accent" : "warn"}>
                      {side === "long" ? "多头" : "空头"}
                    </Badge>
                  ) : null}
                </div>
                <div className="plan-row">
                  <span>目标权重</span>
                  <span className="num">{weight === null ? "" : `${(weight * 100).toFixed(2)}%`}</span>
                </div>
                <div className="plan-row">
                  <span>参考价</span>
                  <span className="num">{formatNumber(price, 2)}</span>
                </div>
                <div className="plan-row">
                  <span>预期净收益</span>
                  <span className="num">{netBps === null ? "" : `${netBps.toFixed(1)} bps`}</span>
                </div>
                <div className="plan-row">
                  <span>容量{estimated ? "（估算）" : ""}</span>
                  <span className="num">
                    {capacity === null ? "" : capacity.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
      {costNote ? <p className="muted" style={{ marginTop: "var(--space-2)" }}>成本口径：{costNote}</p> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 注册表
// ---------------------------------------------------------------------------

const registry: Record<string, ArtifactRenderer> = {
  gate_report: GateReportView,
  trade_plan: TradePlanView,
  summary: KeyValueTable,
  audit: KeyValueTable,
  config: KeyValueTable,
  trials: JsonlTable,
  order_intents: JsonlTable,
  events: JsonlTable,
};

/** 按 kind 解析渲染器；未注册的 kind 回退为格式化 JSON 视图。 */
export function resolveRenderer(kind: string): ArtifactRenderer {
  return registry[kind] ?? JsonView;
}
