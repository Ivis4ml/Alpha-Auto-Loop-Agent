/**
 * 采集状态视图：当前版本为静态说明页，不请求任何采集接口。
 *
 * 内容为合规核实结论摘要与存量语料的静态统计。采集控制能力
 * 待合规确认后再行提供。
 */

import { Badge } from "../components/Badge";

export function CollectStatusView() {
  return (
    <div className="collect-layout">
      <section className="card card-pad">
        <h2 className="section-title">合规核实结论</h2>
        <p style={{ margin: "0 0 var(--space-1)" }}>
          <Badge tone="warn" dot>
            存在实质合规风险
          </Badge>
        </p>
        <p style={{ margin: 0 }}>
          财联社服务条款核实结论为存在实质合规风险，恢复采集待用户确认，详见{" "}
          <code>docs/compliance/cls_terms_review.md</code>。
        </p>
      </section>

      <section>
        <h2 className="section-title">存量语料统计</h2>
        <div className="stat-grid">
          <div className="card stat-card">
            <div className="stat-value num">2020-01-01 至 2026-06-18</div>
            <div className="stat-label">覆盖区间</div>
          </div>
          <div className="card stat-card">
            <div className="stat-value num">约 89 万条</div>
            <div className="stat-label">存量规模</div>
          </div>
          <div className="card stat-card">
            <div className="stat-value num">约七成</div>
            <div className="stat-label">结构性覆盖上限（回填语料）</div>
          </div>
        </div>
      </section>

      <p className="muted">当前版本不提供采集控制接口，本页为静态说明。</p>
    </div>
  );
}
