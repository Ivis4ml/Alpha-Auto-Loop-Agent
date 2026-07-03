/** 将对象的一级字段渲染为键值对表格；嵌套值序列化为紧凑 JSON。 */

function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "null";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

export function KeyValueTable({ data }: { data: unknown }) {
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    return <pre className="json-view">{JSON.stringify(data, null, 2)}</pre>;
  }
  const entries = Object.entries(data as Record<string, unknown>);
  if (entries.length === 0) {
    return <p className="muted">对象为空。</p>;
  }
  return (
    <table className="kv-table">
      <tbody>
        {entries.map(([key, value]) => (
          <tr key={key}>
            <th className="mono">{key}</th>
            <td>{formatValue(value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
