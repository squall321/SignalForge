"""
T4 딥 인사이트 API (P4 트랙 C) — 7 endpoint.

prefix: /api/v1/insights
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.insights import (
    CompareLLMRequest,
    CompareLLMResponse,
    EmergingKeywordsResponse,
    HourlyPatternResponse,
    NewTermsResponse,
    PlatformInfluenceResponse,
    ProductLifecycleResponse,
    SentimentSwingResponse,
    WeekdayPatternResponse,
)
from app.services.insights_service import InsightsService


router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("/hourly-pattern", response_model=HourlyPatternResponse)
async def hourly_pattern(
    product: Optional[str] = Query(None, description="제품 코드 (예: GS25)"),
    period_days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """시간대(0~23)별 VOC 발생량 + 평균 감성."""
    return await InsightsService(db).hourly_pattern(product=product, period_days=period_days)


@router.get("/weekday-pattern", response_model=WeekdayPatternResponse)
async def weekday_pattern(
    product: Optional[str] = Query(None, description="제품 코드 (예: GS25)"),
    period_days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """요일(0=월~6=일)별 VOC 발생량 + 감성 + 부정율."""
    return await InsightsService(db).weekday_pattern(product=product, period_days=period_days)


@router.get("/emerging-keywords", response_model=EmergingKeywordsResponse)
async def emerging_keywords(
    period_days: int = Query(7, ge=1, le=60),
    top_n: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """전주 대비 keyword 증가율 TOP / 감소율 TOP."""
    return await InsightsService(db).emerging_keywords(
        period_days=period_days, top_n=top_n
    )


@router.get("/new-terms", response_model=NewTermsResponse)
async def new_terms(
    period_days: int = Query(30, ge=1, le=180),
    db: AsyncSession = Depends(get_db),
):
    """최근 period_days 에 첫 등장한 키워드 (이전 90일 0건)."""
    return await InsightsService(db).new_terms(period_days=period_days)


@router.get("/sentiment-swing", response_model=SentimentSwingResponse)
async def sentiment_swing(
    period_days: int = Query(14, ge=3, le=90),
    min_volume: int = Query(50, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
):
    """직전 N일 vs 그 전 N일 sentiment delta 가 큰 제품."""
    return await InsightsService(db).sentiment_swing(
        period_days=period_days, min_volume=min_volume
    )


@router.get("/product-lifecycle", response_model=ProductLifecycleResponse)
async def product_lifecycle(
    product: str = Query(..., description="제품 코드 (예: GS25)"),
    db: AsyncSession = Depends(get_db),
):
    """출시일(D+0) 기준 7/30/90/180 윈도우의 count/sent/top-categories."""
    try:
        return await InsightsService(db).product_lifecycle(product=product)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/platform-influence", response_model=PlatformInfluenceResponse)
async def platform_influence(
    period_days: int = Query(30, ge=7, le=180),
    db: AsyncSession = Depends(get_db),
):
    """사이트별 영향력 점수 = engagement × 부정율 × leading_factor."""
    return await InsightsService(db).platform_influence(period_days=period_days)


@router.post("/compare-llm", response_model=CompareLLMResponse)
async def compare_llm(
    req: CompareLLMRequest,
    db: AsyncSession = Depends(get_db),
):
    """제품 2~4개 의 LLM 기반 비교 분석 narrative.

    - 입력: products(2~4) + period_days(7~180, 기본 30)
    - 응답: narrative (None 가능) + tier_label + grounding_score + generated_at
    - 캐시: 동일 입력 15분 (redis_cache)
    - tier: auto → external → high(14b 기대) → fast
    - LLM 키/서버 미가용 시 narrative=None, tier_label='none', score=0.0
    """
    return await InsightsService(db).compare_llm(
        products=req.products, period_days=req.period_days
    )
