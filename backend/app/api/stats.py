# 코퍼스 전체 통계 REST — 포털 LLM 이 '전제'로 쓸 집계를 제공한다
"""통계 엔드포인트.

포털 LLM 검색이 얇게 나오던 이유는 API 가 **행만 주고 집계를 안 줬기** 때문이다.
여기서는 총량·추세·분포·증감을 준다. MCP 동명 도구와 **같은 SQL**(shared/stats_sql.py)
을 쓴다.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.stats_service import StatsService

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/overview")
async def corpus_overview(
    days: Optional[int] = Query(None, description="최근 N일로 한정 — 생략 시 전 기간"),
    db: AsyncSession = Depends(get_db),
):
    """코퍼스 전체 현황 — 규모·기간·커버리지·품질·결함률."""
    return await StatsService(db).corpus_overview(days)


@router.get("/keyword")
async def keyword_stats(
    keyword: str = Query(..., description="검색 키워드 (한국어 가능)"),
    days: int = Query(90, ge=1, le=3650),
    interval: str = Query("week", description="day | week | month"),
    top_n: int = Query(8, ge=1, le=50),
    product_code: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    country: Optional[str] = None,
    platform: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """키워드의 통계 프로파일 — 총량·전기대비·시계열·제품/플랫폼/국가 분포."""
    return await StatsService(db).keyword_stats(
        keyword, days=days, interval=interval, top_n=top_n,
        product_code=product_code, brand=brand, category=category,
        country=country, platform=platform)


@router.get("/period-compare")
async def period_compare(
    days: int = Query(30, ge=1, le=365),
    by: str = Query("product", description="product | brand | category | country | platform"),
    limit: int = Query(15, ge=1, le=100),
    keyword: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    country: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """최근 N일 vs 직전 N일 증감 — risers/fallers 포함."""
    return await StatsService(db).period_compare(
        days=days, by=by, limit=limit, keyword=keyword,
        brand=brand, category=category, country=country)


@router.get("/breakdown")
async def voc_breakdown(
    by: str = Query("product", description="product | brand | category | country | "
                                           "platform | platform_kind | language | sentiment"),
    days: Optional[int] = Query(30),
    limit: int = Query(20, ge=1, le=200),
    keyword: Optional[str] = None,
    product_code: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    country: Optional[str] = None,
    platform: Optional[str] = None,
    sentiment: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """전체 VOC 를 임의 축으로 분해 — share_pct·negative_pct 포함."""
    return await StatsService(db).voc_breakdown(
        by=by, days=days, limit=limit, keyword=keyword,
        product_code=product_code, brand=brand, category=category,
        country=country, platform=platform, sentiment=sentiment)
