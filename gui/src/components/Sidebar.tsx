/** 左侧固定窄导航栏：六个视图的切换入口。 */

import type { ReactNode } from "react";

export type ViewId =
  | "workbench"
  | "news"
  | "chat"
  | "sentiment"
  | "collect"
  | "artifacts";

interface NavEntry {
  id: ViewId;
  label: string;
  icon: ReactNode;
}

/** 16px 线性图标的公共属性。 */
const iconProps = {
  width: 15,
  height: 15,
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.3,
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

const NAV_ENTRIES: NavEntry[] = [
  {
    id: "workbench",
    label: "工作台",
    icon: (
      <svg {...iconProps}>
        <rect x="2" y="2" width="12" height="12" rx="1.5" />
        <path d="M2 6h12M6 6v8" />
      </svg>
    ),
  },
  {
    id: "news",
    label: "新闻检索",
    icon: (
      <svg {...iconProps}>
        <circle cx="7" cy="7" r="4.5" />
        <path d="M10.5 10.5 14 14" />
      </svg>
    ),
  },
  {
    id: "chat",
    label: "对话问答",
    icon: (
      <svg {...iconProps}>
        <path d="M2 3.5h12v7.5H6L2.5 14V3.5Z" />
      </svg>
    ),
  },
  {
    id: "sentiment",
    label: "情绪面板",
    icon: (
      <svg {...iconProps}>
        <rect x="2" y="2" width="5" height="5" rx="1" />
        <rect x="9" y="2" width="5" height="5" rx="1" />
        <rect x="2" y="9" width="5" height="5" rx="1" />
        <rect x="9" y="9" width="5" height="5" rx="1" />
      </svg>
    ),
  },
  {
    id: "collect",
    label: "采集状态",
    icon: (
      <svg {...iconProps}>
        <ellipse cx="8" cy="4" rx="5.5" ry="2" />
        <path d="M2.5 4v8c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2V4" />
        <path d="M2.5 8c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2" />
      </svg>
    ),
  },
  {
    id: "artifacts",
    label: "研究产物",
    icon: (
      <svg {...iconProps}>
        <path d="M4 2h6l3 3v9H4V2Z" />
        <path d="M10 2v3h3M6.5 8.5h4M6.5 11h4" />
      </svg>
    ),
  },
];

interface SidebarProps {
  active: ViewId;
  onSelect: (view: ViewId) => void;
}

export function Sidebar({ active, onSelect }: SidebarProps) {
  return (
    <nav className="app-nav">
      <div className="nav-brand">AlphaLoop</div>
      {NAV_ENTRIES.map((entry) => (
        <button
          key={entry.id}
          type="button"
          className={`nav-item${entry.id === active ? " active" : ""}`}
          onClick={() => onSelect(entry.id)}
        >
          <span className="nav-icon">{entry.icon}</span>
          {entry.label}
        </button>
      ))}
    </nav>
  );
}
