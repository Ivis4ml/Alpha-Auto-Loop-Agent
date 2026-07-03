/**
 * 工作台视图：左栏运行列表，中央为选中运行的 Agent 活动时间线。
 *
 * 事件流以 1.5 秒间隔增量轮询（携带上次最大 seq），运行中与运行后
 * 回放共用同一路径。"未发现"是诚实的合法结论，以中性而非失败样式呈现。
 */

import { useEffect, useRef, useState } from "react";

import { fetchEvents, type EventKind, type RunEntry, type RunEvent } from "../api";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { SkeletonList } from "../components/Skeleton";

const POLL_INTERVAL_MS = 1500;

// ---------------------------------------------------------------------------
// 事件负载辅助
// ---------------------------------------------------------------------------

/** 从事件负载中提取可读文本；无常见文本字段时序列化整个负载。 */
function payloadText(payload: Record<string, unknown>): string {
  for (const key of ["text", "message", "content", "detail"]) {
    if (typeof payload[key] === "string") {
      return payload[key] as string;
    }
  }
  if (Object.keys(payload).length === 0) {
    return "";
  }
  return JSON.stringify(payload, null, 2);
}

/** 负载的单行紧凑摘要，用于工具/结果行的附注。 */
function payloadSummary(payload: Record<string, unknown>): string {
  if (Object.keys(payload).length === 0) {
    return "";
  }
  const raw = JSON.stringify(payload);
  return raw.length > 160 ? `${raw.slice(0, 160)}…` : raw;
}

/** 判断闸门事件是否通过；负载中无可辨识字段时返回 null。 */
function gatePassed(payload: Record<string, unknown>): boolean | null {
  for (const key of ["passed", "pass", "ok", "accepted"]) {
    if (typeof payload[key] === "boolean") {
      return payload[key] as boolean;
    }
  }
  for (const key of ["outcome", "decision", "status", "result"]) {
    const value = payload[key];
    if (typeof value === "string") {
      if (/pass|accept|ok|通过/i.test(value)) {
        return true;
      }
      if (/reject|fail|拒绝/i.test(value)) {
        return false;
      }
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// 单条事件的渲染
// ---------------------------------------------------------------------------

const KIND_LABELS: Record<EventKind, string> = {
  phase: "阶段",
  think: "思考",
  tool: "工具",
  result: "结果",
  gate: "闸门",
  artifact: "产物",
  info: "信息",
  warn: "警告",
};

function EventRow({ event }: { event: RunEvent }) {
  switch (event.kind) {
    case "phase":
      return <div className="wb-phase">{event.title}</div>;
    case "think": {
      const body = payloadText(event.payload);
      return (
        <details className="wb-think">
          <summary>{event.title || "思考过程"}</summary>
          {body ? <div className="wb-think-body">{body}</div> : null}
        </details>
      );
    }
    case "gate": {
      const passed = gatePassed(event.payload);
      return (
        <div className="wb-row kind-gate">
          <span className="wb-row-tag">{KIND_LABELS.gate}</span>
          {passed === null ? (
            <Badge tone="neutral">闸门</Badge>
          ) : (
            <Badge tone={passed ? "pass" : "reject"} dot>
              {passed ? "通过" : "拒绝"}
            </Badge>
          )}
          <span className="wb-row-title">{event.title}</span>
        </div>
      );
    }
    case "artifact":
      return (
        <div className="wb-row kind-artifact">
          <span className="wb-row-tag">{KIND_LABELS.artifact}</span>
          <Badge tone="accent">{event.title}</Badge>
        </div>
      );
    case "tool":
    case "result": {
      const summary = payloadSummary(event.payload);
      return (
        <div className={`wb-row kind-${event.kind}`}>
          <span className="wb-row-tag">{KIND_LABELS[event.kind]}</span>
          <span className="wb-row-title">
            {event.title}
            {summary ? <span className="wb-payload"> {summary}</span> : null}
          </span>
        </div>
      );
    }
    default:
      // info 与 warn 共用普通行样式，warn 附加黄色底。
      return (
        <div className={`wb-row kind-${event.kind}`}>
          <span className="wb-row-tag">{KIND_LABELS[event.kind]}</span>
          <span className="wb-row-title">{event.title}</span>
        </div>
      );
  }
}

// ---------------------------------------------------------------------------
// 视图主体
// ---------------------------------------------------------------------------

interface WorkbenchViewProps {
  runs: RunEntry[];
  runsLoading: boolean;
  onRefreshRuns: () => void;
}

export function WorkbenchView({ runs, runsLoading, onRefreshRuns }: WorkbenchViewProps) {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // 首次载入后默认选中最后一个运行（目录序即时间序）。
  useEffect(() => {
    if (selectedRunId === null && runs.length > 0) {
      setSelectedRunId(runs[runs.length - 1].run_id);
    }
  }, [runs, selectedRunId]);

  // 事件流增量轮询：seq 游标保存在闭包中，切换运行时整体重置。
  useEffect(() => {
    if (selectedRunId === null) {
      return;
    }
    let cancelled = false;
    let maxSeq = 0;
    setEvents([]);
    setEventsError(null);

    const poll = async () => {
      try {
        const batch = await fetchEvents(selectedRunId, maxSeq);
        if (cancelled || batch.length === 0) {
          return;
        }
        maxSeq = batch.reduce((acc, item) => Math.max(acc, item.seq), maxSeq);
        setEvents((previous) => [...previous, ...batch]);
        setEventsError(null);
      } catch (error) {
        if (!cancelled) {
          setEventsError(error instanceof Error ? error.message : String(error));
        }
      }
    };

    void poll();
    const timer = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selectedRunId]);

  // 新事件到达时，若视口已接近底部则跟随滚动。
  useEffect(() => {
    const node = scrollRef.current;
    if (node === null) {
      return;
    }
    const nearBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 240;
    if (nearBottom) {
      node.scrollTop = node.scrollHeight;
    }
  }, [events]);

  const selected = runs.find((run) => run.run_id === selectedRunId) ?? null;
  const summary = selected?.summary;

  return (
    <div className="wb-layout">
      <aside className="wb-runlist">
        <div className="wb-runlist-head">
          <span className="section-title" style={{ margin: 0 }}>
            运行列表
          </span>
          <button type="button" className="btn" onClick={onRefreshRuns}>
            刷新
          </button>
        </div>
        <div className="wb-runlist-body">
          {runsLoading ? (
            <div style={{ padding: "var(--space-1)" }}>
              <SkeletonList rows={5} />
            </div>
          ) : runs.length === 0 ? (
            <EmptyState title="暂无运行" description="尚未产生任何研究运行记录。" />
          ) : (
            runs.map((run) => (
              <button
                key={run.run_id}
                type="button"
                className={`wb-run-item${run.run_id === selectedRunId ? " active" : ""}`}
                onClick={() => setSelectedRunId(run.run_id)}
              >
                <span className="wb-run-id">{run.run_id}</span>
                <span className="wb-run-badges">
                  {run.summary === undefined ? (
                    <Badge tone="neutral">进行中</Badge>
                  ) : run.summary.no_findings ? (
                    <Badge tone="neutral">未发现</Badge>
                  ) : (
                    <Badge tone="pass">通过 {run.summary.n_final_pass}</Badge>
                  )}
                  {run.summary?.trade_plan_written ? <Badge tone="accent">交易计划</Badge> : null}
                </span>
              </button>
            ))
          )}
        </div>
      </aside>

      <section className="wb-main">
        {selected === null ? (
          <div className="wb-empty">
            <EmptyState title="请选择一个运行" description="从左侧列表选择运行以查看 Agent 活动时间线。" />
          </div>
        ) : (
          <>
            {summary ? (
              <div className="wb-summary">
                <span>
                  市场 <span className="num">{summary.market_id}</span>
                </span>
                <span>模式 {summary.mode}</span>
                <span>协议 {summary.protocol_id}</span>
                <span>
                  提出 <span className="num">{summary.n_proposed}</span> / 静态拒绝{" "}
                  <span className="num">{summary.n_rejected_static}</span> / 样本内通过{" "}
                  <span className="num">{summary.n_insample_pass}</span> / 最终通过{" "}
                  <span className="num">{summary.n_final_pass}</span>
                </span>
                <span className="mono" title={summary.freeze_hash}>
                  冻结 {summary.freeze_hash.slice(0, 10)}
                </span>
              </div>
            ) : null}
            <div className="wb-timeline" ref={scrollRef}>
              {summary?.no_findings ? (
                <div className="wb-nofindings">
                  <Badge tone="neutral">未发现</Badge>
                  <span>本次运行没有因子通过全部闸门。诚实的零结果是合法结论，不代表流程失败。</span>
                </div>
              ) : null}
              {eventsError ? <p className="error-text">事件流读取失败：{eventsError}</p> : null}
              {events.length === 0 && eventsError === null ? (
                <p className="muted">暂无事件，等待运行产出……</p>
              ) : (
                events.map((event) => <EventRow key={event.seq} event={event} />)
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}
