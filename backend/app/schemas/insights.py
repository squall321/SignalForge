"""
T4 딥 인사이트 Pydantic schemas (P4 트랙 C).

7 endpoint:
- GET /api/v1/insights/hourly-pattern
- GET /api/v1/insights/weekday-pattern
- GET /api/v1/insights/emerging-keywords
- GET /api/v1/insights/new-terms
- GET /api/v1/insights/sentiment-swing
- GET /api/v1/insights/product-lifecycle
- GET /api/v1/insights/platform-influence

데이터 소스:
- voc_records         : published_at(시·요일), sentiment, platform_id, product_id
- voc_keywords        : keyword/lang/voc_id  (join voc_records.published_at)
- timeline_events     : product release date  (lifecycle anchor)
- platforms           : code/region/base_url (influence drivers)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── 1) hourly-pattern ─────────────────────────────────────────────
class HourlyPoint(BaseModel):
    hour: int                       # 0..23
    count: int
    sent_avg: float                 # -1 ~ 1


class HourlyPatternResponse(BaseModel):
    points: List[HourlyPoint]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 2) weekday-pattern ────────────────────────────────────────────
class WeekdayPoint(BaseModel):
    weekday: int                    # 0=Mon .. 6=Sun
    label: str                      # 'Mon'..'Sun'
    count: int
    sent_avg: float
    neg_rate: float                 # 0 ~ 100


class WeekdayPatternResponse(BaseModel):
    points: List[WeekdayPoint]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 3) emerging-keywords ──────────────────────────────────────────
class KeywordTrend(BaseModel):
    keyword: str
    lang: Optional[str] = None
    prev_week_count: int
    this_week_count: int
    growth_pct: float               # (this-prev)/max(prev,1) * 100


class EmergingKeywordsResponse(BaseModel):
    emerging: List[KeywordTrend]
    declining: List[KeywordTrend]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 4) new-terms ──────────────────────────────────────────────────
class NewTermEntry(BaseModel):
    keyword: str
    lang: Optional[str] = None
    first_seen: str                 # 'YYYY-MM-DD'
    count_recent: int               # within recent period_days


class NewTermsResponse(BaseModel):
    items: List[NewTermEntry]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 5) sentiment-swing ────────────────────────────────────────────
class SentimentSwingEntry(BaseModel):
    product: str
    before_sent: float
    after_sent: float
    delta_pp: float                 # after - before (점수, -2 ~ 2)
    n_before: int
    n_after: int


class SentimentSwingResponse(BaseModel):
    items: List[SentimentSwingEntry]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 6) product-lifecycle ──────────────────────────────────────────
class LifecyclePoint(BaseModel):
    d_offset: int                   # 0/7/30/90/180
    window_from: str                # 'YYYY-MM-DD'
    window_to: str                  # 'YYYY-MM-DD'
    count: int
    sent_avg: float
    top_categories: List[str] = Field(default_factory=list)


class ProductLifecycleResponse(BaseModel):
    product: str
    release_date: Optional[str] = None
    points: List[LifecyclePoint]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 7) platform-influence ─────────────────────────────────────────
class InfluenceDrivers(BaseModel):
    engagement: float               # avg(likes+comments+shares)
    neg_rate: float                 # 0 ~ 100
    lag_days: float                 # 다른 사이트 대비 평균 선행/지연일 (음수=선행)


class PlatformInfluenceEntry(BaseModel):
    platform: str
    region: Optional[str] = None
    score: float                    # 0 ~ 100 정규화
    n: int                          # period 게시량
    drivers: InfluenceDrivers


class PlatformInfluenceResponse(BaseModel):
    items: List[PlatformInfluenceEntry]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 8) compare-llm (트랙 D) ──────────────────────────────────────
class CompareLLMRequest(BaseModel):
    """제품 코드 2~4개 + 기간(일).  /api/v1/insights/compare-llm body."""
    products: List[str] = Field(..., min_length=2, max_length=4)
    period_days: int = Field(30, ge=7, le=180)


class CompareLLMResponse(BaseModel):
    narrative: Optional[str] = None  # LLM 미설정/실패 시 None
    tier_label: str                  # 'high-shared:qwen2.5:14b' 등
    grounding_score: float           # 0~1
    generated_at: str                # ISO8601 UTC
    products: List[str]              # 입력 확정값
    period_days: int


__all__ = [
    "HourlyPoint",
    "HourlyPatternResponse",
    "WeekdayPoint",
    "WeekdayPatternResponse",
    "KeywordTrend",
    "EmergingKeywordsResponse",
    "NewTermEntry",
    "NewTermsResponse",
    "SentimentSwingEntry",
    "SentimentSwingResponse",
    "LifecyclePoint",
    "ProductLifecycleResponse",
    "InfluenceDrivers",
    "PlatformInfluenceEntry",
    "PlatformInfluenceResponse",
    "CompareLLMRequest",
    "CompareLLMResponse",
]
