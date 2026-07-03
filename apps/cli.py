"""命令行入口。

当前提供 run-loop 子命令：按运行配置文件在指定数据根目录上执行一轮
基线研究循环。评估框架以子进程方式调用本入口，实现注入参数与研究
过程的进程级隔离。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from alphaloop.loop.config import FeaturePanelRef, GateConfig, RunConfig, SplitConfig
from alphaloop.loop.engine import run_research_loop
from apps.assembly import build_generator, build_policy, build_store
from apps.server import AppContext, create_server
from news.entity import EntityLinker, build_alias_entries
from news.qa import QaEngine
from news.retrieval import Retriever
from news.store import NewsStore

__all__ = ["main"]


def _load_run_config(path: Path) -> RunConfig:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("protocol_id", None)
    split = SplitConfig(**payload.pop("split"))
    gate = GateConfig(**payload.pop("gate"))
    payload["field_scope"] = tuple(payload["field_scope"])
    payload["feature_panels"] = tuple(
        FeaturePanelRef(**item) for item in payload.get("feature_panels", [])
    )
    return RunConfig(split=split, gate=gate, **payload)


def _cmd_run_loop(args: argparse.Namespace) -> int:
    config = _load_run_config(Path(args.config))
    store = build_store(config.market_id, Path(args.data_root))
    summary = run_research_loop(
        store,
        config,
        build_generator(config.generator_id),
        build_policy(config.mode),
        Path(args.out),
        sealed_root=Path(args.sealed_root),
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    retriever = None
    qa_engine = None
    if args.news_db:
        store = NewsStore(Path(args.news_db))
        retriever = Retriever(store)
        listing_rows = store.connection.execute(
            "SELECT DISTINCT symbol, symbol FROM news_entity"
        ).fetchall()
        linker = EntityLinker(build_alias_entries([(r[0], r[1]) for r in listing_rows])[0])
        qa_engine = QaEngine(retriever, linker)
    context = AppContext(
        runs_dir=Path(args.runs_dir),
        static_dir=Path(args.static_dir) if args.static_dir else None,
        retriever=retriever,
        qa_engine=qa_engine,
        sentiment_panel_path=Path(args.sentiment_panel) if args.sentiment_panel else None,
    )
    server = create_server(context, host=args.host, port=args.port)
    host_text = str(server.server_address[0])
    print(f"服务已启动: http://{host_text}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alphaloop", description="Alpha-Auto-Loop-Agent 命令行")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run-loop", help="执行一轮研究循环")
    run_parser.add_argument("--config", required=True, help="运行配置 JSON 文件路径")
    run_parser.add_argument("--data-root", required=True, help="minute_db 数据根目录")
    run_parser.add_argument("--out", required=True, help="运行产物输出目录")
    run_parser.add_argument("--sealed-root", required=True, help="封存账本目录")
    run_parser.set_defaults(handler=_cmd_run_loop)
    serve_parser = subparsers.add_parser("serve", help="启动 HTTP 服务与界面")
    serve_parser.add_argument("--runs-dir", required=True, help="运行产物目录")
    serve_parser.add_argument("--static-dir", default="gui/dist", help="前端构建产物目录")
    serve_parser.add_argument("--news-db", default=None, help="新闻库 SQLite 路径（可选）")
    serve_parser.add_argument("--sentiment-panel", default=None, help="情绪面板 parquet（可选）")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8710)
    serve_parser.set_defaults(handler=_cmd_serve)
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as error:  # noqa: BLE001 - 命令行边界统一转换为退出码
        print(f"运行失败: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
