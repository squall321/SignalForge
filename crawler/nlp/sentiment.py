"""감성 분석 — VADER 기반 (빠른 처리)"""
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from typing import Tuple

_analyzer = SentimentIntensityAnalyzer()

# 기술 제품 리뷰 특화 어휘 보정
# VADER가 놓치는 기술 문맥 부정 표현에 가중치 부여
_TECH_LEXICON: dict = {
    "runs out": -2.0,
    "drains fast": -2.0,
    "drains quickly": -2.0,
    "doesn't last": -2.5,
    "does not last": -2.5,
    "only lasts": -1.5,
    "short battery": -2.0,
    "creaking": -1.8,
    "creak": -1.5,
    "crackling": -1.8,
    "overheating": -2.5,
    "overheat": -2.0,
    "throttling": -2.0,
    "lagging": -2.0,
    "freezes": -2.0,
    "bricked": -3.0,
    "defective": -2.5,
    "returned": -1.5,
    "returning": -1.5,
    "disappointing": -2.0,
    "regret": -2.0,
    "waste of money": -3.0,
    "not worth": -2.5,
    "overpriced": -2.0,
    "not recommended": -2.5,
    "avoid": -2.0,
}
_analyzer.lexicon.update(_TECH_LEXICON)

# 기술 제품 맥락에서 compound 임계값을 높여 false-positive 방지
_POS_THRESHOLD = 0.20
_NEG_THRESHOLD = -0.20


# @lat: analyze_sentiment — [[nlp#Sentiment Analysis]] 참조.
def analyze_sentiment(text: str) -> Tuple[float, str]:
    """
    VADER 감성 분석.

    기술 제품 리뷰 특화 어휘를 VADER lexicon에 추가 적용.
    compound 임계값: +0.20 이상 positive, -0.20 이하 negative.

    Returns:
        (score, label)
        score: -1.0 (매우 부정) ~ 1.0 (매우 긍정)
        label: 'positive' | 'negative' | 'neutral'
    """
    if not text:
        return 0.0, "neutral"

    scores = _analyzer.polarity_scores(text[:1000])
    compound = scores["compound"]

    if compound >= _POS_THRESHOLD:
        label = "positive"
    elif compound <= _NEG_THRESHOLD:
        label = "negative"
    else:
        label = "neutral"

    return round(compound, 4), label


# @lat: analyze — 언어 인지 감성 디스패처.
def analyze(text: str, lang: str | None = None) -> Tuple[float, str]:
    """언어에 따라 감성 분석기 선택.

    한국어는 번역 의존 없이 원문에서 직접 극성 산출(한국어 사전).
    그 외(영어/번역본)는 VADER. 데이터의 94%가 한국어이므로
    번역 실패와 무관하게 의미 있는 sentiment 를 보장한다.
    """
    if lang == "ko":
        from nlp.sentiment_ko import analyze_sentiment_ko
        return analyze_sentiment_ko(text)
    return analyze_sentiment(text)
