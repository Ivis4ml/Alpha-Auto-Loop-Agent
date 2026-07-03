import type { ReactNode } from "react";

/** 徽标的语义色调；语义色仅用于通过/拒绝与警示。 */
export type BadgeTone = "neutral" | "accent" | "pass" | "reject" | "warn";

interface BadgeProps {
  tone?: BadgeTone;
  /** 是否在文本前显示同色圆点（用于闸门通过/拒绝等状态）。 */
  dot?: boolean;
  children: ReactNode;
}

/** 圆角小徽标，用于状态与标签的紧凑展示。 */
export function Badge({ tone = "neutral", dot = false, children }: BadgeProps) {
  return (
    <span className={`badge tone-${tone}`}>
      {dot ? <span className="badge-dot" /> : null}
      {children}
    </span>
  );
}
