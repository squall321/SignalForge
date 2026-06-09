"""
T2 시계열+LLM 분석 Pydantic schemas (P2-3).

엔드포인트:
- GET  /api/v1/analytics/temporal-series
- GET  /api/v1/analytics/temporal-compare
- POST /api/v1/analytics/llm-narrative
"""
from __future__ import annotations

from typing import List, Optional, Literal, Dict, Any

from pydantic import BaseModel, Field


# ── 공통 ─────────────────────────────────────────────────────────────
class SeriesPoint(BaseModel):
    date: str                       # 'YYYY-MM-DD'
    count: int
    sent_avg: float                 # -1 ~ 1
    neg_rate: float                 # 0 ~ 100
    pos_rate: float                 # 0 ~ 100


class TimelineEvent(BaseModel):
    date: str                       # 'YYYY-MM-DD'
    type: str                       # 'release' | 'update' | 'incident' | 'pr'
    title: str
    product_code: Optional[str] = None
    source_url: Optional[str] = None


class ChangePoint(BaseModel):
    date: str
    metric: Literal["count", "sent_avg"]
    magnitude: float                # |delta| (절대값)
    direction: Literal["up", "down"]


# ── 1) temporal-series ──────────────────────────────────────────────
class TemporalSeriesResponse(BaseModel):
    series: List[SeriesPoint]
    events: List[TimelineEvent] = Field(default_factory=list)
    changepoints: List[ChangePoint] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 2) temporal-compare ─────────────────────────────────────────────
class CompareSeries(BaseModel):
    key: str
    points: List[SeriesPoint]


class DiffPoint(BaseModel):
    date: str
    delta_count: int                # a - b
    delta_sent: float               # a - b


class TemporalCompareResponse(BaseModel):
    mode: Literal["products", "periods", "categories"]
    a: CompareSeries
    b: CompareSeries
    diff: List[DiffPoint]


# ── 3) llm-narrative ────────────────────────────────────────────────
class LLMNarrativeRequest(BaseModel):
    series_payload: Dict[str, Any]
    lang: str = "ko"


class NarrativeCitation(BaseModel):
    event_date: str
    source_url: Optional[str] = None
    title: Optional[str] = None


class LLMNarrativeResponse(BaseModel):
    summary: str
    citations: List[NarrativeCitation] = Field(default_factory=list)
    cached: bool = False
    provider: Optional[str] = None


__all__ = [
    "SeriesPoint",
    "TimelineEvent",
    "ChangePoint",
    "TemporalSeriesResponse",
    "CompareSeries",
    "DiffPoint",
    "TemporalCompareResponse",
    "LLMNarrativeRequest",
    "NarrativeCitation",
    "LLMNarrativeResponse",
]
