"""情绪双轨、面板归日与对话问答的测试。"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from alphaloop.llm.client import LlmClient
from news.entity import EntityLinker, build_alias_entries
from news.qa import QaEngine
from news.retrieval import Retriever
from news.sentiment.lexicon import load_default_lexicon
from news.sentiment.llm_track import LlmSentimentScorer
from news.sentiment.panel import build_sentiment_panel
from news.store import NewsItem, NewsStore

LISTING = [("600519.SH", "贵州茅台"), ("000001.SZ", "平安银行"), ("002594.SZ", "比亚迪")]


class FakeProvider:
    provider_id = "fake"

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, str]] = []

    def complete(self, *, model: str, system: str, prompt: str, temperature: float) -> str:
        self.calls.append({"system": system, "prompt": prompt})
        return self.response


class StubCalendar:
    """测试用日历视图：2024-05-06（周一）起连续交易日，收盘截止 15:00。"""

    DAYS = [date(2024, 5, 6), date(2024, 5, 7), date(2024, 5, 8), date(2024, 5, 9)]

    def timezone(self, market_id: str) -> str:
        return "Asia/Shanghai"

    def trading_days(self, market_id: str, start: date, end: date) -> list[date]:
        return [day for day in self.DAYS if start <= day <= end]

    def session_cutoff(self, market_id: str, day: date) -> datetime:
        return datetime(day.year, day.month, day.day, 15, 0, 0)

    def next_trading_day(self, market_id: str, day: date) -> date:
        for candidate in self.DAYS:
            if candidate > day:
                return candidate
        raise ValueError("超出测试日历范围")


def _fixed_clock() -> str:
    return "2026-01-01T00:00:00Z"


def prepared_store(tmp_path: Path) -> NewsStore:
    store = NewsStore(tmp_path / "news.db", clock=_fixed_clock)
    items = [
        # 北京 2024-05-06 10:00（盘中）→ 归 05-06
        NewsItem(
            source="synthetic",
            publish_ts="2024-05-06T02:00:00Z",
            visible_ts="2024-05-06T02:00:00Z",
            visible_basis="publish",
            title="贵州茅台一季度业绩预增",
            content="贵州茅台预计净利润增百分之十五，超预期。",
            labels=("白酒",),
            corpus_batch="backfill",
        ),
        # 北京 2024-05-06 16:30（收盘后）→ 归 05-07
        NewsItem(
            source="synthetic",
            publish_ts="2024-05-06T08:30:00Z",
            visible_ts="2024-05-06T08:30:00Z",
            visible_basis="publish",
            title="贵州茅台获监管警示函",
            content="贵州茅台收到警示函，涉及信息披露问题。",
            labels=("监管",),
            corpus_batch="backfill",
        ),
        # 北京 2024-05-08 09:00（开盘前）→ 归 05-08
        NewsItem(
            source="synthetic",
            publish_ts="2024-05-08T01:00:00Z",
            visible_ts="2024-05-08T01:00:00Z",
            visible_basis="publish",
            title="平安银行拟回购股份",
            content="平安银行公告拟回购股份用于股权激励。",
            labels=("银行",),
            corpus_batch="realtime",
        ),
    ]
    store.upsert(items)
    entries, _ = build_alias_entries(LISTING)
    linker = EntityLinker(entries)
    links = []
    for news_id, title, content in store.iter_items():
        for link in linker.link(news_id, title, content):
            links.append(
                (link.news_id, link.symbol, link.method, link.confidence, None, None)
            )
    store.write_entities(links)
    return store


class TestLexicon:
    def test_positive_and_negative_events(self) -> None:
        lexicon = load_default_lexicon()
        positive = lexicon.score("公司业绩预增，产品提价，签订重大合同。")
        negative = lexicon.score("公司被立案调查，存在退市风险。")
        assert positive.score > 0
        assert negative.score < 0
        assert "业绩预增" in positive.events
        assert "立案调查" in negative.events

    def test_each_event_counted_once(self) -> None:
        lexicon = load_default_lexicon()
        once = lexicon.score("中标")
        twice = lexicon.score("中标，再次中标，第三次中标")
        assert once.score == twice.score

    def test_config_hash_stable(self) -> None:
        assert load_default_lexicon().config_hash() == load_default_lexicon().config_hash()


class TestLlmTrack:
    def test_score_parses_and_flags_backfilled(self, tmp_path: Path) -> None:
        response = json.dumps(
            {
                "relevance": 0.9,
                "polarity": 0.8,
                "confidence": 0.7,
                "event_type": "业绩预增",
                "rationale": "业绩超预期利好",
            }
        )
        client = LlmClient(FakeProvider(response), tmp_path / "cache", mode="explore")
        scorer = LlmSentimentScorer(client, "senti-model")
        result = scorer.score(
            news_id="n1", symbol="600519.SH", title="业绩预增", content="净利润增长"
        )
        assert result.model_backfilled is True
        assert result.polarity == 0.8
        assert result.cache_key

    def test_unparseable_output_degrades_to_zero(self, tmp_path: Path) -> None:
        client = LlmClient(FakeProvider("无法解析"), tmp_path / "cache", mode="explore")
        result = LlmSentimentScorer(client, "m").score(
            news_id="n1", symbol="600519.SH", title="t", content="c"
        )
        assert result.relevance == 0.0
        assert "不可解析" in result.rationale


class TestPanel:
    def test_pit_assignment_and_aggregation(self, tmp_path: Path) -> None:
        store = prepared_store(tmp_path)
        result = build_sentiment_panel(
            store,
            load_default_lexicon(),
            StubCalendar(),
            market_id="cn_ashare",
            start=date(2024, 5, 6),
            end=date(2024, 5, 9),
            out_dir=tmp_path / "panel",
        )
        panel = result.panel
        maotai = panel[panel["symbol"] == "600519.SH"].set_index("trade_date")
        # 盘中新闻归当日，收盘后新闻归次日
        assert maotai.loc["2024-05-06", "lex_score"] > 0
        assert maotai.loc["2024-05-07", "lex_score"] < 0
        bank = panel[panel["symbol"] == "000001.SZ"].set_index("trade_date")
        assert bank.loc["2024-05-08", "n_news"] == 1
        assert (panel["llm_n"] == 0).all()
        assert Path(result.panel_path).exists()

    def test_manifest_declares_pit_and_coverage(self, tmp_path: Path) -> None:
        store = prepared_store(tmp_path)
        result = build_sentiment_panel(
            store,
            load_default_lexicon(),
            StubCalendar(),
            market_id="cn_ashare",
            start=date(2024, 5, 6),
            end=date(2024, 5, 9),
            out_dir=tmp_path / "panel",
        )
        manifest = result.manifest
        assert manifest.grain == "date_symbol"
        assert manifest.visibility_ts_column == "visible_ts"
        assert manifest.no_lookahead_attested is True
        assert any("七成" in note for note in manifest.coverage_notes)
        assert any("回算值" in note for note in manifest.coverage_notes)
        assert manifest.build_config_hash

    def test_replay_reproducible_with_llm_track(self, tmp_path: Path) -> None:
        """explore 打分后 replay 断后端重建面板，逐字节一致。"""
        response = json.dumps(
            {"relevance": 0.9, "polarity": -0.5, "confidence": 0.8, "event_type": None,
             "rationale": "监管利空"}
        )
        cache = tmp_path / "cache"
        outputs = []
        for label, provider in (("explore", FakeProvider(response)), ("replay", None)):
            store = prepared_store(tmp_path / label)
            if provider is not None:
                client = LlmClient(provider, cache, mode="explore")
            else:
                class _Failing:
                    provider_id = "fake"

                    def complete(self, **kwargs: object) -> str:
                        raise AssertionError("回放不得触达后端")

                client = LlmClient(_Failing(), cache, mode="replay")
            result = build_sentiment_panel(
                store,
                load_default_lexicon(),
                StubCalendar(),
                market_id="cn_ashare",
                start=date(2024, 5, 6),
                end=date(2024, 5, 9),
                out_dir=tmp_path / f"panel-{label}",
                llm_scorer=LlmSentimentScorer(client, "senti-model"),
            )
            outputs.append(result.panel.to_csv(index=False))
        assert outputs[0] == outputs[1]


class TestQa:
    def _engine(self, tmp_path: Path, response: str | None) -> QaEngine:
        store = prepared_store(tmp_path)
        entries, _ = build_alias_entries(LISTING)
        linker = EntityLinker(entries)
        client = None
        if response is not None:
            client = LlmClient(FakeProvider(response), tmp_path / "qa-cache", mode="explore")
        return QaEngine(Retriever(store), linker, client, model="qa-model")

    def test_insufficient_evidence_is_honest(self, tmp_path: Path) -> None:
        engine = self._engine(tmp_path, None)
        answer = engine.answer(
            "某不存在公司的新闻", symbol="600519.SH", as_of="2024-05-01T00:00:00Z"
        )
        assert answer.evidence_mode == "insufficient"
        assert "未检索到" in answer.text
        assert answer.citations == ()

    def test_pit_preamble_and_boundary(self, tmp_path: Path) -> None:
        engine = self._engine(tmp_path, None)
        answer = engine.answer(
            "贵州茅台有什么新闻", symbol="600519.SH", as_of="2024-05-06T03:00:00Z"
        )
        assert answer.text.startswith("（以下回答仅基于 2024-05-06T03:00:00Z")
        assert len(answer.citations) == 1
        assert all(c.visible_ts <= "2024-05-06T03:00:00Z" for c in answer.citations)

    def test_evidence_answer_with_llm(self, tmp_path: Path) -> None:
        canned = (
            "【新闻证据】\n贵州茅台一季度业绩预增，超出市场预期 [1]。\n"
            "【背景知识】\n以下为模型背景知识，非新闻证据：该公司为白酒行业龙头。"
        )
        engine = self._engine(tmp_path, canned)
        answer = engine.answer("贵州茅台的业绩怎么样", symbol="600519.SH")
        assert answer.evidence_mode == "evidence"
        assert "【新闻证据】" in answer.text
        assert "【背景知识】" in answer.text
        assert "[1]" in answer.text
        assert answer.trace["llm_cache_key"]

    def test_symbol_detected_from_question(self, tmp_path: Path) -> None:
        engine = self._engine(tmp_path, None)
        answer = engine.answer("平安银行的回购进展如何")
        assert answer.evidence_mode == "evidence"
        assert any("平安银行" in c.title for c in answer.citations)


class TestPanelBoundary:
    def test_news_after_last_day_cutoff_is_dropped(self, tmp_path: Path) -> None:
        """窗口末日收盘后的新闻应被丢弃而不是触发日历越界异常。"""
        store = NewsStore(tmp_path / "news.db", clock=_fixed_clock)
        store.upsert(
            [
                NewsItem(
                    source="synthetic",
                    publish_ts="2024-05-06T02:00:00Z",
                    visible_ts="2024-05-06T02:00:00Z",
                    visible_basis="publish",
                    title="贵州茅台盘中新闻",
                    content="贵州茅台盘中公告。",
                    labels=(),
                    corpus_batch="backfill",
                ),
                NewsItem(
                    source="synthetic",
                    publish_ts="2024-05-09T09:00:00Z",
                    visible_ts="2024-05-09T09:00:00Z",
                    visible_basis="publish",
                    title="贵州茅台收盘后新闻",
                    content="贵州茅台收盘后公告（北京 17 时，末日收盘之后）。",
                    labels=(),
                    corpus_batch="backfill",
                ),
            ]
        )
        entries, _ = build_alias_entries(LISTING)
        linker = EntityLinker(entries)
        links = []
        for news_id, title, content in store.iter_items():
            for link in linker.link(news_id, title, content):
                links.append(
                    (link.news_id, link.symbol, link.method, link.confidence, None, None)
                )
        store.write_entities(links)
        result = build_sentiment_panel(
            store,
            load_default_lexicon(),
            StubCalendar(),
            market_id="cn_ashare",
            start=date(2024, 5, 6),
            end=date(2024, 5, 9),
            out_dir=tmp_path / "panel",
        )
        assert result.panel["n_news"].sum() == 1
        assert set(result.panel["trade_date"]) == {"2024-05-06"}
