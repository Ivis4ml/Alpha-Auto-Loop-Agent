"""实体链接：新闻文本到标的代码的关联。

词表来自标的清单（symbol 与名称二元组由装配层提供，本模块不读取行情
库）；链接用 Aho-Corasick 多模式精确匹配，不做模糊匹配（电报为编辑
发布的规范文本）。置信度分级：代码命中 1.0，长度不小于 3 的唯一简称
0.9（出现在标题另加 0.05），两字或歧义别名 0.5 进入待裁决队列（后续
里程碑交语言模型批量裁决）。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import ahocorasick

__all__ = ["EntityLink", "AliasEntry", "build_alias_entries", "EntityLinker"]

_CONFIDENCE_CODE = 1.0
_CONFIDENCE_NAME = 0.9
_CONFIDENCE_TITLE_BONUS = 0.05
_CONFIDENCE_AMBIGUOUS = 0.5
DEFAULT_CONFIDENCE_THRESHOLD = 0.75


@dataclass(frozen=True, slots=True)
class AliasEntry:
    """一条别名记录。"""

    alias: str
    symbol: str
    alias_type: str


@dataclass(frozen=True, slots=True)
class EntityLink:
    """一条链接结果。"""

    news_id: str
    symbol: str
    method: str
    confidence: float
    span_start: int
    span_end: int


def normalize_text(value: str) -> str:
    """全角转半角并去除全部空白（标的名称如"万  科Ａ"需要归一）。"""
    return "".join(unicodedata.normalize("NFKC", value).split())


def build_alias_entries(listing: list[tuple[str, str]]) -> tuple[list[AliasEntry], list[str]]:
    """由 (symbol, name) 清单构建别名表。

    返回 (别名表, 被丢弃的歧义别名列表)。机械层别名：六位代码、带后缀
    代码、归一化简称、ST 前缀剥离变体。映射到多个标的的名称别名视为
    歧义并丢弃（代码别名不会歧义）。
    """
    candidate: dict[str, list[AliasEntry]] = {}

    def add(alias: str, symbol: str, alias_type: str) -> None:
        if not alias:
            return
        candidate.setdefault(alias, []).append(AliasEntry(alias, symbol, alias_type))

    for symbol, raw_name in listing:
        code, _, _suffix = symbol.partition(".")
        add(symbol, symbol, "code_suffixed")
        if code.isdigit() and len(code) == 6:
            add(code, symbol, "code6")
        name = normalize_text(raw_name or "")
        if name:
            add(name, symbol, "short_name")
            for prefix in ("*ST", "ST"):
                if name.startswith(prefix) and len(name) > len(prefix):
                    add(name[len(prefix) :], symbol, "st_variant")

    entries: list[AliasEntry] = []
    ambiguous: list[str] = []
    for alias, group in candidate.items():
        symbols = {entry.symbol for entry in group}
        if len(symbols) > 1 and not group[0].alias_type.startswith("code"):
            ambiguous.append(alias)
            continue
        entries.append(group[0])
    return entries, sorted(ambiguous)


class EntityLinker:
    """Aho-Corasick 精确匹配链接器。"""

    def __init__(self, entries: list[AliasEntry]) -> None:
        self._automaton = ahocorasick.Automaton()
        for entry in entries:
            existing = (
                self._automaton.get(entry.alias) if entry.alias in self._automaton else []
            )
            self._automaton.add_word(entry.alias, [*existing, entry])
        self._automaton.make_automaton()

    def link(self, news_id: str, title: str, content: str) -> list[EntityLink]:
        """链接一条新闻，返回按标的去重（保留最高置信度）的结果。"""
        title_normalized = normalize_text(title)
        text = title_normalized + "\n" + normalize_text(content)
        best: dict[str, EntityLink] = {}
        for end_index, entries in self._automaton.iter(text):
            for entry in entries:
                start_index = end_index - len(entry.alias) + 1
                confidence, method = self._score(entry, start_index, len(title_normalized))
                link = EntityLink(
                    news_id=news_id,
                    symbol=entry.symbol,
                    method=method,
                    confidence=confidence,
                    span_start=start_index,
                    span_end=end_index + 1,
                )
                current = best.get(entry.symbol)
                if current is None or link.confidence > current.confidence:
                    best[entry.symbol] = link
        return sorted(best.values(), key=lambda item: item.symbol)

    @staticmethod
    def _score(entry: AliasEntry, start_index: int, title_length: int) -> tuple[float, str]:
        if entry.alias_type.startswith("code"):
            return _CONFIDENCE_CODE, "code"
        if len(entry.alias) >= 3:
            confidence = _CONFIDENCE_NAME
            if start_index < title_length:
                confidence += _CONFIDENCE_TITLE_BONUS
            return confidence, "alias_exact"
        return _CONFIDENCE_AMBIGUOUS, "alias_short"
