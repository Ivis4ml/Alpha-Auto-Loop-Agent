"""新闻检索：关键词（FTS5 BM25）为基线，point-in-time 过滤单点实施。

point-in-time 过滤在候选生成的 SQL 层实施且只依据 visible_ts，排序层
只在候选集内工作，结构上不可能召回越界文档；ingest_ts 不参与任何
过滤。混合检索（向量路）按里程碑计划在其后加入，且必须在检索金标
集上给出相对本基线的提升数字才允许合入。
"""

from __future__ import annotations

from dataclasses import dataclass

import jieba

from news.entity import DEFAULT_CONFIDENCE_THRESHOLD
from news.store import NewsStore

__all__ = ["ScoredNews", "Retriever"]


@dataclass(frozen=True, slots=True)
class ScoredNews:
    """一条检索结果。"""

    news_id: str
    score: float
    publish_ts: str
    visible_ts: str
    source: str
    title: str
    snippet: str
    matched_symbols: tuple[str, ...]


class Retriever:
    """关键词检索器。"""

    def __init__(
        self,
        store: NewsStore,
        *,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        self.store = store
        self.confidence_threshold = confidence_threshold

    def search(
        self,
        query: str,
        *,
        symbols: list[str] | None = None,
        as_of: str | None = None,
        window: tuple[str, str] | None = None,
        top_k: int = 20,
    ) -> list[ScoredNews]:
        """检索最相关的新闻子集。

        as_of 为 point-in-time 时点（UTC，ISO 格式），None 表示不限；
        symbols 限定已链接标的；window 为 (起, 止) 可见时间窗。
        """
        tokens = [token.strip() for token in jieba.lcut(query) if token.strip()]
        if not tokens:
            return []
        match_expression = " OR ".join(f'"{token}"' for token in tokens)

        join_parameters: list[object] = []
        symbol_join = ""
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            symbol_join = (
                "JOIN news_entity entity ON entity.news_id = items.news_id "
                f"AND entity.symbol IN ({placeholders}) AND entity.confidence >= ?"
            )
            join_parameters = [*symbols, self.confidence_threshold]

        conditions = ["news_fts MATCH ?"]
        condition_parameters: list[object] = [match_expression]
        if as_of is not None:
            conditions.append("items.visible_ts <= ?")
            condition_parameters.append(as_of)
        if window is not None:
            conditions.append("items.visible_ts >= ? AND items.visible_ts <= ?")
            condition_parameters.extend(window)

        sql = (
            "SELECT items.news_id, bm25(news_fts) AS rank_score, items.publish_ts, "
            "items.visible_ts, items.source, items.title, substr(items.content, 1, 200) "
            "FROM news_fts "
            "JOIN news_items items ON items.rowid = news_fts.rowid "
            f"{symbol_join} "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY rank_score LIMIT ?"
        )
        rows = self.store.connection.execute(
            sql, [*join_parameters, *condition_parameters, top_k]
        ).fetchall()
        results: list[ScoredNews] = []
        for row in rows:
            matched = self._matched_symbols(str(row[0]))
            results.append(
                ScoredNews(
                    news_id=str(row[0]),
                    score=-float(row[1]),
                    publish_ts=str(row[2]),
                    visible_ts=str(row[3]),
                    source=str(row[4]),
                    title=str(row[5] or ""),
                    snippet=str(row[6] or ""),
                    matched_symbols=matched,
                )
            )
        return results

    def _matched_symbols(self, news_id: str) -> tuple[str, ...]:
        rows = self.store.connection.execute(
            "SELECT symbol FROM news_entity WHERE news_id = ? AND confidence >= ? "
            "ORDER BY symbol",
            (news_id, self.confidence_threshold),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)
