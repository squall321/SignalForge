from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List

from app.database import get_db
from app.schemas.analytics import (
    SentimentTrendResponse,
    CategoryDistResponse,
    CountryHeatmapResponse,
    TopIssuesResponse,
    CompareResponse,
    KeywordTrackResponse,
    CohortCompareResponse,
    SiteHealthResponse,
    RecentIssuesResponse,
)
from app.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/sentiment-trend", response_model=SentimentTrendResponse)
async def sentiment_trend(
    product: str = Query(..., description="제품 코드"),
    period_days: int = Query(90, description="조회 기간 (일)"),
    granularity: str = Query("week", description="day | week | month"),
    db: AsyncSession = Depends(get_db),
):
    """감성 트렌드 시계열 조회"""
    return await AnalyticsService(db).get_sentiment_trend(
        product_code=product,
        period_days=period_days,
        granularity=granularity,
    )


@router.get("/category-dist", response_model=CategoryDistResponse)
async def category_distribution(
    product: str = Query(..., description="제품 코드"),
    period_days: int = Query(30),
    db: AsyncSession = Depends(get_db),
):
    """카테고리 분포 조회"""
    return await AnalyticsService(db).get_category_distribution(
        product_code=product,
        period_days=period_days,
    )


@router.get("/country-heatmap", response_model=CountryHeatmapResponse)
async def country_heatmap(
    product: str = Query(..., description="제품 코드"),
    period_days: int = Query(30),
    db: AsyncSession = Depends(get_db),
):
    """국가별 VOC 히트맵 데이터"""
    return await AnalyticsService(db).get_country_heatmap(
        product_code=product,
        period_days=period_days,
    )


@router.get("/top-issues", response_model=TopIssuesResponse)
async def top_issues(
    product: str = Query(..., description="제품 코드"),
    period_days: int = Query(30),
    top_n: int = Query(10, le=20),
    db: AsyncSession = Depends(get_db),
):
    """상위 이슈 랭킹"""
    return await AnalyticsService(db).get_top_issues(
        product_code=product,
        period_days=period_days,
        top_n=top_n,
    )


@router.get("/compare", response_model=CompareResponse)
async def compare_products(
    products: str = Query(..., description="제품 코드 (콤마 구분, 예: GS25U,GZF7)"),
    period_days: int = Query(30),
    db: AsyncSession = Depends(get_db),
):
    """제품 간 VOC 비교 (레이더 차트용)"""
    product_codes = [p.strip() for p in products.split(",")]
    return await AnalyticsService(db).compare_products(
        product_codes=product_codes,
        period_days=period_days,
    )


# ── 신규 endpoint ──────────────────────────────────────────

@router.get("/keyword-track", response_model=KeywordTrackResponse)
async def keyword_track(
    keyword: str = Query(..., min_length=2, description="검색 키워드"),
    period_days: int = Query(30, ge=1, le=365),
    granularity: str = Query("day", description="day | week | month"),
    db: AsyncSession = Depends(get_db),
):
    """키워드 본문 검색 시계열 추이 (content_translated / content_original ILIKE)"""
    return await AnalyticsService(db).get_keyword_track(
        keyword=keyword,
        period_days=period_days,
        granularity=granularity,
    )


@router.get("/cohort-compare", response_model=CohortCompareResponse)
async def cohort_compare(
    products: str = Query(..., description="제품 코드 (콤마 구분, 예: GS25,GS26)"),
    dimension: str = Query("sentiment", description="sentiment | category"),
    period_days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """제품 그룹 코호트 비교 (감성 / 카테고리 분포)"""
    product_codes = [p.strip() for p in products.split(",") if p.strip()]
    return await AnalyticsService(db).cohort_compare(
        product_codes=product_codes,
        dimension=dimension,
        period_days=period_days,
    )


@router.get("/site-health", response_model=SiteHealthResponse)
async def site_health(
    db: AsyncSession = Depends(get_db),
):
    """사이트별 24h/7d 활성도, 평균 본문 길이, 태깅률"""
    return await AnalyticsService(db).get_site_health()


@router.get("/recent-issues", response_model=RecentIssuesResponse)
async def recent_issues(
    product: str = Query(..., description="제품 코드"),
    top_n: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """최근 N건 부정 본문 인용 (LLM 입력 대비)"""
    return await AnalyticsService(db).get_recent_issues(
        product_code=product,
        top_n=top_n,
    )
