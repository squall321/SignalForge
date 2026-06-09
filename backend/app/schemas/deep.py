"""
P3.6 트랙 A — 심층 분석 8 endpoint Pydantic 스키마.

prefix: /api/v1/deep
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── 1) issue-lifecycle ────────────────────────────────────────────
class LifecycleItem(BaseModel):
    category: Optional[str] = None
    keyword: str
    first_seen: str               # YYYY-MM-DD
    peak_day: str
    last_seen: str
    days_to_peak: int
    lifespan: int
    intensity: int                # peak day count


class LifecycleCategoryAvg(BaseModel):
    category: str
    avg_lifespan: float
    avg_days_to_peak: float
    n_issues: int


class IssueLifecycleResponse(BaseModel):
    items: List[LifecycleItem]
    category_avg: List[LifecycleCategoryAvg]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 2) category-product-matrix ────────────────────────────────────
class MatrixCell(BaseModel):
    product: str
    category: str
    score: float
    n: int
    zscore: Optional[float] = None
    flag: str                     # 'outlier_neg' | 'outlier_pos' | 'normal'


class CategoryProductMatrixResponse(BaseModel):
    products: List[str]
    categories: List[str]
    cells: List[MatrixCell]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 3) site-diffusion ─────────────────────────────────────────────
class DiffusionHop(BaseModel):
    site: str
    first_seen: str
    hop: int
    lag_days: Optional[int] = None


class DiffusionKeyword(BaseModel):
    keyword: str
    path: List[DiffusionHop]
    total_span_days: int
    origin_site: str
    terminal_site: str


class DiffusionEdge(BaseModel):
    from_site: str
    to_site: str
    count: int
    avg_lag: float


class SiteDiffusionResponse(BaseModel):
    keywords: List[DiffusionKeyword]
    edges: List[DiffusionEdge]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 4) country-sentiment-gap ──────────────────────────────────────
class CountrySentimentItem(BaseModel):
    product: str
    country: str
    score: float                  # -1..1
    n: int
    gap_vs_global: float


class TopGapEntry(BaseModel):
    product: str
    country_high: str
    country_low: str
    gap: float


class CountrySentimentGapResponse(BaseModel):
    items: List[CountrySentimentItem]
    top_gaps: List[TopGapEntry]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 5) engagement-sentiment ───────────────────────────────────────
class EngagementBucket(BaseModel):
    bucket: int                   # 1..5
    eng_range: str                # 'min~max'
    score: float
    neg_ratio: float
    n: int


class EngagementByCategory(BaseModel):
    category: str
    corr_eng_neg: float           # spearman-like sign indicator
    top_bucket: int


class EngagementSentimentResponse(BaseModel):
    buckets: List[EngagementBucket]
    by_category: List[EngagementByCategory]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 6) new-term-survival ──────────────────────────────────────────
class SurvivalItem(BaseModel):
    keyword: str
    first_day: str
    last_day: str
    survival_days: int
    active_days: int
    total: int
    cls: str                      # 'sustained' | 'mid' | 'flash'


class SurvivalSummary(BaseModel):
    sustained: int
    mid: int
    flash: int
    avg_survival: float


class NewTermSurvivalResponse(BaseModel):
    items: List[SurvivalItem]
    summary: SurvivalSummary
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 7) keyword-cooccurrence ───────────────────────────────────────
class CooccurNode(BaseModel):
    id: str
    degree: int
    sentiment_bias: float         # -1..1


class CooccurEdge(BaseModel):
    from_node: str = Field(alias="from")
    to: str
    weight: int
    lift: float

    model_config = {"populate_by_name": True}


class CooccurPair(BaseModel):
    k1: str
    k2: str
    weight: int
    lift: float
    sentiment_skew: float


class KeywordCooccurrenceResponse(BaseModel):
    nodes: List[CooccurNode]
    edges: List[CooccurEdge]
    top_pairs: List[CooccurPair]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 8) anomaly-context ────────────────────────────────────────────
class KeywordDelta(BaseModel):
    keyword: str
    before: int
    after: int
    delta: int


class MatchedEvent(BaseModel):
    title: str
    event_date: str
    lag_days: int


class SpikeEntry(BaseModel):
    date: str
    category: str
    count: int
    z: float
    top_keywords_delta: List[KeywordDelta]
    matched_events: List[MatchedEvent]
    inferred_cause: Optional[str] = None


class AnomalyContextResponse(BaseModel):
    spikes: List[SpikeEntry]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 9) category-momentum (D1) ─────────────────────────────────────
class MomentumWeekPoint(BaseModel):
    week: str                     # YYYY-MM-DD (week start)
    share_pct: float              # 0..100
    n: int


class CategoryMomentumItem(BaseModel):
    code: str
    name_ko: Optional[str] = None
    series: List[MomentumWeekPoint]
    momentum_slope: float         # 최근 4주 OLS slope (share_pct/week)


class CategoryMomentumResponse(BaseModel):
    categories: List[CategoryMomentumItem]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 10) keyword-network (D2) ──────────────────────────────────────
class NetworkNode(BaseModel):
    id: str
    keyword: str
    lang: Optional[str] = None
    freq: int
    community_id: int


class NetworkEdge(BaseModel):
    source: str
    target: str
    weight: int


class KeywordNetworkResponse(BaseModel):
    nodes: List[NetworkNode]
    edges: List[NetworkEdge]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 11) lifecycle-funnel (D3) ─────────────────────────────────────
class LifecycleFunnelExample(BaseModel):
    keyword: str
    days_alive: int
    peak_count: int


class LifecycleFunnelStage(BaseModel):
    stage: str                    # 신규|성장|정체|감소
    n_keywords: int
    examples: List[LifecycleFunnelExample]


class LifecycleFunnelResponse(BaseModel):
    stages: List[LifecycleFunnelStage]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 12) influence-rank (D4) ───────────────────────────────────────
class InfluenceDrivers(BaseModel):
    engagement: float             # 정규화 0..1
    neg_rate: float               # 0..1
    lead_days: float              # 양수면 lead, 음수면 lag
    reach: float                  # 정규화 0..1


class InfluenceRankItem(BaseModel):
    platform: str                 # 표시명
    code: str
    region: Optional[str] = None
    score: float                  # 0..1
    drivers: InfluenceDrivers


class InfluenceRankResponse(BaseModel):
    items: List[InfluenceRankItem]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 13) product-funnel (D5) ───────────────────────────────────────
class ProductFunnelStage(BaseModel):
    stage: str                    # 출시|인지|관심|구매고려|실사용|이탈
    period: str                   # 'YYYY-MM-DD~YYYY-MM-DD'
    count: int
    sent_avg: float               # -1..1
    top_keywords: List[str]


class ProductFunnelResponse(BaseModel):
    product: str
    stages: List[ProductFunnelStage]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 14) sentiment-driver (P3.7 트랙 B 결합 카드) ──────────────────
class SentimentDriverItem(BaseModel):
    keyword: str
    lang: Optional[str] = None
    before_neg_rate: float            # 0..1
    after_neg_rate: float             # 0..1
    delta_pp: float                   # percentage point (after - before) * 100
    n_before: int
    n_after: int
    related_categories: List[str] = Field(default_factory=list)


class SentimentDriverResponse(BaseModel):
    items: List[SentimentDriverItem]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 15) anomaly-with-drivers (P3.7 트랙 B 결합 카드) ─────────────
class TopDriver(BaseModel):
    keyword: str
    delta_pct: float                  # (after - before)/max(before,1)*100
    sentiment: float                  # -1..1 (after window sentiment bias)


class AnomalyWithDriversEntry(BaseModel):
    date: str
    metric: str                       # 'category_daily_count'
    category: str
    z: float
    baseline: float                   # mu
    value: float                      # daily count
    top_drivers: List[TopDriver]


class AnomalyWithDriversResponse(BaseModel):
    anomalies: List[AnomalyWithDriversEntry]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 16) anomaly-drilldown (트랙 B 확장: 시·제품·키워드 3차원 cross drill-down) ──
class DrilldownHourBucket(BaseModel):
    hour: int                         # 0..23
    count: int
    sent_avg: float                   # -1..1
    neg_rate: float                   # 0..1


class DrilldownProduct(BaseModel):
    code: str
    name_ko: Optional[str] = None
    count: int
    neg_rate: float                   # 0..1


class DrilldownKeyword(BaseModel):
    keyword: str
    lang: Optional[str] = None
    count: int
    delta_pct: float                  # vs 14일 baseline ((today - baseline_avg)/max(baseline_avg,1)*100)
    related_products: List[str] = Field(default_factory=list)  # 동일 day 내 동시 등장 top 3 product code


class DrilldownPlatform(BaseModel):
    code: str
    name: Optional[str] = None
    count: int


class AnomalySummary(BaseModel):
    z: float
    value: float
    baseline: float


class AnomalyDrilldownResponse(BaseModel):
    date: str
    anomaly_summary: AnomalySummary
    hourly: List[DrilldownHourBucket]
    products: List[DrilldownProduct]
    keywords: List[DrilldownKeyword]
    platforms: List[DrilldownPlatform]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 17) anomaly-drilldown-hour (E3 — 1h VoC 리스트) ───────────────
class DrilldownHourProductRef(BaseModel):
    code: str
    name_ko: Optional[str] = None


class DrilldownHourPlatformRef(BaseModel):
    code: str
    name: Optional[str] = None


class DrilldownHourVocItem(BaseModel):
    id: int
    product: Optional[DrilldownHourProductRef] = None
    platform: Optional[DrilldownHourPlatformRef] = None
    content_preview: str                       # content_original 앞 200자
    sentiment_label: Optional[str] = None       # 'positive' | 'negative' | 'neutral'
    sentiment_score: Optional[float] = None     # -1..1
    engagement_score: Optional[float] = None
    url: Optional[str] = None
    published_at: Optional[str] = None          # ISO8601 UTC


class AnomalyDrilldownHourResponse(BaseModel):
    date: str
    hour: int                                   # 0..23
    total: int
    items: List[DrilldownHourVocItem]
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── 18) keyword-detail (UX R2 트랙 A — KeywordNetwork node 클릭 → Drawer) ─
class KeywordDetailProductStat(BaseModel):
    code: str
    name_ko: Optional[str] = None
    count: int


class KeywordDetailPlatformStat(BaseModel):
    code: str
    name: Optional[str] = None
    count: int


class KeywordDetailStats(BaseModel):
    total_count: int
    sentiment_avg: float                  # -1..1
    top_products: List[KeywordDetailProductStat] = Field(default_factory=list)
    top_platforms: List[KeywordDetailPlatformStat] = Field(default_factory=list)


class KeywordDetailSample(BaseModel):
    id: int
    content_preview: str                  # content_original 앞 200자
    sentiment_label: Optional[str] = None
    product: Optional[str] = None         # product code
    platform: Optional[str] = None        # platform code
    url: Optional[str] = None
    published_at: Optional[str] = None    # ISO8601


class KeywordDetailRelated(BaseModel):
    keyword: str
    lang: Optional[str] = None
    cooccur_count: int


class KeywordDetailCategory(BaseModel):
    category: str
    count: int


class KeywordDetailResponse(BaseModel):
    keyword: str
    lang: Optional[str] = None
    period_days: int
    stats: KeywordDetailStats
    samples: List[KeywordDetailSample]
    related_keywords: List[KeywordDetailRelated]
    categories: List[KeywordDetailCategory] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── R9 트랙 A: galaxy-history ─────────────────────────────────────
# 17년 lifecycle/위기 사례 분석.
class GalaxyTimelineModel(BaseModel):
    code: str                       # GS22 등
    name: str                       # Galaxy S22
    series: str                     # GS / GN / GZ / GZF / GZFL / GW / GB
    released_at: Optional[str] = None  # YYYY-MM-DD
    voc_7d_count: int               # 출시 +/- 7일 voc count
    sent_avg: float                 # -1..1, 7d window
    neg_rate: float                 # 0..1, 7d window
    peak_count: int                 # released_at +180일 내 최고 단일일 count
    total_count: int                # released_at + period_days 내 총 voc


class GalaxyTimelineResponse(BaseModel):
    series: str
    models: List[GalaxyTimelineModel]
    meta: Dict[str, Any] = Field(default_factory=dict)


class CrisisCaseTimelinePoint(BaseModel):
    day: str                        # YYYY-MM-DD
    count: int


class CrisisCaseKeyword(BaseModel):
    keyword: str
    count: int


class CrisisCaseSite(BaseModel):
    site: str
    count: int


class CrisisCase(BaseModel):
    code: str                       # GN7 / GZF1 / GS22U
    title: str                      # 위기 명
    description: str
    period_start: str               # YYYY-MM-DD
    period_end: str                 # YYYY-MM-DD
    total_voc: int
    neg_rate: float                 # 0..1
    timeline: List[CrisisCaseTimelinePoint]
    top_keywords: List[CrisisCaseKeyword]
    top_sites: List[CrisisCaseSite]


class CrisisCasesResponse(BaseModel):
    cases: List[CrisisCase]
    meta: Dict[str, Any] = Field(default_factory=dict)


class SeriesComparisonGenPoint(BaseModel):
    gen: int                        # 1..N (출시 순서)
    code: str
    name: str
    released_at: Optional[str] = None
    count: int
    sent_avg: float
    neg_rate: float


class SeriesComparisonSeries(BaseModel):
    series: str                     # GS / GN / GZ ...
    label: str                      # "Galaxy S"
    points: List[SeriesComparisonGenPoint]


class SeriesComparisonResponse(BaseModel):
    series_list: List[SeriesComparisonSeries]
    meta: Dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "IssueLifecycleResponse",
    "CategoryProductMatrixResponse",
    "SiteDiffusionResponse",
    "CountrySentimentGapResponse",
    "EngagementSentimentResponse",
    "NewTermSurvivalResponse",
    "KeywordCooccurrenceResponse",
    "AnomalyContextResponse",
    "CategoryMomentumResponse",
    "KeywordNetworkResponse",
    "LifecycleFunnelResponse",
    "InfluenceRankResponse",
    "ProductFunnelResponse",
    "SentimentDriverResponse",
    "AnomalyWithDriversResponse",
    "AnomalyDrilldownResponse",
    "AnomalySummary",
    "DrilldownHourBucket",
    "DrilldownProduct",
    "DrilldownKeyword",
    "DrilldownPlatform",
    "AnomalyDrilldownHourResponse",
    "DrilldownHourVocItem",
    "DrilldownHourProductRef",
    "DrilldownHourPlatformRef",
    "KeywordDetailResponse",
    "KeywordDetailStats",
    "KeywordDetailSample",
    "KeywordDetailRelated",
    "KeywordDetailCategory",
    "KeywordDetailProductStat",
    "KeywordDetailPlatformStat",
    # R9 galaxy-history
    "GalaxyTimelineResponse",
    "GalaxyTimelineModel",
    "CrisisCasesResponse",
    "CrisisCase",
    "CrisisCaseTimelinePoint",
    "CrisisCaseKeyword",
    "CrisisCaseSite",
    "SeriesComparisonResponse",
    "SeriesComparisonSeries",
    "SeriesComparisonGenPoint",
]
