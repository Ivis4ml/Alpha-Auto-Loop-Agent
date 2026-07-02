"""新闻子系统测试：入库、去重、实体链接、检索与 point-in-time 边界。

语料夹具为合成文本（模仿电报 CSV 格式），不含任何真实抓取数据，
以符合合规核实记录中"不对外发布原始抓取数据"的边界。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from news.entity import EntityLinker, build_alias_entries, normalize_text
from news.retrieval import Retriever
from news.store import NewsItem, NewsStore, ingest_cls_csv_dir

LISTING = [
    ("600519.SH", "贵州茅台"),
    ("000001.SZ", "平安银行"),
    ("601318.SH", "中国平安"),
    ("000002.SZ", "万  科Ａ"),
    ("600030.SH", "中信证券"),
    ("002594.SZ", "比亚迪"),
    ("600123.SH", "ST兰花"),
]

_CSV_HEADER = "Index,Time,Title,Content,Labels,Reads,Comments,Shares\n"


def _fixed_clock() -> str:
    return "2026-01-01T00:00:00Z"


def make_store(tmp_path: Path) -> NewsStore:
    return NewsStore(tmp_path / "news.db", clock=_fixed_clock)


def synthetic_items() -> list[NewsItem]:
    rows = [
        ("2024-05-06T02:00:00Z", "白酒龙头发布年报", "贵州茅台公布年度报告，营收稳定增长。"),
        ("2024-05-06T03:00:00Z", "银行板块走强", "平安银行今日领涨银行板块，成交活跃。"),
        ("2024-05-07T01:30:00Z", "新能源车企产销快报", "比亚迪公布产销数据，出口保持增长。"),
        (
            "2024-05-08T05:00:00Z",
            "券商研报观点",
            "中信证券发布对白酒行业的展望报告，提及贵州茅台。",
        ),
    ]
    return [
        NewsItem(
            source="synthetic",
            publish_ts=ts,
            visible_ts=ts,
            visible_basis="publish",
            title=title,
            content=content,
            labels=("测试",),
            corpus_batch="backfill",
            market_hint="cn",
        )
        for ts, title, content in rows
    ]


class TestStore:
    def test_upsert_and_dedupe(self, tmp_path: Path) -> None:
        store = make_store(tmp_path)
        items = synthetic_items()
        assert store.upsert(items) == 4
        assert store.upsert(items) == 0
        assert store.count() == 4

    def test_csv_ingestion(self, tmp_path: Path) -> None:
        csv_dir = tmp_path / "corpus"
        csv_dir.mkdir()
        (csv_dir / "财联社电报2024-05-06.csv").write_text(
            _CSV_HEADER
            + '0,10:00:00,合成标题一,合成正文：贵州茅台测试条目。,白酒,100,0,1\n'
            + '1,10:05:00,合成标题二,合成正文：平安银行测试条目。,银行,200,0,2\n',
            encoding="utf-8-sig",
        )
        store = make_store(tmp_path)
        report = ingest_cls_csv_dir(store, csv_dir)
        assert report == {"files": 1, "rows": 2, "inserted": 2}
        row = store.connection.execute(
            "SELECT publish_ts, visible_ts, visible_basis FROM news_items ORDER BY publish_ts"
        ).fetchone()
        assert row[0] == "2024-05-06T02:00:00Z"
        assert row[1] == row[0]
        assert row[2] == "publish"

    def test_ingest_ts_is_audit_only(self, tmp_path: Path) -> None:
        store = make_store(tmp_path)
        store.upsert(synthetic_items())
        row = store.connection.execute("SELECT DISTINCT ingest_ts FROM news_items").fetchall()
        assert row == [("2026-01-01T00:00:00Z",)]


class TestEntityLinking:
    def test_normalize_fullwidth_and_spaces(self) -> None:
        assert normalize_text("万  科Ａ") == "万科A"

    def test_alias_entries_cover_codes_and_st(self) -> None:
        entries, ambiguous = build_alias_entries(LISTING)
        aliases = {(entry.alias, entry.symbol) for entry in entries}
        assert ("600519", "600519.SH") in aliases
        assert ("600519.SH", "600519.SH") in aliases
        assert ("贵州茅台", "600519.SH") in aliases
        assert ("ST兰花", "600123.SH") in aliases
        assert ("兰花", "600123.SH") in aliases
        assert ambiguous == []

    def test_ambiguous_name_dropped(self) -> None:
        entries, ambiguous = build_alias_entries(
            [("600001.SH", "国泰集团"), ("000601.SZ", "国泰集团")]
        )
        assert "国泰集团" in ambiguous
        assert all(entry.alias != "国泰集团" for entry in entries)

    def test_linker_finds_code_and_name(self) -> None:
        entries, _ = build_alias_entries(LISTING)
        linker = EntityLinker(entries)
        links = linker.link("n1", "白酒观察", "盘面上 600519 领涨，贵州茅台成交放量。")
        by_symbol = {link.symbol: link for link in links}
        assert by_symbol["600519.SH"].confidence == 1.0
        assert by_symbol["600519.SH"].method == "code"

    def test_title_hit_gets_bonus(self) -> None:
        entries, _ = build_alias_entries(LISTING)
        linker = EntityLinker(entries)
        in_title = linker.link("n1", "贵州茅台年报点评", "正文不再提及该公司。")
        in_body = linker.link("n2", "白酒行业观察", "正文提及贵州茅台的年报。")
        assert in_title[0].confidence > in_body[0].confidence

    def test_gold_set_precision_recall(self) -> None:
        """合成金标集：优先保证精确率（误链接危害大于漏链接）。"""
        entries, _ = build_alias_entries(LISTING)
        linker = EntityLinker(entries)
        gold: list[tuple[str, str, set[str]]] = [
            ("白酒龙头年报", "贵州茅台公布年度报告。", {"600519.SH"}),
            ("银行板块走强", "平安银行领涨，中国平安跟涨。", {"000001.SZ", "601318.SH"}),
            ("地产成交回暖", "万科A 公布月度销售数据。", {"000002.SZ"}),
            ("市场综述", "今日两市成交平淡，无明显主线。", set()),
            ("车企快报", "比亚迪出口数据亮眼。", {"002594.SZ"}),
        ]
        true_positive = 0
        false_positive = 0
        false_negative = 0
        threshold = 0.75
        for index, (title, content, expected) in enumerate(gold):
            links = linker.link(f"g{index}", title, content)
            predicted = {link.symbol for link in links if link.confidence >= threshold}
            true_positive += len(predicted & expected)
            false_positive += len(predicted - expected)
            false_negative += len(expected - predicted)
        precision = true_positive / (true_positive + false_positive)
        recall = true_positive / (true_positive + false_negative)
        assert precision >= 0.95
        assert recall >= 0.80


class TestRetrieval:
    @pytest.fixture()
    def prepared(self, tmp_path: Path) -> tuple[NewsStore, Retriever]:
        store = make_store(tmp_path)
        items = synthetic_items()
        store.upsert(items)
        entries, _ = build_alias_entries(LISTING)
        linker = EntityLinker(entries)
        links = []
        for news_id, title, content in store.iter_items():
            for link in linker.link(news_id, title, content):
                links.append(
                    (
                        link.news_id,
                        link.symbol,
                        link.method,
                        link.confidence,
                        link.span_start,
                        link.span_end,
                    )
                )
        store.write_entities(links)
        return store, Retriever(store)

    def test_keyword_search_ranks_relevant(self, prepared: tuple[NewsStore, Retriever]) -> None:
        _, retriever = prepared
        results = retriever.search("贵州茅台 年报")
        assert len(results) >= 2
        assert "茅台" in results[0].title + results[0].snippet

    def test_symbol_filter(self, prepared: tuple[NewsStore, Retriever]) -> None:
        _, retriever = prepared
        results = retriever.search("公布 数据", symbols=["002594.SZ"])
        assert len(results) == 1
        assert results[0].matched_symbols == ("002594.SZ",)

    def test_pit_boundary_excludes_future(self, prepared: tuple[NewsStore, Retriever]) -> None:
        """point-in-time 边界：as_of 之前的可见、之后的不可见。"""
        _, retriever = prepared
        before = retriever.search("贵州茅台", as_of="2024-05-06T02:00:00Z")
        assert {result.visible_ts for result in before} == {"2024-05-06T02:00:00Z"}
        after = retriever.search("贵州茅台", as_of="2024-05-09T00:00:00Z")
        assert len(after) > len(before)
        none_yet = retriever.search("贵州茅台", as_of="2024-05-05T00:00:00Z")
        assert none_yet == []

    def test_window_filter(self, prepared: tuple[NewsStore, Retriever]) -> None:
        _, retriever = prepared
        results = retriever.search(
            "公布", window=("2024-05-07T00:00:00Z", "2024-05-07T23:59:59Z")
        )
        assert len(results) == 1
        assert "比亚迪" in results[0].snippet

    def test_empty_query_returns_nothing(self, prepared: tuple[NewsStore, Retriever]) -> None:
        _, retriever = prepared
        assert retriever.search("   ") == []
