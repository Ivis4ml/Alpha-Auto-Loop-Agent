/**
 * 应用根组件：左侧导航 + 顶栏 + 六个视图。
 *
 * 视图切换不引入路由库，由 useState 管理；视图在首次激活后保持挂载
 * （隐藏而非卸载），以保留会话内容与轮询进度。首屏并行拉取 health
 * 与 runs，未就绪时由各视图渲染骨架屏。
 */

import { useCallback, useEffect, useLayoutEffect, useState } from "react";

import { fetchHealth, fetchRuns, type RunEntry } from "./api";
import { Sidebar, type ViewId } from "./components/Sidebar";
import { ArtifactsView } from "./views/ArtifactsView";
import { ChatView } from "./views/ChatView";
import { CollectStatusView } from "./views/CollectStatusView";
import { NewsSearchView } from "./views/NewsSearchView";
import { SentimentView } from "./views/SentimentView";
import { WorkbenchView } from "./views/WorkbenchView";

type Theme = "light" | "dark";

const THEME_STORAGE_KEY = "alphaloop-theme";

/** 初始主题：优先取用户上次选择，否则跟随系统 prefers-color-scheme。 */
function initialTheme(): Theme {
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (stored === "light" || stored === "dark") {
    return stored;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

const VIEW_TITLES: Record<ViewId, string> = {
  workbench: "工作台",
  news: "新闻检索",
  chat: "对话问答",
  sentiment: "情绪面板",
  collect: "采集状态",
  artifacts: "研究产物",
};

/** 无自身内边距、由内部自行布局的视图。 */
const FULL_BLEED_VIEWS: ReadonlySet<ViewId> = new Set(["workbench", "chat"]);

export function App() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [view, setView] = useState<ViewId>("workbench");
  const [visited, setVisited] = useState<ReadonlySet<ViewId>>(new Set<ViewId>(["workbench"]));
  const [health, setHealth] = useState<"loading" | "ok" | "bad">("loading");
  const [runs, setRuns] = useState<RunEntry[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);

  // 主题写入根元素属性并持久化到 localStorage。
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  /**
   * 切换主题。先同步更新根元素属性再触发重渲染：图表组件在渲染期
   * 读取 CSS 变量的计算值，属性必须先于渲染更新，否则读到旧主题色。
   */
  const toggleTheme = () => {
    setTheme((previous) => {
      const next: Theme = previous === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = next;
      return next;
    });
  };

  const refreshRuns = useCallback(async () => {
    setRunsLoading(true);
    try {
      setRuns(await fetchRuns());
    } catch {
      // 运行列表读取失败时保留现有数据；健康指示灯已反映服务状态。
    } finally {
      setRunsLoading(false);
    }
  }, []);

  // 首屏并行拉取 health 与 runs。
  useEffect(() => {
    void (async () => {
      const [healthResult] = await Promise.allSettled([fetchHealth(), refreshRuns()]);
      setHealth(
        healthResult.status === "fulfilled" && healthResult.value.status === "ok" ? "ok" : "bad",
      );
    })();
  }, [refreshRuns]);

  const selectView = (next: ViewId) => {
    setView(next);
    setVisited((previous) => {
      if (previous.has(next)) {
        return previous;
      }
      const merged = new Set(previous);
      merged.add(next);
      return merged;
    });
  };

  /** 渲染单个视图容器：首次激活后保持挂载，仅以 display 控制可见性。 */
  const renderView = (id: ViewId, node: React.ReactNode) => {
    if (!visited.has(id)) {
      return null;
    }
    const fullBleed = FULL_BLEED_VIEWS.has(id);
    return (
      <div
        key={id}
        className={`view-container${fullBleed ? " no-pad" : ""}`}
        style={{ display: view === id ? undefined : "none" }}
      >
        {node}
      </div>
    );
  };

  return (
    <div className="app">
      <Sidebar active={view} onSelect={selectView} />
      <main className="app-main">
        <header className="topbar">
          <span className="topbar-title">{VIEW_TITLES[view]}</span>
          <div className="topbar-actions">
            <span className="health-pill">
              <span
                className={`health-dot${health === "ok" ? " ok" : health === "bad" ? " bad" : ""}`}
              />
              {health === "loading" ? "连接中" : health === "ok" ? "服务正常" : "服务不可达"}
            </span>
            <button type="button" className="theme-toggle" onClick={toggleTheme}>
              {theme === "light" ? "深色主题" : "浅色主题"}
            </button>
          </div>
        </header>
        {renderView(
          "workbench",
          <WorkbenchView runs={runs} runsLoading={runsLoading} onRefreshRuns={() => void refreshRuns()} />,
        )}
        {renderView("news", <NewsSearchView />)}
        {renderView("chat", <ChatView />)}
        {renderView("sentiment", <SentimentView active={view === "sentiment"} theme={theme} />)}
        {renderView("collect", <CollectStatusView />)}
        {renderView("artifacts", <ArtifactsView runs={runs} />)}
      </main>
    </div>
  );
}
