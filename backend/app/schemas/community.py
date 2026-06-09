"""
T3 커뮤니티 비교 API Pydantic schemas (P3-3).

6 endpoint:
- GET /api/v1/community/platforms/health
- GET /api/v1/community/platforms/product-matrix
- GET /api/v1/community/platforms/dispersion
- GET /api/v1/community/platforms/early-signal
- GET /api/v1/community/platforms/clusters
- GET /api/v1/community/platforms/anomalies

source MV:
- platform_health (1행/플랫폼)
- country_daily   (1행/day×country×product)
- voc_records     (라이브 — dispersion / early-signal 일부 계산용)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── 1) /health ─────────────────────────────────────────────────────
class PlatformHealth(BaseModel):
    platform_id: int
    code: str
    region: Optional[str] = None
    base_url: Optional[str] = None
    posts_24h: int
    posts_7d: int
    sent_avg_7d: Optional[float] = None
    avg_body_len_7d: Optional[int] = None
    last_collected: Optional[str] = None  # ISO timestamp
    status: str                            # 'active' | 'idle' | 'dead'


class PlatformHealthResponse(BaseModel):
    items: List[PlatformHealth]
    total: int
    active: int
    idle: int
    dead: int


# ── 2) /product-matrix ─────────────────────────────────────────────
class MatrixCell(BaseModel):
    platform: str
    product: str
    n: int
    sent_avg: float                        # -1 ~ 1
    neg_rate: float                        # 0 ~ 100


class ProductMatrixResponse(BaseModel):
    cells: List[MatrixCell]
    platforms: List[str]
    products: List[str]
    since: str


# ── 3) /dispersion ─────────────────────────────────────────────────
class BoxplotEntry(BaseModel):
    platform: str
    q1: float
    median: float
    q3: float
    iqr: float
    lo: float                              # whisker low (q1 - 1.5*iqr clipped to min)
    hi: float                              # whisker high
    n: int


class OutlierEntry(BaseModel):
    platform: str
    voc_id: int
    sentiment_score: float
    snippet: Optional[str] = None


class DispersionResponse(BaseModel):
    boxplot: List[BoxplotEntry]
    outliers: List[OutlierEntry]
    product_id: Optional[int] = None
    since: str


# ── 4) /early-signal ───────────────────────────────────────────────
class EarlySignalTimelinePoint(BaseModel):
    platform: str
    day: str                                # YYYY-MM-DD
    n: int
    sent_avg: float


class EarlySignalEvent(BaseModel):
    detected_at: str                        # YYYY-MM-DD
    leading_platform: Optional[str] = None
    lead_days: Optional[int] = None
    summary: str


class EarlySignalResponse(BaseModel):
    event: Optional[EarlySignalEvent] = None
    timeline: List[EarlySignalTimelinePoint]
    product_id: Optional[int] = None
    category: Optional[str] = None


# ── 5) /clusters ───────────────────────────────────────────────────
class ClusterPoint(BaseModel):
    platform: str
    cluster: int
    x: float                                # 2D 좌표 (PCA-lite: pos_rate vs neg_rate)
    y: float


class ClusterCentroid(BaseModel):
    cluster: int
    x: float
    y: float
    size: int                               # 멤버 개수
    pos_rate_avg: float
    neg_rate_avg: float
    sent_avg: float


class ClustersResponse(BaseModel):
    points: List[ClusterPoint]
    centroids: List[ClusterCentroid]
    k: int
    iterations: int


# ── 6) /anomalies ──────────────────────────────────────────────────
class AnomalyEntry(BaseModel):
    code: str                               # platform code
    reason: str                             # 'dead_7d' | 'idle_24h' | 'extreme_negative' | ...
    since: str                              # ISO timestamp or 'YYYY-MM-DD'
    detail: Dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "PlatformHealth",
    "PlatformHealthResponse",
    "MatrixCell",
    "ProductMatrixResponse",
    "BoxplotEntry",
    "OutlierEntry",
    "DispersionResponse",
    "EarlySignalTimelinePoint",
    "EarlySignalEvent",
    "EarlySignalResponse",
    "ClusterPoint",
    "ClusterCentroid",
    "ClustersResponse",
    "AnomalyEntry",
]
