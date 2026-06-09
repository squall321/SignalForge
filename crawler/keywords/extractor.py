"""키워드 추출 — 한국어/영어 다국어, 외부 NLP 패키지 없이 정규식 기반.

설계 원칙:
- KoNLPy/spaCy 같은 무거운 의존성 회피 (런타임 안정성 우선).
- 한국어: 한글 토큰(2글자 이상) + 불용어/조사 어미 제거.
- 영어: 알파벳 토큰(3글자 이상) + 불용어 + lowercase.
- 너무 흔한 키워드(갤럭시, 삼성, Samsung 등)는 가중치 ↓.
- 길이 cutoff 와 빈도 기반 단순 점수.

사용:
    from keywords.extractor import extract
    extract("S25 Ultra 배터리 진짜 좋네요", lang="ko")
    # -> [("S25", 1.0), ("울트라", 0.8), ("배터리", 1.0), ("좋네요", 0.6)]
"""
from __future__ import annotations

import re
from collections import Counter
from typing import List, Optional, Tuple

# ── 불용어 사전 ────────────────────────────────────────────────────────
# 한국어: 자주 등장하지만 의미 없는 조사/어미/대명사
STOPWORDS_KO = {
    "있다", "없다", "하다", "되다", "이다", "그것", "저것", "것이", "것은", "그래도",
    "그리고", "하지만", "그러나", "그래서", "근데", "그런데", "하지", "이게", "저게",
    "여기", "저기", "거기", "이거", "저거", "어떻게", "왜냐하면", "때문에", "그런",
    "이런", "저런", "어떤", "어느", "무슨", "그냥", "정말", "진짜", "너무", "되게",
    "많이", "조금", "약간", "아주", "되고", "되면", "되네", "되어", "있는", "있어",
    "있고", "없는", "없어", "지금", "오늘", "내일", "어제", "이번", "다음", "한번",
    "어떤가요", "어떻게요", "그래요", "맞아요", "그래", "그럼", "이제", "방금", "곧",
    "다시", "또는", "그게", "이게", "저게", "거기", "혹은", "처음", "마지막", "결국",
}

# 영어: 일반 불용어
STOPWORDS_EN = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "her", "was",
    "one", "our", "out", "day", "get", "has", "him", "his", "how", "man", "new",
    "now", "old", "see", "two", "way", "who", "boy", "did", "its", "let", "put",
    "say", "she", "too", "use", "any", "had", "yet", "this", "that", "with",
    "from", "have", "they", "will", "your", "what", "when", "make", "like",
    "time", "just", "know", "take", "into", "year", "good", "some", "could",
    "them", "see", "than", "then", "look", "only", "come", "over", "think",
    "also", "back", "after", "first", "well", "even", "want", "because",
    "these", "give", "most", "where", "much", "such", "find", "here", "thing",
    "really", "very", "quite", "still", "every", "made", "should", "would",
    "many", "those", "their", "which", "about", "would",
}

# 가중치 낮춤 — 너무 흔해서 정보량 낮은 키워드
LOW_WEIGHT_TERMS = {
    "갤럭시", "삼성", "폰", "스마트폰", "휴대폰", "기종",
    "samsung", "galaxy", "phone", "smartphone", "android", "mobile",
}

# 한국어 토큰 패턴: 한글 + 영문 + 숫자 (한 토큰)
RE_TOKEN_KO = re.compile(r"[가-힣A-Za-z0-9]{2,20}")
# 영어 토큰 패턴: 알파벳 + 숫자 (3글자 이상)
RE_TOKEN_EN = re.compile(r"[A-Za-z][A-Za-z0-9]{2,19}")

# 한국어 끝 조사/어미 제거 (단순 휴리스틱)
RE_KO_SUFFIX = re.compile(
    r"(은|는|이|가|을|를|에|의|로|와|과|도|만|에서|에게|한테|부터|까지|"
    r"이다|입니다|입니까|입니다만|네요|군요|이요|이요|니까|구나|구먼|"
    r"습니다|어요|아요|예요|에요|이에요|는데|하면|면서|지만|던데|"
    r"이라|라는|이라는|라는|에서도|이고|이라서)$"
)


def _strip_korean_suffix(token: str) -> str:
    """한국어 토큰 끝의 조사/어미 제거 (단순 휴리스틱).

    완벽한 형태소 분석이 아니라 빈도 집계의 정확도를 약간 올리는 정도.
    """
    if not token or not _is_hangul(token[0]):
        return token
    prev = token
    for _ in range(2):  # 이중 조사 (예: '에서도') 처리
        new = RE_KO_SUFFIX.sub("", prev)
        if new == prev or len(new) < 2:
            break
        prev = new
    return prev


def _is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣"


def extract(text: str, lang: Optional[str] = None, top_n: int = 30) -> List[Tuple[str, float]]:
    """텍스트에서 키워드 + 가중치 추출.

    Args:
        text: 본문
        lang: 'ko' / 'en' / 'auto' (None=auto)
        top_n: 상위 N개만 반환

    Returns:
        [(keyword, weight), ...] — weight 내림차순. 빈 리스트 가능.
    """
    if not text or not text.strip():
        return []

    if lang is None or lang == "auto":
        lang = "ko" if any(_is_hangul(c) for c in text[:200]) else "en"

    if lang == "ko":
        tokens = RE_TOKEN_KO.findall(text)
        tokens = [_strip_korean_suffix(t) for t in tokens]
        stopwords = STOPWORDS_KO
        min_len = 2
    else:
        tokens = RE_TOKEN_EN.findall(text)
        tokens = [t.lower() for t in tokens]
        stopwords = STOPWORDS_EN
        min_len = 3

    # 필터: 길이/불용어
    cleaned = [t for t in tokens if len(t) >= min_len and t.lower() not in stopwords]
    if not cleaned:
        return []

    # 빈도 → 가중치 (저빈도 0.5 ~ 고빈도 1.5)
    cnt = Counter(cleaned)
    max_n = max(cnt.values())
    out: List[Tuple[str, float]] = []
    for kw, n in cnt.most_common(top_n):
        # 기본 가중치 = log scale 빈도 정규화
        base = 0.5 + 1.0 * (n / max_n)
        # 너무 흔한 키워드 가중치 ↓
        if kw.lower() in LOW_WEIGHT_TERMS:
            base *= 0.4
        out.append((kw, round(base, 3)))
    return out
