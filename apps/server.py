"""HTTP 服务：JSON REST API 加前端静态托管，标准库实现，零框架依赖。

实时进度经事件流增量轮询（GET /api/runs/<id>/events?since=N），运行中
与运行后回放走同一路径。产物按白名单暴露，未知产物由前端以通用视图
渲染，后端不为新增产物类型改动框架。
"""

from __future__ import annotations

import json
import mimetypes
from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from alphaloop.infra.events import read_events
from alphaloop.infra.jsonio import read_json, read_jsonl

__all__ = ["AppContext", "create_server"]

_JSON_ARTIFACTS = frozenset(
    {"config", "audit", "gate_report", "summary", "trade_plan", "freeze", "feedback"}
)
_JSONL_ARTIFACTS = frozenset({"trials", "order_intents", "events"})


class AppContext:
    """服务依赖装配：各子系统可选，未装配的路由返回 503。"""

    def __init__(
        self,
        *,
        runs_dir: Path,
        static_dir: Path | None = None,
        retriever: Any = None,  # noqa: ANN401 - news.retrieval.Retriever，避免硬依赖
        qa_engine: Any = None,  # noqa: ANN401 - news.qa.QaEngine
        sentiment_panel_path: Path | None = None,
    ) -> None:
        self.runs_dir = runs_dir
        self.static_dir = static_dir
        self.retriever = retriever
        self.qa_engine = qa_engine
        self.sentiment_panel_path = sentiment_panel_path


def create_server(
    context: AppContext, *, host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    """构建 HTTP 服务实例（port=0 表示随机可用端口）。"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

        # ------------------------------------------------------------------
        def _send_json(self, payload: Any, *, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, status: int, message: str) -> None:
            self._send_json({"error": message}, status=status)

        # ------------------------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            segments = [segment for segment in parsed.path.split("/") if segment]
            try:
                if segments[:1] == ["api"]:
                    self._route_api_get(segments[1:], query)
                else:
                    self._serve_static(parsed.path)
            except BrokenPipeError:
                pass
            except Exception as error:  # noqa: BLE001 - 服务边界统一转 500
                self._send_error_json(500, f"内部错误: {error}")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            segments = [segment for segment in parsed.path.split("/") if segment]
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_error_json(400, "请求体不是合法 JSON")
                return
            try:
                if segments == ["api", "chat"]:
                    self._handle_chat(payload)
                else:
                    self._send_error_json(404, "未知接口")
            except Exception as error:  # noqa: BLE001
                self._send_error_json(500, f"内部错误: {error}")

        # ------------------------------------------------------------------
        def _route_api_get(self, segments: list[str], query: dict[str, str]) -> None:
            if segments == ["health"]:
                self._send_json({"status": "ok"})
            elif segments == ["runs"]:
                self._send_json({"runs": self._list_runs()})
            elif len(segments) == 3 and segments[0] == "runs" and segments[2] == "events":
                self._handle_events(segments[1], query)
            elif len(segments) == 3 and segments[0] == "runs" and segments[2] == "artifacts":
                self._handle_artifact_index(segments[1])
            elif len(segments) == 4 and segments[0] == "runs" and segments[2] == "artifact":
                self._handle_artifact(segments[1], segments[3])
            elif segments == ["news", "search"]:
                self._handle_news_search(query)
            elif segments == ["sentiment", "panel"]:
                self._handle_sentiment_panel()
            else:
                self._send_error_json(404, "未知接口")

        def _list_runs(self) -> list[dict[str, Any]]:
            runs: list[dict[str, Any]] = []
            if not context.runs_dir.is_dir():
                return runs
            for run_dir in sorted(context.runs_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                summary_path = run_dir / "summary.json"
                entry: dict[str, Any] = {"run_id": run_dir.name}
                if summary_path.exists():
                    entry["summary"] = read_json(summary_path)
                runs.append(entry)
            return runs

        def _run_dir(self, run_id: str) -> Path | None:
            if "/" in run_id or ".." in run_id or run_id.startswith("."):
                return None
            run_dir = context.runs_dir / run_id
            return run_dir if run_dir.is_dir() else None

        def _handle_events(self, run_id: str, query: dict[str, str]) -> None:
            run_dir = self._run_dir(run_id)
            if run_dir is None:
                self._send_error_json(404, f"运行 {run_id} 不存在")
                return
            since = int(query.get("since", "0"))
            events = read_events(run_dir / "events.jsonl", since=since)
            self._send_json({"events": events})

        def _handle_artifact_index(self, run_id: str) -> None:
            run_dir = self._run_dir(run_id)
            if run_dir is None:
                self._send_error_json(404, f"运行 {run_id} 不存在")
                return
            names = sorted(
                path.stem
                for path in run_dir.iterdir()
                if path.stem in (_JSON_ARTIFACTS | _JSONL_ARTIFACTS)
            )
            self._send_json({"artifacts": names})

        def _handle_artifact(self, run_id: str, name: str) -> None:
            run_dir = self._run_dir(run_id)
            if run_dir is None:
                self._send_error_json(404, f"运行 {run_id} 不存在")
                return
            if name in _JSON_ARTIFACTS and (run_dir / f"{name}.json").exists():
                self._send_json({"kind": name, "data": read_json(run_dir / f"{name}.json")})
            elif name in _JSONL_ARTIFACTS and (run_dir / f"{name}.jsonl").exists():
                self._send_json(
                    {"kind": name, "data": read_jsonl(run_dir / f"{name}.jsonl")}
                )
            else:
                self._send_error_json(404, f"产物 {name} 不存在或不在白名单内")

        def _handle_news_search(self, query: dict[str, str]) -> None:
            if context.retriever is None:
                self._send_error_json(503, "新闻检索未装配")
                return
            question = query.get("q", "").strip()
            if not question:
                self._send_error_json(400, "缺少查询词 q")
                return
            symbols = [query["symbol"]] if query.get("symbol") else None
            results = context.retriever.search(
                question,
                symbols=symbols,
                as_of=query.get("as_of"),
                top_k=int(query.get("top_k", "20")),
            )
            self._send_json({"results": [asdict(item) for item in results]})

        def _handle_sentiment_panel(self) -> None:
            if context.sentiment_panel_path is None or not context.sentiment_panel_path.exists():
                self._send_error_json(503, "情绪面板未装配")
                return
            import pandas as pd  # import-bypass: allow(重依赖延迟加载，仅此路由需要)

            frame = pd.read_parquet(context.sentiment_panel_path)
            # NaN 经 to_json 转为合法的 null，避免 json.dumps 产出裸 NaN 记号。
            records = json.loads(frame.to_json(orient="records"))
            self._send_json({"panel": records})

        def _handle_chat(self, payload: dict[str, Any]) -> None:
            if context.qa_engine is None:
                self._send_error_json(503, "问答引擎未装配")
                return
            question = str(payload.get("question", "")).strip()
            if not question:
                self._send_error_json(400, "缺少 question")
                return
            answer = context.qa_engine.answer(
                question,
                symbol=payload.get("symbol"),
                as_of=payload.get("as_of"),
            )
            if is_dataclass(answer) and not isinstance(answer, type):
                response: Any = asdict(answer)
            else:
                response = answer
            self._send_json({"answer": response})

        # ------------------------------------------------------------------
        def _serve_static(self, path: str) -> None:
            if context.static_dir is None:
                self._send_error_json(503, "前端静态资源未构建")
                return
            relative = path.lstrip("/") or "index.html"
            target = (context.static_dir / relative).resolve()
            if not str(target).startswith(str(context.static_dir.resolve())):
                self._send_error_json(403, "非法路径")
                return
            if not target.is_file():
                target = context.static_dir / "index.html"
            if not target.is_file():
                self._send_error_json(404, "资源不存在")
                return
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), Handler)
