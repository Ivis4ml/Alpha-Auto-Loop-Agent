#!/usr/bin/env bash
# Alpha-Auto-Loop-Agent 一键试用脚本。
#
# 完成以下步骤：
#   1. 准备 Python 虚拟环境与依赖（已存在则跳过）；
#   2. 构建前端（gui/dist 已存在则跳过；需要本机 npm）；
#   3. 从本机存量财联社语料导入最近 N 天新闻、构建实体链接与情绪面板
#      （只读语料，不做任何新增抓取，词典轨打分、零模型调用）；
#   4. 在真实全量数据上跑双市场研究循环各一轮（约 15 秒）；
#   5. 启动 HTTP 服务与界面。
#
# 可用环境变量（均有默认值）：
#   ALPHA_DATA   Alpha-Data 数据根目录
#   CLS_CORPUS   财联社按日 CSV 语料目录
#   NEWS_DAYS    导入最近多少天的新闻（默认 365）
#   PORT         服务端口（默认 8710）
#   DEMO_NO_SERVE=1  只准备数据与运行产物，不启动服务（供脚本自测）
#
# 重复运行说明：每次运行使用新的运行标识与独立封存账本目录。封存
# 账本按设计拒绝"同一冻结规则 + 同一数据 + 同一候选集合"的重复保留段
# 评估，这是科研诚信机制而非缺陷；试用场景以时间戳隔离即可。

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

ALPHA_DATA="${ALPHA_DATA:-/Users/xinyu/Code/Workspace/ncvx-project/Alpha-Data/data}"
CLS_CORPUS="${CLS_CORPUS:-/Users/xinyu/Code/Workspace/ncvx-project/分钟线策略任务-B/Crawler/crawler_cls/data/output}"
NEWS_DAYS="${NEWS_DAYS:-365}"
PORT="${PORT:-8710}"
RUN_TAG="$(date +%Y%m%d-%H%M%S)"
DEMO_DIR="$REPO/runs/demo"
PY="$REPO/.venv/bin/python"

echo "== 第 1 步：Python 环境 =="
if [ ! -x "$PY" ]; then
    python3 -m venv .venv
fi
if ! "$PY" -c "import alphaloop" >/dev/null 2>&1; then
    "$REPO/.venv/bin/pip" install --quiet -e ".[dev]"
fi
echo "   就绪：$("$PY" --version)"

echo "== 第 2 步：前端构建 =="
if [ -f "$REPO/gui/dist/index.html" ]; then
    echo "   gui/dist 已存在，跳过构建"
elif command -v npm >/dev/null 2>&1; then
    (cd "$REPO/gui" && npm install --silent && npm run build --silent)
    echo "   构建完成"
else
    echo "   未找到 npm，跳过前端构建（界面路由将返回 503，API 不受影响）"
fi

mkdir -p "$DEMO_DIR"

echo "== 第 3 步：新闻导入、实体链接与情绪面板（最近 $NEWS_DAYS 天存量语料）=="
CLS_CORPUS="$CLS_CORPUS" NEWS_DAYS="$NEWS_DAYS" ALPHA_DATA="$ALPHA_DATA" \
DEMO_DIR="$DEMO_DIR" "$PY" - <<'PYEOF'
import os
import tempfile
from datetime import date
from pathlib import Path

import duckdb

from apps.assembly import StoreCalendarView, build_store
from news.entity import EntityLinker, build_alias_entries
from news.sentiment.lexicon import load_default_lexicon
from news.sentiment.panel import build_sentiment_panel
from news.store import NewsStore, ingest_cls_csv_dir

demo_dir = Path(os.environ["DEMO_DIR"])
corpus = Path(os.environ["CLS_CORPUS"])
news_days = int(os.environ["NEWS_DAYS"])
alpha_data = Path(os.environ["ALPHA_DATA"])

db_path = demo_dir / "news.db"
store = NewsStore(db_path)
if store.count() == 0:
    if not corpus.is_dir():
        raise SystemExit(f"语料目录不存在: {corpus}（可用 CLS_CORPUS 环境变量指定）")
    files = sorted(path for path in corpus.iterdir() if path.name.endswith(".csv"))
    newest = files[-news_days:]
    with tempfile.TemporaryDirectory() as staging:
        for path in newest:
            (Path(staging) / path.name).symlink_to(path)
        report = ingest_cls_csv_dir(store, Path(staging))
    print(f"   新闻入库: {report}")
else:
    print(f"   新闻库已存在（{store.count()} 条），跳过导入")

linked = store.connection.execute("SELECT count(*) FROM news_entity").fetchone()[0]
if linked == 0:
    listing = duckdb.sql(
        f"SELECT symbol, name FROM read_parquet('{(alpha_data / 'cn_equity' / 'minute_db' / 'listing.parquet').as_posix()}')"
    ).fetchall()
    entries, ambiguous = build_alias_entries([(s, n or "") for s, n in listing])
    linker = EntityLinker(entries)
    links = []
    for news_id, title, content in store.iter_items():
        for link in linker.link(news_id, title, content):
            if link.confidence >= 0.75:
                links.append(
                    (link.news_id, link.symbol, link.method, link.confidence,
                     link.span_start, link.span_end)
                )
    store.write_entities(links)
    print(f"   实体链接: {len(links)} 条（歧义别名丢弃 {len(ambiguous)} 个）")
else:
    print(f"   实体链接已存在（{linked} 条），跳过")

panel_path = demo_dir / "sentiment" / "panel.parquet"
if not panel_path.exists():
    cn_store = build_store("cn_ashare", alpha_data / "cn_equity" / "minute_db")
    calendar = StoreCalendarView({"cn_ashare": cn_store.market})
    days = cn_store.trading_days()
    bounds = store.connection.execute(
        "SELECT min(visible_ts), max(visible_ts) FROM news_items"
    ).fetchone()
    news_start = date.fromisoformat(str(bounds[0])[:10])
    news_end = date.fromisoformat(str(bounds[1])[:10])
    window_start = max(days[0], news_start)
    window_end = min(days[-1], news_end)
    if window_start > window_end:
        print(
            f"   跳过情绪面板：新闻区间 {news_start}..{news_end} 与行情交易日 "
            f"{days[0]}..{days[-1]} 无交集（可调大 NEWS_DAYS 覆盖行情区间）"
        )
    else:
        result = build_sentiment_panel(
            store,
            load_default_lexicon(),
            calendar,
            market_id="cn_ashare",
            start=window_start,
            end=window_end,
            out_dir=demo_dir / "sentiment",
        )
        print(f"   情绪面板: {len(result.panel)} 行（{window_start}..{window_end}）")
else:
    print("   情绪面板已存在，跳过")
PYEOF

echo "== 第 4 步：双市场研究循环（真实全量数据）=="
ALPHA_DATA="$ALPHA_DATA" DEMO_DIR="$DEMO_DIR" RUN_TAG="$RUN_TAG" "$PY" - <<'PYEOF'
import json
import os
import time
from pathlib import Path

from alphaloop.loop.config import GateConfig, RunConfig, SplitConfig
from alphaloop.loop.engine import run_research_loop
from apps.assembly import build_generator, build_policy, build_store

demo_dir = Path(os.environ["DEMO_DIR"])
alpha_data = Path(os.environ["ALPHA_DATA"])
tag = os.environ["RUN_TAG"]

plans = [
    ("cn_ashare", alpha_data / "cn_equity" / "minute_db",
     SplitConfig(train_start="2024-01-02", train_end="2025-03-31", embargo_days=10,
                 holdout_start="2025-04-15", holdout_end="2025-12-31")),
    ("us_equity", alpha_data / "equity" / "minute_db",
     SplitConfig(train_start="2024-01-02", train_end="2025-03-31", embargo_days=10,
                 holdout_start="2025-04-15", holdout_end="2026-06-29")),
]
for market_id, root, split in plans:
    started = time.time()
    store = build_store(market_id, root)
    config = RunConfig(
        run_id=f"demo-{market_id}-{tag}",
        root_seed=2026,
        mode="A",
        market_id=market_id,
        dataset_fingerprint=store.manifest().fingerprint,
        field_scope=("close", "volume", "amount"),
        candidate_budget=16,
        compute_budget=32,
        universe_size=50,
        generator_id="template_v1",
        split=split,
        gate=GateConfig(top_k=10),
    )
    summary = run_research_loop(
        store,
        config,
        build_generator(config.generator_id),
        build_policy(config.mode),
        demo_dir / config.run_id,
        sealed_root=demo_dir / f"sealed-{tag}",
    )
    elapsed = time.time() - started
    print(f"   [{market_id}] {elapsed:.0f}s 完成，未发现={summary['no_findings']}，"
          f"产物目录 {demo_dir / config.run_id}")
PYEOF

echo "== 第 5 步：启动服务 =="
if [ "${DEMO_NO_SERVE:-0}" = "1" ]; then
    echo "   DEMO_NO_SERVE=1，跳过服务启动"
    exit 0
fi
echo "   浏览器打开 http://127.0.0.1:$PORT （Ctrl+C 停止）"
exec "$PY" -m apps.cli serve \
    --runs-dir "$DEMO_DIR" \
    --news-db "$DEMO_DIR/news.db" \
    --sentiment-panel "$DEMO_DIR/sentiment/panel.parquet" \
    --static-dir "$REPO/gui/dist" \
    --port "$PORT"
