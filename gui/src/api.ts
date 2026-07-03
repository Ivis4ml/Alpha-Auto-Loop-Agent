/**
 * 后端 API 的统一调用层。
 *
 * 全部网络请求经由本文件收敛：类型定义、路径拼装与错误处理均在此完成。
 * 非 2xx 响应会尝试解析 {"error": ...} 结构，并抛出携带中文消息与
 * HTTP 状态码的 ApiError，供视图层区分"未装配"(503) 等情形。
 */

/** 携带 HTTP 状态码的错误类型；status 为 0 表示网络层失败。 */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** 判断错误是否为"子系统未装配"(HTTP 503)。 */
export function isUnavailable(error: unknown): boolean {
  return error instanceof ApiError && error.status === 503;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError(0, "网络请求失败，请确认服务是否已启动");
  }
  if (!response.ok) {
    let message = `请求失败 (HTTP ${response.status})`;
    try {
      const body = (await response.json()) as { error?: unknown };
      if (typeof body.error === "string" && body.error.length > 0) {
        message = body.error;
      }
    } catch {
      // 响应体不是 JSON 时保留默认消息。
    }
    throw new ApiError(response.status, message);
  }
  const raw = await response.text();
  try {
    return JSON.parse(raw) as T;
  } catch {
    // 后端以 json.dumps 序列化 pandas 数据时，缺失值会以裸 NaN 记号
    // 出现，导致严格解析失败；仅在此回退路径中将其替换为 null。
    const sanitized = raw
      .replace(/\bNaN\b/g, "null")
      .replace(/-?\bInfinity\b/g, "null");
    try {
      return JSON.parse(sanitized) as T;
    } catch {
      throw new ApiError(response.status, "响应不是合法 JSON");
    }
  }
}

// ---------------------------------------------------------------------------
// 类型定义
// ---------------------------------------------------------------------------

/** 单次运行的结果摘要，字段与后端 summary.json 一致。 */
export interface RunSummary {
  run_id: string;
  market_id: string;
  mode: string;
  protocol_id: string;
  n_proposed: number;
  n_rejected_static: number;
  n_insample_pass: number;
  n_final_pass: number;
  no_findings: boolean;
  freeze_hash: string;
  sealed_key: string;
  trade_plan_written: boolean;
}

/** 运行列表条目；运行尚未产出摘要时 summary 缺省。 */
export interface RunEntry {
  run_id: string;
  summary?: RunSummary;
}

/** 事件类别的封闭枚举，与后端事件流契约一致。 */
export type EventKind =
  | "phase"
  | "think"
  | "tool"
  | "result"
  | "gate"
  | "artifact"
  | "info"
  | "warn";

/** 事件流中的单条事件。 */
export interface RunEvent {
  schema_version: number;
  seq: number;
  kind: EventKind;
  title: string;
  payload: Record<string, unknown>;
}

/** 新闻检索结果条目。 */
export interface NewsResult {
  news_id: string;
  score: number;
  publish_ts: string;
  visible_ts: string;
  source: string;
  title: string;
  snippet: string;
  matched_symbols: string[];
}

/** 问答回答中的单条引用。 */
export interface Citation {
  index: number;
  news_id: string;
  source: string;
  publish_ts: string;
  visible_ts: string;
  title: string;
}

/** 问答回答体。evidence_mode 为 insufficient 时表示证据不足。 */
export interface ChatAnswer {
  text: string;
  citations: Citation[];
  evidence_mode: "evidence" | "insufficient";
  as_of: string | null;
  trace: unknown;
}

/** 情绪面板中单个 (标的, 交易日) 的打分记录；缺失值可能为 null。 */
export interface SentimentRow {
  market_id: string;
  symbol: string;
  trade_date: string;
  n_news: number;
  lex_score: number | null;
  llm_score: number | null;
  llm_n: number;
  blended_score: number | null;
  model_backfilled_share: number | null;
}

/** 产物响应体；data 的具体结构由 kind 决定，未知 kind 由通用视图渲染。 */
export interface ArtifactPayload {
  kind: string;
  data: unknown;
}

// ---------------------------------------------------------------------------
// 接口函数
// ---------------------------------------------------------------------------

/** GET /api/health，服务存活探测。 */
export async function fetchHealth(): Promise<{ status: string }> {
  return request<{ status: string }>("/api/health");
}

/** GET /api/runs，列出全部运行。 */
export async function fetchRuns(): Promise<RunEntry[]> {
  const body = await request<{ runs: RunEntry[] }>("/api/runs");
  return body.runs;
}

/** GET /api/runs/{id}/events?since=N，增量拉取事件流。 */
export async function fetchEvents(runId: string, since: number): Promise<RunEvent[]> {
  const body = await request<{ events: RunEvent[] }>(
    `/api/runs/${encodeURIComponent(runId)}/events?since=${since}`,
  );
  return body.events;
}

/** GET /api/runs/{id}/artifacts，列出运行的产物名称。 */
export async function fetchArtifactNames(runId: string): Promise<string[]> {
  const body = await request<{ artifacts: string[] }>(
    `/api/runs/${encodeURIComponent(runId)}/artifacts`,
  );
  return body.artifacts;
}

/** GET /api/runs/{id}/artifact/{name}，读取单个产物。 */
export async function fetchArtifact(runId: string, name: string): Promise<ArtifactPayload> {
  return request<ArtifactPayload>(
    `/api/runs/${encodeURIComponent(runId)}/artifact/${encodeURIComponent(name)}`,
  );
}

/** 新闻检索参数；asOf 采用 ISO 8601 字符串。 */
export interface NewsSearchParams {
  q: string;
  symbol?: string;
  asOf?: string;
  topK?: number;
}

/** GET /api/news/search，按时点约束检索新闻。 */
export async function searchNews(params: NewsSearchParams): Promise<NewsResult[]> {
  const query = new URLSearchParams({ q: params.q });
  if (params.symbol) query.set("symbol", params.symbol);
  if (params.asOf) query.set("as_of", params.asOf);
  if (params.topK) query.set("top_k", String(params.topK));
  const body = await request<{ results: NewsResult[] }>(`/api/news/search?${query.toString()}`);
  return body.results;
}

/** 对话问答请求体。 */
export interface ChatRequest {
  question: string;
  symbol?: string;
  as_of?: string;
}

/** POST /api/chat，基于新闻证据的问答。 */
export async function sendChat(payload: ChatRequest): Promise<ChatAnswer> {
  const body = await request<{ answer: ChatAnswer }>("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return body.answer;
}

/** GET /api/sentiment/panel，读取情绪面板全量数据。 */
export async function fetchSentimentPanel(): Promise<SentimentRow[]> {
  const body = await request<{ panel: SentimentRow[] }>("/api/sentiment/panel");
  return body.panel;
}
