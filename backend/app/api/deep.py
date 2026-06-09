"""
P3.6 트랙 A — 심층 분석 8 endpoint API.

prefix: /api/v1/deep
"""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.deep import (
    AnomalyContextResponse,
    AnomalyDrilldownHourResponse,
    AnomalyDrilldownResponse,
    AnomalyWithDriversResponse,
    CategoryMomentumResponse,
    CategoryProductMatrixResponse,
    CountrySentimentGapResponse,
    CrisisCasesResponse,
    EngagementSentimentResponse,
    GalaxyTimelineResponse,
    InfluenceRankResponse,
    IssueLifecycleResponse,
    KeywordCooccurrenceResponse,
    KeywordDetailResponse,
    KeywordNetworkResponse,
    LifecycleFunnelResponse,
    NewTermSurvivalResponse,
    ProductFunnelResponse,
    SentimentDriverResponse,
    SeriesComparisonResponse,
    SiteDiffusionResponse,
)
from app.services.deep_service import DeepService

router = APIRouter(prefix="/deep", tags=["deep"])


@router.get("/issue-lifecycle", response_model=IssueLifecycleResponse)
async def issue_lifecycle(
    category: Optional[str] = Query(None, description="카테고리 코드 필터 (옵션)"),
    period_days: int = Query(60, ge=7, le=365),
    top_n: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """부정 이슈 라이프사이클 — first_seen → peak → last_seen 의 lifespan/days_to_peak."""
    return await DeepService(db).issue_lifecycle(
        category=category, period_days=period_days, top_n=top_n
    )


@router.get("/category-product-matrix", response_model=CategoryProductMatrixResponse)
async def category_product_matrix(
    period_days: int = Query(30, ge=7, le=180),
    top_products: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """제품 × 카테고리 sentiment 매트릭스 (z-score outlier flag)."""
    return await DeepService(db).category_product_matrix(
        period_days=period_days, top_products=top_products
    )


@router.get("/site-diffusion", response_model=SiteDiffusionResponse)
async def site_diffusion(
    period_days: int = Query(45, ge=7, le=180),
    min_sites: int = Query(2, ge=2, le=20),
    top_keywords: int = Query(30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """키워드의 사이트 간 확산 경로(origin→terminal) + edge 통계."""
    return await DeepService(db).site_diffusion(
        period_days=period_days,
        min_sites=min_sites,
        top_keywords=top_keywords,
    )


@router.get("/country-sentiment-gap", response_model=CountrySentimentGapResponse)
async def country_sentiment_gap(
    period_days: int = Query(30, ge=7, le=180),
    top_products: int = Query(10, ge=1, le=50),
    min_n: int = Query(20, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
):
    """제품별 국가별 sentiment score 와 글로벌 평균 대비 gap."""
    return await DeepService(db).country_sentiment_gap(
        period_days=period_days,
        top_products=top_products,
        min_n=min_n,
    )


@router.get("/engagement-sentiment", response_model=EngagementSentimentResponse)
async def engagement_sentiment(
    period_days: int = Query(30, ge=7, le=180),
    db: AsyncSession = Depends(get_db),
):
    """engagement 5분위(quintile) 버킷별 sentiment / neg_ratio."""
    return await DeepService(db).engagement_sentiment(period_days=period_days)


@router.get("/new-term-survival", response_model=NewTermSurvivalResponse)
async def new_term_survival(
    period_days: int = Query(60, ge=7, le=365),
    lookback_window: int = Query(14, ge=3, le=60),
    min_mentions: int = Query(5, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    """신규 키워드 첫 등장 후 lookback 윈도우 내 생존 패턴 (sustained/mid/flash)."""
    return await DeepService(db).new_term_survival(
        period_days=period_days,
        lookback_window=lookback_window,
        min_mentions=min_mentions,
    )


@router.get("/keyword-cooccurrence", response_model=KeywordCooccurrenceResponse)
async def keyword_cooccurrence(
    period_days: int = Query(30, ge=7, le=180),
    min_edge_weight: int = Query(5, ge=1, le=10000),
    top_nodes: int = Query(80, ge=10, le=500),
    db: AsyncSession = Depends(get_db),
):
    """키워드 공출현 네트워크 — edge weight / lift / top_pairs."""
    return await DeepService(db).keyword_cooccurrence(
        period_days=period_days,
        min_edge_weight=min_edge_weight,
        top_nodes=top_nodes,
    )


@router.get("/anomaly-context", response_model=AnomalyContextResponse)
async def anomaly_context(
    period_days: int = Query(14, ge=3, le=90),
    z_threshold: float = Query(2.5, ge=1.0, le=10.0),
    db: AsyncSession = Depends(get_db),
):
    """카테고리 spike 탐지 + 키워드 delta + timeline event 매칭으로 원인 추정."""
    return await DeepService(db).anomaly_context(
        period_days=period_days, z_threshold=z_threshold
    )


# ── P3.7 트랙 B 결합 카드 ─────────────────────────────────────────
@router.get("/sentiment-driver", response_model=SentimentDriverResponse)
async def sentiment_driver(
    period_days: int = Query(30, ge=14, le=180),
    top_n: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """직전 14일 vs 그 전 14일 negative rate delta 가 큰 키워드 top_n."""
    return await DeepService(db).sentiment_driver(
        period_days=period_days, top_n=top_n
    )


@router.get("/anomaly-with-drivers", response_model=AnomalyWithDriversResponse)
async def anomaly_with_drivers(
    period_days: int = Query(14, ge=3, le=90),
    z_threshold: float = Query(2.0, ge=1.0, le=10.0),
    db: AsyncSession = Depends(get_db),
):
    """anomaly day 별 직전 24h 키워드 변화 (top 5 driver) 결합 응답."""
    return await DeepService(db).anomaly_with_drivers(
        period_days=period_days, z_threshold=z_threshold
    )


@router.get("/anomaly-drilldown", response_model=AnomalyDrilldownResponse)
async def anomaly_drilldown(
    date_: str = Query(..., alias="date", description="YYYY-MM-DD"),
    z_threshold: float = Query(2.0, ge=1.0, le=10.0),
    top_k: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """anomaly day 의 시간대 × 제품 × 키워드 × 사이트 cross drill-down."""
    try:
        target = datetime.strptime(date_, "%Y-%m-%d").date()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"invalid date format: {e}")
    return await DeepService(db).anomaly_drilldown(
        target_date=target, z_threshold=z_threshold, top_k=top_k
    )


@router.get("/anomaly-drilldown-hour", response_model=AnomalyDrilldownHourResponse)
async def anomaly_drilldown_hour(
    date_: str = Query(..., alias="date", description="YYYY-MM-DD"),
    hour: int = Query(..., ge=0, le=23, description="UTC hour 0..23"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    db: AsyncSession = Depends(get_db),
):
    """E3 — drilldown 1h VoC 리스트 (negative 우선, engagement 내림차순)."""
    try:
        target = datetime.strptime(date_, "%Y-%m-%d").date()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"invalid date format: {e}")
    return await DeepService(db).anomaly_drilldown_hour(
        target_date=target, hour=hour, limit=limit, offset=offset
    )


# ── 트랙 D: 추가 deep cut 5 endpoint ─────────────────────────────
@router.get("/category-momentum", response_model=CategoryMomentumResponse)
async def category_momentum(
    period_days: int = Query(60, ge=14, le=365),
    bucket: str = Query("week", pattern="^(week|day)$"),
    db: AsyncSession = Depends(get_db),
):
    """카테고리 12개 × 주별 share(%) + 최근 4주 momentum slope."""
    return await DeepService(db).category_momentum(
        period_days=period_days, bucket=bucket
    )


@router.get("/keyword-network", response_model=KeywordNetworkResponse)
async def keyword_network(
    period_days: int = Query(30, ge=7, le=180),
    min_cooccur: int = Query(10, ge=2, le=10000),
    max_nodes: int = Query(80, ge=10, le=500),
    db: AsyncSession = Depends(get_db),
):
    """키워드 동시 출현 네트워크 (force-directed 데이터 + community_id)."""
    return await DeepService(db).keyword_network(
        period_days=period_days, min_cooccur=min_cooccur, max_nodes=max_nodes
    )


@router.get("/lifecycle-funnel", response_model=LifecycleFunnelResponse)
async def lifecycle_funnel(
    period_days: int = Query(90, ge=14, le=365),
    db: AsyncSession = Depends(get_db),
):
    """신규 키워드 단계별 잔존(신규→성장→정체→감소) 깔때기."""
    return await DeepService(db).lifecycle_funnel(period_days=period_days)


@router.get("/influence-rank", response_model=InfluenceRankResponse)
async def influence_rank(
    period_days: int = Query(30, ge=7, le=180),
    top_n: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """사이트 영향력 종합 점수 (engagement × neg_rate × lead × reach)."""
    return await DeepService(db).influence_rank(
        period_days=period_days, top_n=top_n
    )


@router.get("/product-funnel", response_model=ProductFunnelResponse)
async def product_funnel(
    product: str = Query(..., min_length=2, max_length=10),
    period_days: int = Query(180, ge=30, le=720),
    db: AsyncSession = Depends(get_db),
):
    """제품의 출시-인지-관심-구매고려-실사용-이탈 단계 추정."""
    return await DeepService(db).product_funnel(
        product=product, period_days=period_days
    )


# ── UX R2 트랙 A: KeywordNetwork node 클릭 → 키워드 상세 Drawer ───
@router.get("/keyword-detail", response_model=KeywordDetailResponse)
async def keyword_detail(
    keyword: str = Query(..., min_length=1, max_length=120),
    lang: Optional[str] = Query(None, max_length=8, description="ko|en|ja|... 옵션"),
    period_days: int = Query(7, ge=1, le=90),
    limit: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """키워드 최근 N일 통계 + 샘플 VoC + 연결 키워드 + 카테고리 분포."""
    return await DeepService(db).keyword_detail(
        keyword=keyword, lang=lang, period_days=period_days, limit=limit
    )


# ── R9 트랙 A: galaxy-history (17년 lifecycle / 위기 사례) ────────
_SERIES_ALIAS = {
    "S": "GS",
    "NOTE": "GN",
    "N": "GN",
    "Z": "GZ",
    "FOLD": "GZF",
    "FLIP": "GZFL",
    "WATCH": "GW",
    "W": "GW",
    "BUDS": "GB",
    "B": "GB",
    "A": "GA",
    "M": "GM",
    "J": "GJ",
}


def _resolve_series(s: str) -> str:
    u = s.upper().strip()
    return _SERIES_ALIAS.get(u, u)


@router.get("/galaxy-timeline", response_model=GalaxyTimelineResponse)
async def galaxy_timeline(
    series: str = Query(..., min_length=1, max_length=8, description="S|Note|Z|Fold|Flip|Watch|Buds"),
    product: Optional[str] = Query(None, max_length=10, description="단일 모델 필터 (옵션)"),
    db: AsyncSession = Depends(get_db),
):
    """시리즈별 17년 라이프사이클: 출시 +/- 7일 voc + 출시 후 180일 peak."""
    return await DeepService(db).galaxy_timeline(
        series=_resolve_series(series), product=product
    )


@router.get("/crisis-cases", response_model=CrisisCasesResponse)
async def crisis_cases(
    db: AsyncSession = Depends(get_db),
):
    """사전정의 위기 사례 (Note 7 발화, Fold 1 결함, S22 GoS) 의 voc 통계."""
    return await DeepService(db).crisis_cases()


@router.get("/series-comparison", response_model=SeriesComparisonResponse)
async def series_comparison(
    series: str = Query("S,Note,Z", min_length=1, max_length=40,
                        description="콤마구분: S,Note,Z 또는 GS,GN,GZ"),
    db: AsyncSession = Depends(get_db),
):
    """여러 시리즈의 세대별 sentiment / count 추이 비교."""
    series_list = [_resolve_series(s) for s in series.split(",") if s.strip()]
    if not series_list:
        raise HTTPException(status_code=422, detail="series empty")
    if len(series_list) > 6:
        raise HTTPException(status_code=422, detail="max 6 series")
    return await DeepService(db).series_comparison(series=series_list)
