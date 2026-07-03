import type { ReactNode } from "react";

interface EmptyStateProps {
  title: string;
  description?: ReactNode;
}

/** 通用空态说明，用于子系统未装配、无数据等情形。 */
export function EmptyState({ title, description }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <div className="empty-state-title">{title}</div>
      {description ? <div className="empty-state-desc">{description}</div> : null}
    </div>
  );
}
