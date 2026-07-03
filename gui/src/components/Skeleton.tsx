/** 骨架屏占位块。首屏数据未就绪时用于替代空白等待。 */
export function Skeleton({ width, height = 14 }: { width?: number | string; height?: number | string }) {
  return <div className="skeleton" style={{ width: width ?? "100%", height }} />;
}

/** 纵向排列的骨架屏组，模拟列表加载。 */
export function SkeletonList({ rows = 4 }: { rows?: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} width={`${100 - (index % 3) * 12}%`} />
      ))}
    </div>
  );
}
