/**
 * 通用 JSON 视图：任意数据格式化后展示。
 *
 * 作为产物渲染器注册表的回退项，未知 kind 一律由本组件渲染，
 * 因此新增产物类型不需要改动前端框架。
 */
export function JsonView({ data }: { data: unknown }) {
  return <pre className="json-view">{JSON.stringify(data, null, 2)}</pre>;
}
