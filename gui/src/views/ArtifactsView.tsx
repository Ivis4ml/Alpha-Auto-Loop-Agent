/**
 * 研究产物视图：选择运行后列出产物，按渲染器注册表分发渲染。
 *
 * 未注册的产物 kind 一律回退为格式化 JSON 视图，后端新增产物类型
 * 不需要前端框架改动。
 */

import { useEffect, useState } from "react";

import {
  fetchArtifact,
  fetchArtifactNames,
  type ArtifactPayload,
  type RunEntry,
} from "../api";
import { resolveRenderer } from "../components/artifact-renderers";
import { EmptyState } from "../components/EmptyState";
import { SkeletonList } from "../components/Skeleton";

/** 产物名称的中文标签；未收录的名称按原文显示。 */
const ARTIFACT_LABELS: Record<string, string> = {
  config: "配置",
  audit: "审计",
  gate_report: "门控报告",
  summary: "摘要",
  trade_plan: "交易计划",
  freeze: "冻结清单",
  feedback: "反馈摘要",
  trials: "试验记录",
  order_intents: "订单意图",
  events: "事件流",
};

interface ArtifactsViewProps {
  runs: RunEntry[];
}

export function ArtifactsView({ runs }: ArtifactsViewProps) {
  const [runId, setRunId] = useState<string>("");
  const [names, setNames] = useState<string[] | null>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [artifact, setArtifact] = useState<ArtifactPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 运行列表就绪后默认选中最后一个运行。
  useEffect(() => {
    if (runId === "" && runs.length > 0) {
      setRunId(runs[runs.length - 1].run_id);
    }
  }, [runs, runId]);

  // 切换运行时拉取产物名称列表。
  useEffect(() => {
    if (runId === "") {
      return;
    }
    let cancelled = false;
    setNames(null);
    setSelectedName(null);
    setArtifact(null);
    setError(null);
    (async () => {
      try {
        const list = await fetchArtifactNames(runId);
        if (!cancelled) {
          setNames(list);
          if (list.length > 0) {
            // 优先展示门控报告，其次摘要，否则取第一项。
            setSelectedName(
              list.includes("gate_report") ? "gate_report" : list.includes("summary") ? "summary" : list[0],
            );
          }
        }
      } catch (requestError) {
        if (!cancelled) {
          setError(requestError instanceof Error ? requestError.message : String(requestError));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  // 切换产物时拉取内容。
  useEffect(() => {
    if (runId === "" || selectedName === null) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setArtifact(null);
    setError(null);
    (async () => {
      try {
        const payload = await fetchArtifact(runId, selectedName);
        if (!cancelled) {
          setArtifact(payload);
        }
      } catch (requestError) {
        if (!cancelled) {
          setError(requestError instanceof Error ? requestError.message : String(requestError));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [runId, selectedName]);

  if (runs.length === 0) {
    return <EmptyState title="暂无运行" description="尚未产生任何研究运行，因而没有可查看的产物。" />;
  }

  const Renderer = artifact === null ? null : resolveRenderer(artifact.kind);

  return (
    <div>
      <div className="art-toolbar">
        <div className="field">
          <label className="field-label" htmlFor="art-run">
            运行
          </label>
          <select
            id="art-run"
            className="select mono"
            value={runId}
            onChange={(changeEvent) => setRunId(changeEvent.target.value)}
          >
            {runs.map((run) => (
              <option key={run.run_id} value={run.run_id}>
                {run.run_id}
              </option>
            ))}
          </select>
        </div>
        {names !== null && names.length > 0 ? (
          <div className="art-tabs">
            {names.map((name) => (
              <button
                key={name}
                type="button"
                className={`chip${name === selectedName ? " active" : ""}`}
                onClick={() => setSelectedName(name)}
                title={name}
              >
                {ARTIFACT_LABELS[name] ?? name}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {error !== null ? (
        <p className="error-text">产物读取失败：{error}</p>
      ) : names === null ? (
        <SkeletonList rows={4} />
      ) : names.length === 0 ? (
        <EmptyState title="该运行没有产物" description="运行可能尚未结束，或未写出任何白名单内的产物。" />
      ) : loading || artifact === null || Renderer === null ? (
        <SkeletonList rows={6} />
      ) : (
        <div className="card card-pad">
          <Renderer data={artifact.data} />
        </div>
      )}
    </div>
  );
}
