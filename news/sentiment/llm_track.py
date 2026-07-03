"""语言模型情绪轨：对（新闻，标的）对做相关性与极性判断。

对历史新闻事后打分的产出一律 model_backfilled=True（模型回算值），
与真实历史区分；打分经三态缓存，replay 模式下可离线复现。打分提示词
保持市场中立。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from alphaloop.llm.client import LlmClient
from alphaloop.llm.parse import extract_json_objects

__all__ = ["LlmSentiment", "LlmSentimentScorer", "SENTIMENT_PROMPT_VERSION"]

SENTIMENT_PROMPT_VERSION = "1"

_SYSTEM = (
    "你是金融新闻分析员。给定一条新闻与一个标的，判断新闻与该标的的相关性"
    "与影响方向。输出单个 JSON 对象："
    '{"relevance": 0 到 1 的小数, "polarity": -1 到 1 的小数, '
    '"confidence": 0 到 1 的小数, "event_type": 事件类型字符串或 null, '
    '"rationale": 一句话依据}。只输出 JSON，不要输出其他文字。'
)


@dataclass(frozen=True, slots=True)
class LlmSentiment:
    """一条打分结果。"""

    news_id: str
    symbol: str
    relevance: float
    polarity: float
    confidence: float
    event_type: str | None
    rationale: str
    model_backfilled: bool
    cache_key: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


class LlmSentimentScorer:
    """语言模型打分器。"""

    def __init__(
        self,
        client: LlmClient,
        model: str,
        *,
        prompt_version: str = SENTIMENT_PROMPT_VERSION,
    ) -> None:
        self.client = client
        self.model = model
        self.prompt_version = prompt_version

    def score(
        self, *, news_id: str, symbol: str, title: str, content: str
    ) -> LlmSentiment:
        """对一个（新闻，标的）对打分。解析失败按零相关处理并保留痕迹。"""
        prompt = (
            f"提示词版本: {self.prompt_version}\n标的: {symbol}\n"
            f"新闻标题: {title}\n新闻正文: {content[:1500]}"
        )
        cache_key = self.client.cache_key(
            model=self.model, system=_SYSTEM, prompt=prompt, temperature=0.0
        )
        text = self.client.complete(model=self.model, system=_SYSTEM, prompt=prompt)
        objects = extract_json_objects(text)
        if not objects:
            return LlmSentiment(
                news_id=news_id,
                symbol=symbol,
                relevance=0.0,
                polarity=0.0,
                confidence=0.0,
                event_type=None,
                rationale="模型输出不可解析",
                model_backfilled=True,
                cache_key=cache_key,
            )
        payload = objects[0]
        return LlmSentiment(
            news_id=news_id,
            symbol=symbol,
            relevance=_clip(payload.get("relevance"), 0.0, 1.0),
            polarity=_clip(payload.get("polarity"), -1.0, 1.0),
            confidence=_clip(payload.get("confidence"), 0.0, 1.0),
            event_type=(
                str(payload["event_type"]) if payload.get("event_type") is not None else None
            ),
            rationale=str(payload.get("rationale", "")),
            model_backfilled=True,
            cache_key=cache_key,
        )


def _clip(value: object, low: float, high: float) -> float:
    if not isinstance(value, int | float):
        return 0.0
    return max(low, min(high, float(value)))
