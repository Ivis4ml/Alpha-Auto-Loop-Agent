"""新闻权威库：单文件 SQLite，含全文索引与 point-in-time 字段。

时间戳约定（全部 UTC，格式 ``YYYY-MM-DDTHH:MM:SSZ``，字典序即时间序）：

- publish_ts：发布时间；
- visible_ts：市场可见时间，point-in-time 过滤的唯一依据；
- ingest_ts：入库时间，仅供审计，禁止参与任何 point-in-time 判断。

阅读数、评论数、分享数是抓取时点的快照值，天然含未来信息，只存入
metrics 字段供展示，禁止进入任何因子。合规边界：本模块只消费本机
既有语料，不包含任何抓取能力（见 docs/compliance/cls_terms_review.md）。
"""

from __future__ import annotations

import csv
import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jieba

__all__ = ["NewsItem", "NewsStore", "ingest_cls_csv_dir"]

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_CLS_FILE_PATTERN = re.compile(r"财联社电报(\d{4}-\d{2}-\d{2})\.csv$")
_BEIJING_UTC_OFFSET = timedelta(hours=8)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_items (
    news_id       TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    market_hint   TEXT,
    publish_ts    TEXT NOT NULL,
    visible_ts    TEXT NOT NULL,
    visible_basis TEXT NOT NULL,
    ingest_ts     TEXT NOT NULL,
    title         TEXT,
    content       TEXT NOT NULL,
    labels        TEXT,
    metrics       TEXT,
    corpus_batch  TEXT NOT NULL,
    tokens        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_visible ON news_items (visible_ts);
CREATE TABLE IF NOT EXISTS news_entity (
    news_id    TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    method     TEXT NOT NULL,
    confidence REAL NOT NULL,
    span_start INTEGER,
    span_end   INTEGER,
    PRIMARY KEY (news_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_entity_symbol ON news_entity (symbol);
CREATE VIRTUAL TABLE IF NOT EXISTS news_fts USING fts5(
    tokens, content='news_items', content_rowid='rowid'
);
"""


@dataclass(frozen=True, slots=True)
class NewsItem:
    """一条规范化新闻。"""

    source: str
    publish_ts: str
    visible_ts: str
    visible_basis: str
    title: str
    content: str
    labels: tuple[str, ...]
    corpus_batch: str
    market_hint: str | None = None
    metrics: str | None = None

    def news_id(self) -> str:
        """内容身份：来源 + 发布时刻 + 归一正文的哈希（源无稳定 id）。"""
        normalized = "".join(self.content.split())
        return hashlib.sha1(
            "\x00".join((self.source, self.publish_ts, normalized)).encode("utf-8")
        ).hexdigest()


def _utcnow() -> str:
    return datetime.now(UTC).strftime(_TS_FORMAT)


class NewsStore:
    """新闻库读写入口。clock 可注入以获得确定性 ingest_ts。"""

    def __init__(self, db_path: Path, *, clock: Callable[[], str] = _utcnow) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._clock = clock
        self.connection = sqlite3.connect(db_path)
        self.connection.executescript(_SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def upsert(self, items: Iterable[NewsItem]) -> int:
        """写入新闻，按 news_id 去重；返回新增条数。"""
        inserted = 0
        cursor = self.connection.cursor()
        ingest_ts = self._clock()
        for item in items:
            tokens = " ".join(jieba.lcut(f"{item.title} {item.content}"))
            result = cursor.execute(
                "INSERT OR IGNORE INTO news_items "
                "(news_id, source, market_hint, publish_ts, visible_ts, visible_basis, "
                " ingest_ts, title, content, labels, metrics, corpus_batch, tokens) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.news_id(),
                    item.source,
                    item.market_hint,
                    item.publish_ts,
                    item.visible_ts,
                    item.visible_basis,
                    ingest_ts,
                    item.title,
                    item.content,
                    " ".join(item.labels),
                    item.metrics,
                    item.corpus_batch,
                    tokens,
                ),
            )
            if result.rowcount > 0:
                inserted += 1
                cursor.execute(
                    "INSERT INTO news_fts (rowid, tokens) "
                    "SELECT rowid, tokens FROM news_items WHERE news_id = ?",
                    (item.news_id(),),
                )
        self.connection.commit()
        return inserted

    def count(self) -> int:
        """新闻总条数。"""
        row = self.connection.execute("SELECT count(*) FROM news_items").fetchone()
        return int(row[0])

    def write_entities(
        self, links: Iterable[tuple[str, str, str, float, int | None, int | None]]
    ) -> int:
        """写入实体链接 (news_id, symbol, method, confidence, span_start, span_end)。"""
        cursor = self.connection.cursor()
        written = 0
        for news_id, symbol, method, confidence, span_start, span_end in links:
            cursor.execute(
                "INSERT OR REPLACE INTO news_entity "
                "(news_id, symbol, method, confidence, span_start, span_end) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (news_id, symbol, method, confidence, span_start, span_end),
            )
            written += 1
        self.connection.commit()
        return written

    def iter_items(self, *, batch_size: int = 1000) -> Iterable[tuple[str, str, str]]:
        """迭代 (news_id, title, content)，供实体链接批处理。"""
        cursor = self.connection.execute("SELECT news_id, title, content FROM news_items")
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                return
            for row in rows:
                yield (str(row[0]), str(row[1] or ""), str(row[2]))


def _beijing_to_utc(day: str, hms: str) -> str:
    local = datetime.fromisoformat(f"{day}T{hms}")
    return (local - _BEIJING_UTC_OFFSET).strftime(_TS_FORMAT)


def ingest_cls_csv_dir(
    store: NewsStore,
    csv_dir: Path,
    *,
    corpus_batch: str = "backfill",
    limit_files: int | None = None,
) -> dict[str, int]:
    """把按日 CSV 存量语料导入新闻库。

    列结构 Index,Time,Title,Content,Labels,Reads,Comments,Shares，日期取自
    文件名；Time 为北京时间，转为 UTC 存储；电报发布即公开，visible_ts
    等于 publish_ts（visible_basis='publish'）。
    """
    files = sorted(path for path in csv_dir.iterdir() if _CLS_FILE_PATTERN.search(path.name))
    if limit_files is not None:
        files = files[:limit_files]
    total_rows = 0
    total_inserted = 0
    for path in files:
        match = _CLS_FILE_PATTERN.search(path.name)
        assert match is not None
        day = match.group(1)
        items: list[NewsItem] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                time_text = (row.get("Time") or "").strip()
                content = (row.get("Content") or "").strip()
                if not time_text or not content:
                    continue
                publish_ts = _beijing_to_utc(day, time_text)
                labels = tuple(
                    part for part in (row.get("Labels") or "").split() if part
                )
                metrics = (
                    f'{{"reads": {row.get("Reads") or 0}, '
                    f'"comments": {row.get("Comments") or 0}, '
                    f'"shares": {row.get("Shares") or 0}}}'
                )
                items.append(
                    NewsItem(
                        source="cls",
                        market_hint="cn",
                        publish_ts=publish_ts,
                        visible_ts=publish_ts,
                        visible_basis="publish",
                        title=(row.get("Title") or "").strip(),
                        content=content,
                        labels=labels,
                        corpus_batch=corpus_batch,
                        metrics=metrics,
                    )
                )
        total_rows += len(items)
        total_inserted += store.upsert(items)
    return {"files": len(files), "rows": total_rows, "inserted": total_inserted}
