/**
 * 新闻检索视图：按查询词、标的与 as_of 时点检索存量新闻。
 *
 * as_of 语义为可见性截止：时点之后才可见的新闻不会出现在结果中。
 * 新闻子系统未装配（HTTP 503）时显示空态说明。
 */

import { useState, type FormEvent } from "react";

import { isUnavailable, searchNews, type NewsResult } from "../api";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { SkeletonList } from "../components/Skeleton";

/** 将 datetime-local 的值补齐秒位，转为 ISO 8601 字符串。 */
function toIsoTimestamp(local: string): string {
  return local.length === 16 ? `${local}:00` : local;
}

export function NewsSearchView() {
  const [query, setQuery] = useState("");
  const [symbol, setSymbol] = useState("");
  const [asOf, setAsOf] = useState("");
  const [results, setResults] = useState<NewsResult[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (formEvent: FormEvent) => {
    formEvent.preventDefault();
    if (query.trim() === "") {
      return;
    }
    setLoading(true);
    setError(null);
    setUnavailable(false);
    try {
      const items = await searchNews({
        q: query.trim(),
        symbol: symbol.trim() || undefined,
        asOf: asOf ? toIsoTimestamp(asOf) : undefined,
      });
      setResults(items);
    } catch (requestError) {
      setResults(null);
      if (isUnavailable(requestError)) {
        setUnavailable(true);
      } else {
        setError(requestError instanceof Error ? requestError.message : String(requestError));
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <form className="news-form" onSubmit={(e) => void handleSubmit(e)}>
        <div className="field" style={{ flex: "1 1 260px" }}>
          <label className="field-label" htmlFor="news-query">
            查询词
          </label>
          <input
            id="news-query"
            className="input"
            value={query}
            onChange={(changeEvent) => setQuery(changeEvent.target.value)}
            placeholder="例如：回购 增持 减持"
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="news-symbol">
            标的代码（可选）
          </label>
          <input
            id="news-symbol"
            className="input"
            value={symbol}
            onChange={(changeEvent) => setSymbol(changeEvent.target.value)}
            placeholder="例如 600519.SH"
            style={{ width: 160 }}
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="news-asof">
            as_of 时点（可选）
          </label>
          <input
            id="news-asof"
            className="input"
            type="datetime-local"
            value={asOf}
            onChange={(changeEvent) => setAsOf(changeEvent.target.value)}
          />
          <span className="field-hint">时点之后可见的新闻不会出现</span>
        </div>
        <button type="submit" className="btn btn-primary" disabled={loading || query.trim() === ""}>
          检索
        </button>
      </form>

      {loading ? (
        <div style={{ maxWidth: 880 }}>
          <SkeletonList rows={6} />
        </div>
      ) : unavailable ? (
        <EmptyState
          title="新闻子系统未装配"
          description="当前服务实例未装配新闻检索组件。请在启动服务时装配新闻索引后重试。"
        />
      ) : error !== null ? (
        <p className="error-text">检索失败：{error}</p>
      ) : results === null ? (
        <p className="muted">输入查询词后开始检索。</p>
      ) : results.length === 0 ? (
        <EmptyState title="没有匹配结果" description="请尝试更换查询词、放宽标的或调整 as_of 时点。" />
      ) : (
        <div className="news-list">
          {results.map((item) => (
            <article className="card news-item" key={item.news_id}>
              <div className="news-meta">
                <span>发布 {item.publish_ts}</span>
                <span>可见 {item.visible_ts}</span>
                <span>{item.source}</span>
                <span>相关度 {item.score.toFixed(3)}</span>
              </div>
              <h3 className="news-item-title">{item.title}</h3>
              {item.snippet ? <p className="news-snippet">{item.snippet}</p> : null}
              {item.matched_symbols.length > 0 ? (
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                  {item.matched_symbols.map((matched) => (
                    <Badge key={matched} tone="accent">
                      {matched}
                    </Badge>
                  ))}
                </div>
              ) : null}
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
