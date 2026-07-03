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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alphaloop", description="Alpha-Auto-Loop-Agent 命令行")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run-loop", help="执行一轮研究循环")
    run_parser.add_argument("--config", required=True, help="运行配置 JSON 文件路径")
    run_parser.add_argument("--data-root", required=True, help="minute_db 数据根目录")
    run_parser.add_argument("--out", required=True, help="运行产物输出目录")
    run_parser.add_argument("--sealed-root", required=True, help="封存账本目录")
    run_parser.set_defaults(handler=_cmd_run_loop)
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as error:  # noqa: BLE001 - 命令行边界统一转换为退出码
        print(f"运行失败: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
