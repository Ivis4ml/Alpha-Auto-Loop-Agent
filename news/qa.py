"""对话问答：围绕标的与新闻的证据式回答。

流程：查询解析（确定性规则优先）→ point-in-time 检索 → 证据编号组装
→ 生成（证据段句尾挂 [n] 引用；背景知识段显式冠名；证据不足走固定
模板如实说明）→ 痕迹落盘。纯交互问答不计入试验计数；as_of 给定时
回答首行申明时点边界。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from alphaloop.llm.client import LlmClient
from news.entity import EntityLinker
from news.retrieval import Retriever, ScoredNews

__all__ = ["Citation", "Answer", "QaEngine", "QA_PROMPT_VERSION"]

QA_PROMPT_VERSION = "1"

_SYSTEM = (
    "你是金融新闻问答助手。依据提供的编号证据回答用户问题，遵守以下规则："
    "回答分为两个部分，第一部分标题为【新闻证据】，其中每个论断句末尾必须"
    "标注所依据的证据编号（如 [1]）；第二部分标题为【背景知识】，明确写出"
    "“以下为模型背景知识，非新闻证据”后再陈述。不得编造证据编号，"
    "不得把背景知识混入证据部分。证据与问题无关时如实说明。使用正式书面中文。"
)

_INSUFFICIENT_TEMPLATE = (
    "截至 {as_of}，未检索到足以支撑判断的相关新闻。"
    "以下回答仅基于模型背景知识，请谨慎参考。"
)


@dataclass(frozen=True, slots=True)
class Citation:
    """一条引用。"""

    index: int
    news_id: str
    source: str
    publish_ts: str
    visible_ts: str
    title: str


@dataclass(frozen=True, slots=True)
class Answer:
    """一次问答结果。"""

    text: str
    citations: tuple[Citation, ...]
    evidence_mode: Literal["evidence", "insufficient"]
    as_of: str | None
    trace: dict[str, object] = field(repr=False, default_factory=dict)


class QaEngine:
    """问答引擎。client 为 None 时使用确定性证据罗列回答（离线兜底）。"""

    def __init__(
        self,
        retriever: Retriever,
        linker: EntityLinker,
        client: LlmClient | None = None,
        *,
        model: str = "",
        top_k: int = 8,
    ) -> None:
        self.retriever = retriever
        self.linker = linker
        self.client = client
        self.model = model
        self.top_k = top_k

    def answer(
        self,
        question: str,
        *,
        symbol: str | None = None,
        as_of: str | None = None,
    ) -> Answer:
        """回答问题。symbol 显式给定时优先于文本内识别。"""
        symbols = [symbol] if symbol else self._detect_symbols(question)
        results = self.retriever.search(
            question, symbols=symbols or None, as_of=as_of, top_k=self.top_k
        )
        if not results and symbols:
            results = self.retriever.search(question, as_of=as_of, top_k=self.top_k)
        trace: dict[str, object] = {
            "question": question,
            "symbols": symbols,
            "as_of": as_of,
            "candidate_news_ids": [item.news_id for item in results],
            "prompt_version": QA_PROMPT_VERSION,
        }
        preamble = (
            f"（以下回答仅基于 {as_of} 之前市场可见的新闻。）\n" if as_of is not None else ""
        )
        if not results:
            text = preamble + _INSUFFICIENT_TEMPLATE.format(as_of=as_of or "当前时点")
            return Answer(
                text=text, citations=(), evidence_mode="insufficient", as_of=as_of, trace=trace
            )
        citations = tuple(
            Citation(
                index=index + 1,
                news_id=item.news_id,
                source=item.source,
                publish_ts=item.publish_ts,
                visible_ts=item.visible_ts,
                title=item.title,
            )
            for index, item in enumerate(results)
        )
        body = self._generate(question, results, trace)
        return Answer(
            text=preamble + body,
            citations=citations,
            evidence_mode="evidence",
            as_of=as_of,
            trace=trace,
        )

    def _detect_symbols(self, question: str) -> list[str]:
        links = self.linker.link("question", question, question)
        return [link.symbol for link in links if link.confidence >= 0.75]

    def _generate(
        self, question: str, results: list[ScoredNews], trace: dict[str, object]
    ) -> str:
        evidence_lines = [
            f"[{index + 1}] （{item.visible_ts}，{item.source}）{item.title} {item.snippet}"
            for index, item in enumerate(results)
        ]
        if self.client is None:
            listing = "\n".join(evidence_lines)
            return (
                "【新闻证据】\n检索到以下相关新闻，请结合原文判断：\n"
                + listing
                + "\n【背景知识】\n以下为模型背景知识，非新闻证据：离线模式下不生成推断性结论。"
            )
        prompt = "问题: " + question + "\n证据列表:\n" + "\n".join(evidence_lines)
        trace["llm_cache_key"] = self.client.cache_key(
            model=self.model, system=_SYSTEM, prompt=prompt, temperature=0.0
        )
        return self.client.complete(model=self.model, system=_SYSTEM, prompt=prompt)
