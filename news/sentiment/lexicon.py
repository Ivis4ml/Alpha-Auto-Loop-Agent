"""事件词典轨：确定性金融事件识别与打分。

词典按事件类型组织（触发子串、极性、强度），版本化存储于同目录 JSON
文件，版本内容参与情绪面板的构建配置哈希。打分为纯函数：每个事件
类型在单条文本内只计一次，得分为 Σ(极性 × 强度)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from alphaloop.infra.hashing import content_hash

__all__ = ["LEXICON_VERSION", "EventLexicon", "load_default_lexicon"]

LEXICON_VERSION = "1"
_DEFAULT_PATH = Path(__file__).resolve().parent / f"event_lexicon_v{LEXICON_VERSION}.json"


@dataclass(frozen=True, slots=True)
class EventRule:
    """单个事件类型的识别规则。"""

    event_type: str
    patterns: tuple[str, ...]
    polarity: int
    strength: int


@dataclass(frozen=True, slots=True)
class LexiconScore:
    """一条文本的词典打分结果。"""

    score: float
    events: tuple[str, ...]


class EventLexicon:
    """事件词典。"""

    def __init__(self, rules: list[EventRule], *, version: str) -> None:
        if not rules:
            raise ValueError("事件词典为空")
        self.rules = rules
        self.version = version

    def config_hash(self) -> str:
        """词典内容哈希，参与面板构建配置。"""
        payload = [
            {
                "event_type": rule.event_type,
                "patterns": list(rule.patterns),
                "polarity": rule.polarity,
                "strength": rule.strength,
            }
            for rule in self.rules
        ]
        return content_hash("event-lexicon", self.version, payload)

    def score(self, text: str) -> LexiconScore:
        """打分：每个事件类型只计一次。"""
        total = 0.0
        hit_events: list[str] = []
        for rule in self.rules:
            if any(pattern in text for pattern in rule.patterns):
                total += rule.polarity * rule.strength
                hit_events.append(rule.event_type)
        return LexiconScore(score=total, events=tuple(hit_events))


def load_default_lexicon() -> EventLexicon:
    """加载缺省版本词典。"""
    payload = json.loads(_DEFAULT_PATH.read_text(encoding="utf-8"))
    rules = [
        EventRule(
            event_type=str(item["event_type"]),
            patterns=tuple(str(pattern) for pattern in item["patterns"]),
            polarity=int(item["polarity"]),
            strength=int(item["strength"]),
        )
        for item in payload["events"]
    ]
    return EventLexicon(rules, version=str(payload["version"]))
