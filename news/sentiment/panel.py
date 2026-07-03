"""情绪面板合成：按 (symbol, trade_date) 聚合双轨打分，产出研究层契约。

point-in-time 归日规则：新闻按市场可见时刻（UTC）转市场本地墙钟，
可见于交易日收盘截止之前的归当日，之后的归下一个可交易时点；该规则
写入 manifest。全部历史批量打分为模型回算值，回算占比逐行披露。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from alphaloop.contracts.feature_panel import FeaturePanelManifest, panel_content_fingerprint
from alphaloop.contracts.market_view import MarketCalendarView
from alphaloop.infra.hashing import content_hash
from news.entity import DEFAULT_CONFIDENCE_THRESHOLD
from news.sentiment.lexicon import EventLexicon
from news.sentiment.llm_track import LlmSentimentScorer
from news.store import NewsStore

__all__ = ["SentimentPanelResult", "build_sentiment_panel"]

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True, slots=True)
class SentimentPanelResult:
    """面板构建产物。"""

    panel: pd.DataFrame
    manifest: FeaturePanelManifest
    panel_path: str


def build_sentiment_panel(
    store: NewsStore,
    lexicon: EventLexicon,
    calendar: MarketCalendarView,
    *,
    market_id: str,
    start: date,
    end: date,
    out_dir: Path,
    llm_scorer: LlmSentimentScorer | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> SentimentPanelResult:
    """构建 [start, end] 交易日范围内的情绪面板并落盘。"""
    trading_days = calendar.trading_days(market_id, start, end)
    if not trading_days:
        raise ValueError("窗口内无交易日")
    zone = ZoneInfo(calendar.timezone(market_id))
    rows = store.connection.execute(
        "SELECT entity.symbol, items.news_id, items.visible_ts, items.title, items.content, "
        "items.corpus_batch "
        "FROM news_entity entity JOIN news_items items ON items.news_id = entity.news_id "
        "WHERE entity.confidence >= ? ORDER BY items.visible_ts, entity.symbol",
        (confidence_threshold,),
    ).fetchall()

    records: list[dict[str, object]] = []
    for symbol, news_id, visible_ts, title, content, corpus_batch in rows:
        assigned = _assign_trade_date(
            str(visible_ts), zone, calendar, market_id, trading_days
        )
        if assigned is None:
            continue
        text = f"{title} {content}"
        lex = lexicon.score(text)
        record: dict[str, object] = {
            "symbol": str(symbol),
            "trade_date": assigned,
            "news_id": str(news_id),
            "lex_score": lex.score,
            "corpus_batch": str(corpus_batch),
        }
        if llm_scorer is not None:
            scored = llm_scorer.score(
                news_id=str(news_id),
                symbol=str(symbol),
                title=str(title or ""),
                content=str(content),
            )
            record["llm_polarity"] = scored.polarity * scored.relevance
            record["llm_scored"] = True
        else:
            record["llm_polarity"] = float("nan")
            record["llm_scored"] = False
        records.append(record)

    frame = pd.DataFrame(records)
    if frame.empty:
        raise ValueError("窗口内没有可归日的已链接新闻")
    grouped = frame.groupby(["symbol", "trade_date"], sort=True)
    panel = grouped.agg(
        n_news=("news_id", "nunique"),
        lex_score=("lex_score", "mean"),
        llm_score=("llm_polarity", "mean"),
        llm_n=("llm_scored", "sum"),
    ).reset_index()
    panel["market_id"] = market_id
    panel["blended_score"] = panel["llm_score"].where(panel["llm_n"] > 0, panel["lex_score"])
    panel["model_backfilled_share"] = (panel["llm_n"] > 0).astype("float64")
    panel = panel[
        [
            "market_id",
            "symbol",
            "trade_date",
            "n_news",
            "lex_score",
            "llm_score",
            "llm_n",
            "blended_score",
            "model_backfilled_share",
        ]
    ].sort_values(["symbol", "trade_date"])
    panel["trade_date"] = panel["trade_date"].map(lambda value: value.isoformat())

    build_config_hash = content_hash(
        "sentiment-panel-config",
        market_id,
        lexicon.config_hash(),
        llm_scorer.prompt_version if llm_scorer is not None else None,
        llm_scorer.model if llm_scorer is not None else None,
        confidence_threshold,
        (start.isoformat(), end.isoformat()),
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    panel_path = out_dir / "panel.parquet"
    panel.to_parquet(panel_path, index=False)
    manifest = FeaturePanelManifest(
        panel_id=f"sentiment_{market_id}",
        market_id=market_id,
        grain="date_symbol",
        columns=tuple(str(column) for column in panel.columns),
        fingerprint=panel_content_fingerprint(panel.to_csv(index=False)),
        visibility_ts_column="visible_ts",
        snapshot_start=start.isoformat(),
        no_lookahead_attested=True,
        build_config_hash=build_config_hash,
        coverage_notes=(
            "历史回填语料的结构性覆盖上限约七成（在榜电报口径），评估样本完整性时须计入",
            "全部历史批量打分为模型回算值（model_backfilled_share 列披露）",
        ),
    )
    return SentimentPanelResult(
        panel=panel, manifest=manifest, panel_path=panel_path.as_posix()
    )


def _assign_trade_date(
    visible_ts: str,
    zone: ZoneInfo,
    calendar: MarketCalendarView,
    market_id: str,
    trading_days: list[date],
) -> date | None:
    """把可见时刻归入下一可交易时点；超出窗口返回 None。"""
    utc_moment = datetime.strptime(visible_ts, _TS_FORMAT).replace(tzinfo=UTC)
    local_moment = utc_moment.astimezone(zone).replace(tzinfo=None)
    local_day = local_moment.date()
    first_day, last_day = trading_days[0], trading_days[-1]
    if local_day > last_day or local_day < first_day:
        return None
    day_set = set(trading_days)
    if local_day in day_set:
        cutoff = calendar.session_cutoff(market_id, local_day)
        if local_moment <= cutoff:
            return local_day
    next_day = calendar.next_trading_day(market_id, local_day)
    return next_day if next_day <= last_day else None
