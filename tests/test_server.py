"""HTTP 服务的接口测试：路由、事件轮询、产物白名单、问答与静态托管。"""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from alphaloop.infra.events import EventSink
from alphaloop.infra.jsonio import append_jsonl, atomic_write_json
from apps.server import AppContext, create_server


class StubQa:
    def answer(self, question: str, *, symbol: str | None = None, as_of: str | None = None):  # noqa: ANN201
        return {
            "text": f"关于 {symbol or '全市场'} 的回答",
            "citations": [],
            "evidence_mode": "insufficient",
            "as_of": as_of,
            "trace": {},
        }


@pytest.fixture()
def server_env(tmp_path: Path):  # noqa: ANN201
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run-001"
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / "summary.json", {"run_id": "run-001", "no_findings": True})
    atomic_write_json(run_dir / "gate_report.json", {"candidates": []})
    append_jsonl(run_dir / "trials.jsonl", {"trial_no": 1, "phase": "planned"})
    sink = EventSink(run_dir / "events.jsonl")
    sink.write({"kind": "phase", "title": "数据审计", "payload": {}})
    sink.write({"kind": "result", "title": "审计完成", "payload": {"n": 1}})
    (run_dir / "secret_notes.json").write_text("{}", encoding="utf-8")

    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>工作台</html>", encoding="utf-8")

    server = create_server(
        AppContext(runs_dir=runs_dir, static_dir=static_dir, qa_engine=StubQa())
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address
    server.shutdown()


def _get(address: tuple[str, int], path: str) -> tuple[int, Any]:
    connection = http.client.HTTPConnection(*address, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    body = response.read().decode("utf-8")
    connection.close()
    try:
        return response.status, json.loads(body)
    except json.JSONDecodeError:
        return response.status, body


def _post(address: tuple[str, int], path: str, payload: dict) -> tuple[int, Any]:
    connection = http.client.HTTPConnection(*address, timeout=10)
    connection.request(
        "POST", path, body=json.dumps(payload), headers={"Content-Type": "application/json"}
    )
    response = connection.getresponse()
    body = json.loads(response.read().decode("utf-8"))
    connection.close()
    return response.status, body


class TestServer:
    def test_health(self, server_env: tuple[str, int]) -> None:
        status, body = _get(server_env, "/api/health")
        assert status == 200
        assert body == {"status": "ok"}

    def test_runs_listing_with_summary(self, server_env: tuple[str, int]) -> None:
        status, body = _get(server_env, "/api/runs")
        assert status == 200
        assert body["runs"][0]["run_id"] == "run-001"
        assert body["runs"][0]["summary"]["no_findings"] is True

    def test_events_incremental_polling(self, server_env: tuple[str, int]) -> None:
        status, body = _get(server_env, "/api/runs/run-001/events?since=0")
        assert status == 200
        assert [event["seq"] for event in body["events"]] == [1, 2]
        status, body = _get(server_env, "/api/runs/run-001/events?since=1")
        assert [event["seq"] for event in body["events"]] == [2]

    def test_artifact_whitelist(self, server_env: tuple[str, int]) -> None:
        status, body = _get(server_env, "/api/runs/run-001/artifacts")
        assert status == 200
        assert "gate_report" in body["artifacts"]
        assert "secret_notes" not in body["artifacts"]
        status, body = _get(server_env, "/api/runs/run-001/artifact/gate_report")
        assert status == 200
        assert body["kind"] == "gate_report"
        status, _ = _get(server_env, "/api/runs/run-001/artifact/secret_notes")
        assert status == 404

    def test_path_traversal_rejected(self, server_env: tuple[str, int]) -> None:
        status, _ = _get(server_env, "/api/runs/..%2F..%2Fetc/events")
        assert status == 404

    def test_chat_route(self, server_env: tuple[str, int]) -> None:
        status, body = _post(
            server_env, "/api/chat", {"question": "有什么新闻", "symbol": "600519.SH"}
        )
        assert status == 200
        assert "600519.SH" in body["answer"]["text"]

    def test_chat_requires_question(self, server_env: tuple[str, int]) -> None:
        status, body = _post(server_env, "/api/chat", {})
        assert status == 400

    def test_news_search_unassembled_returns_503(self, server_env: tuple[str, int]) -> None:
        status, body = _get(server_env, "/api/news/search?q=test")
        assert status == 503

    def test_static_spa_fallback(self, server_env: tuple[str, int]) -> None:
        status, body = _get(server_env, "/some/client/route")
        assert status == 200
        assert "工作台" in body


class TestStoreCalendarView:
    def test_adapter_over_market_spec(self, cn_store) -> None:  # noqa: ANN001
        from datetime import date

        from apps.assembly import StoreCalendarView

        view = StoreCalendarView({"cn_ashare": cn_store.market})
        assert view.timezone("cn_ashare") == "Asia/Shanghai"
        days = view.trading_days("cn_ashare", date(2024, 1, 2), date(2024, 1, 10))
        assert days[0] == date(2024, 1, 2)
        cutoff = view.session_cutoff("cn_ashare", date(2024, 1, 2))
        assert (cutoff.hour, cutoff.minute) == (15, 0)
        assert view.next_trading_day("cn_ashare", date(2024, 1, 5)) == date(2024, 1, 8)
        with pytest.raises(KeyError, match="未装配"):
            view.timezone("mars")


class TestNewsRoutesCrossThread:
    def test_search_and_chat_from_server_threads(self, tmp_path: Path) -> None:
        """新闻库连接须可被服务工作线程使用（多线程 HTTP 下的真实路径）。"""
        from news.entity import EntityLinker, build_alias_entries
        from news.qa import QaEngine
        from news.retrieval import Retriever
        from news.store import NewsItem, NewsStore

        store = NewsStore(tmp_path / "news.db")
        store.upsert(
            [
                NewsItem(
                    source="synthetic",
                    publish_ts="2024-05-06T02:00:00Z",
                    visible_ts="2024-05-06T02:00:00Z",
                    visible_basis="publish",
                    title="贵州茅台业绩预增",
                    content="贵州茅台预计净利润增长。",
                    labels=(),
                    corpus_batch="backfill",
                )
            ]
        )
        entries, _ = build_alias_entries([("600519.SH", "贵州茅台")])
        linker = EntityLinker(entries)
        links = [
            (link.news_id, link.symbol, link.method, link.confidence, None, None)
            for news_id, title, content in store.iter_items()
            for link in linker.link(news_id, title, content)
        ]
        store.write_entities(links)
        retriever = Retriever(store)
        server = create_server(
            AppContext(
                runs_dir=tmp_path / "runs",
                retriever=retriever,
                qa_engine=QaEngine(retriever, linker),
            )
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, body = _get(
                server.server_address, "/api/news/search?q=%E8%8C%85%E5%8F%B0"
            )
            assert status == 200
            assert len(body["results"]) == 1
            status, body = _post(
                server.server_address,
                "/api/chat",
                {"question": "贵州茅台有什么新闻", "symbol": "600519.SH"},
            )
            assert status == 200
            assert body["answer"]["evidence_mode"] == "evidence"
        finally:
            server.shutdown()
