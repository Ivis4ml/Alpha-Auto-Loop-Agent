/**
 * 情绪面板视图：标的 x 日期的情绪热力图。
 *
 * 色值为 blended_score，采用蓝（正）与橙（负）的发散色板替代红绿，
 * 以兼顾色觉可用性；色板端点已通过 CVD 分离度与对比度校验。
 * 图注固定披露历史批量打分为模型回算值及其覆盖上限。
 */

import { useEffect, useMemo, useRef, useState } from "react";

import * as echarts from "echarts/core";
import { HeatmapChart } from "echarts/charts";
import { GridComponent, TooltipComponent, VisualMapComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import ReactEChartsCore from "echarts-for-react/lib/core";

import { fetchSentimentPanel, isUnavailable, type SentimentRow } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Skeleton } from "../components/Skeleton";

echarts.use([HeatmapChart, GridComponent, TooltipComponent, VisualMapComponent, CanvasRenderer]);

/** 读取设计令牌的当前值；颜色仍集中定义于 theme.css。 */
function cssToken(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

interface SentimentViewProps {
  /** 视图是否处于激活状态；从隐藏切换为可见时需要重算图表尺寸。 */
  active: boolean;
  /** 当前主题标识；主题切换时图表颜色随令牌重建。 */
  theme: "light" | "dark";
}

export function SentimentView({ active, theme }: SentimentViewProps) {
  const [rows, setRows] = useState<SentimentRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string> | null>(null);
  const chartRef = useRef<ReactEChartsCore>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const panel = await fetchSentimentPanel();
        if (!cancelled) {
          setRows(panel);
        }
      } catch (requestError) {
        if (cancelled) {
          return;
        }
        if (isUnavailable(requestError)) {
          setUnavailable(true);
        } else {
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
  }, []);

  // 视图从隐藏容器切换为可见时，容器宽度才可测量，需要触发一次重算。
  useEffect(() => {
    if (active) {
      chartRef.current?.getEchartsInstance().resize();
    }
  }, [active]);

  const allSymbols = useMemo(
    () => (rows === null ? [] : Array.from(new Set(rows.map((row) => row.symbol))).sort()),
    [rows],
  );
  const activeSymbols = useMemo(
    () => (selected === null ? allSymbols : allSymbols.filter((symbol) => selected.has(symbol))),
    [allSymbols, selected],
  );

  const toggleSymbol = (symbol: string) => {
    setSelected((previous) => {
      const next = new Set(previous ?? allSymbols);
      if (next.has(symbol)) {
        next.delete(symbol);
      } else {
        next.add(symbol);
      }
      return next;
    });
  };

  const option = useMemo(() => {
    if (rows === null || activeSymbols.length === 0) {
      return null;
    }
    const dates = Array.from(new Set(rows.map((row) => row.trade_date))).sort();
    const symbolIndex = new Map(activeSymbols.map((symbol, index) => [symbol, index]));
    const dateIndex = new Map(dates.map((date, index) => [date, index]));
    const detail = new Map<string, SentimentRow>();
    const cells: Array<[number, number, number]> = [];
    let maxAbs = 0;
    for (const row of rows) {
      const y = symbolIndex.get(row.symbol);
      const x = dateIndex.get(row.trade_date);
      // 混合得分缺失的记录不参与绘制。
      if (y === undefined || x === undefined || typeof row.blended_score !== "number") {
        continue;
      }
      cells.push([x, y, row.blended_score]);
      detail.set(`${row.symbol}|${row.trade_date}`, row);
      maxAbs = Math.max(maxAbs, Math.abs(row.blended_score));
    }
    if (maxAbs === 0) {
      maxAbs = 1;
    }
    const textColor = cssToken("--color-text-secondary");
    const mutedColor = cssToken("--color-text-tertiary");
    const surface = cssToken("--color-surface");
    const border = cssToken("--color-border");
    return {
      animation: false,
      grid: { left: 96, right: 24, top: 16, bottom: 88 },
      xAxis: {
        type: "category" as const,
        data: dates,
        axisLine: { lineStyle: { color: border } },
        axisTick: { show: false },
        axisLabel: { color: mutedColor, fontSize: 11 },
      },
      yAxis: {
        type: "category" as const,
        data: activeSymbols,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: textColor, fontSize: 11, fontFamily: "SF Mono, Menlo, monospace" },
      },
      visualMap: {
        type: "continuous" as const,
        min: -maxAbs,
        max: maxAbs,
        calculable: true,
        orient: "horizontal" as const,
        left: "center",
        bottom: 8,
        itemWidth: 10,
        itemHeight: 140,
        precision: 2,
        textStyle: { color: mutedColor, fontSize: 11 },
        // 发散色板：负值为橙、中性为灰、正值为蓝（令牌见 theme.css）。
        inRange: { color: [cssToken("--viz-neg"), cssToken("--viz-mid"), cssToken("--viz-pos")] },
      },
      tooltip: {
        backgroundColor: surface,
        borderColor: border,
        textStyle: { color: cssToken("--color-text"), fontSize: 12 },
        formatter: (params: { value: [number, number, number] }) => {
          const [x, y] = params.value;
          const row = detail.get(`${activeSymbols[y]}|${dates[x]}`);
          if (row === undefined) {
            return "";
          }
          const score = (value: number | null) => (value === null ? "无" : value.toFixed(3));
          const share =
            row.model_backfilled_share === null
              ? "无"
              : `${(row.model_backfilled_share * 100).toFixed(0)}%`;
          return [
            `<b>${row.symbol}</b> ${row.trade_date}`,
            `混合得分 ${score(row.blended_score)}`,
            `词典得分 ${score(row.lex_score)} / 模型得分 ${score(row.llm_score)}`,
            `新闻数 ${row.n_news} / 模型回算占比 ${share}`,
          ].join("<br/>");
        },
      },
      series: [
        {
          type: "heatmap" as const,
          data: cells,
          // 单元格之间以表面色描边形成 2px 间隔，保持发散色可读。
          itemStyle: { borderColor: surface, borderWidth: 2, borderRadius: 2 },
          emphasis: { itemStyle: { borderColor: cssToken("--color-accent"), borderWidth: 1 } },
        },
      ],
    };
    // theme 参与依赖：主题切换后令牌值变化，需要重建配置。
  }, [rows, activeSymbols, theme]);

  if (loading) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <Skeleton width={320} height={24} />
        <Skeleton height={360} />
      </div>
    );
  }
  if (unavailable) {
    return (
      <EmptyState
        title="情绪面板未装配"
        description="当前服务实例未装配情绪面板数据。请在启动服务时提供情绪面板文件后重试。"
      />
    );
  }
  if (error !== null) {
    return <p className="error-text">情绪面板读取失败：{error}</p>;
  }
  if (rows === null || rows.length === 0) {
    return <EmptyState title="暂无情绪数据" description="情绪面板尚无任何打分记录。" />;
  }

  const chartHeight = Math.max(360, 140 + activeSymbols.length * 26);

  return (
    <div>
      <div className="sent-toolbar">
        <button
          type="button"
          className={`chip${selected === null || selected.size === allSymbols.length ? " active" : ""}`}
          onClick={() => setSelected(null)}
        >
          全部
        </button>
        {allSymbols.map((symbol) => {
          const isOn = selected === null || selected.has(symbol);
          return (
            <button
              key={symbol}
              type="button"
              className={`chip${isOn ? " active" : ""}`}
              onClick={() => toggleSymbol(symbol)}
            >
              {symbol}
            </button>
          );
        })}
      </div>
      {activeSymbols.length === 0 ? (
        <EmptyState title="未选择标的" description="请至少选择一个标的以绘制热力图。" />
      ) : option !== null ? (
        <div className="card card-pad">
          <ReactEChartsCore
            ref={chartRef}
            echarts={echarts}
            option={option}
            notMerge
            style={{ height: chartHeight, width: "100%" }}
          />
        </div>
      ) : null}
      <p className="sent-caption">
        图注：历史批量打分为模型回算值；回填语料存在约七成的结构性覆盖上限，缺失日期不代表无相关新闻。
      </p>
    </div>
  );
}
