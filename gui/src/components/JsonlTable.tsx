import { JsonView } from "./JsonView";

/** 单元格显示的最大字符数，超出部分截断并保留 title 提示全文。 */
const CELL_LIMIT = 80;

function formatCell(value: unknown): { text: string; full?: string } {
  if (value === null || value === undefined) {
    return { text: "" };
  }
  const raw =
    typeof value === "string" || typeof value === "number" || typeof value === "boolean"
      ? String(value)
      : JSON.stringify(value);
  if (raw.length > CELL_LIMIT) {
    return { text: `${raw.slice(0, CELL_LIMIT)}…`, full: raw };
  }
  return { text: raw };
}

/**
 * 将 JSONL 记录数组渲染为表格。列为全部记录键名的并集，
 * 保持首次出现的顺序；记录不是对象时回退为 JSON 视图。
 */
export function JsonlTable({ data }: { data: unknown }) {
  if (!Array.isArray(data)) {
    return <JsonView data={data} />;
  }
  if (data.length === 0) {
    return <p className="muted">暂无记录。</p>;
  }
  if (data.some((row) => typeof row !== "object" || row === null || Array.isArray(row))) {
    return <JsonView data={data} />;
  }
  const rows = data as Record<string, unknown>[];
  const columns: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!columns.includes(key)) {
        columns.push(key);
      }
    }
  }
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th className="num">#</th>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              <td className="num muted">{index + 1}</td>
              {columns.map((column) => {
                const cell = formatCell(row[column]);
                return (
                  <td key={column} title={cell.full}>
                    {cell.text}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
