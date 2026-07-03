"""情绪双轨：确定性事件词典轨与语言模型轨。

词典轨是确定性纯函数、离线可复现，作为语言模型轨不可用时的兜底；
语言模型轨对历史新闻事后打分的产出一律标记为模型回算值
（model_backfilled），与真实历史区分。
"""

from news.sentiment.lexicon import LEXICON_VERSION, EventLexicon, load_default_lexicon
from news.sentiment.llm_track import LlmSentiment, LlmSentimentScorer
from news.sentiment.panel import build_sentiment_panel

__all__ = [
    "EventLexicon",
    "LEXICON_VERSION",
    "load_default_lexicon",
    "LlmSentiment",
    "LlmSentimentScorer",
    "build_sentiment_panel",
]
