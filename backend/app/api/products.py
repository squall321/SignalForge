from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List

from app.database import get_db
from app.schemas.voc import ProductRead, VocListResponse, ProductStats
from app.services.voc_service import VocService

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=List[ProductRead])
async def list_products(
    series: Optional[str] = Query(None, description="시리즈 코드 필터 (GS, GZ, GA, GW, GB, GR)"),
    brand: Optional[str] = Query(None, description="브랜드 필터 (samsung, apple, google ...)"),
    category: Optional[str] = Query(None, description="제품군 필터 (phone, watch, buds, tablet, band, headphone, ring)"),
    is_active: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    """제품 목록 조회. 경쟁사 380종이 함께 들어 있으므로 brand 로 걸러 쓴다."""
    return await VocService(db).get_products(
        series=series, brand=brand, category=category, is_active=is_active)


@router.get("/{code}/voc", response_model=VocListResponse)
async def get_product_voc(
    code: str,
    country: Optional[str] = Query(None, description="국가 코드 (콤마 구분, 예: KR,US)"),
    platform: Optional[str] = Query(None, description="플랫폼 코드 (콤마 구분)"),
    sentiment: Optional[str] = Query(None, description="positive | negative | neutral"),
    category: Optional[str] = Query(None, description="카테고리 코드"),
    from_date: Optional[str] = Query(None, alias="from", description="시작일 YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, alias="to", description="종료일 YYYY-MM-DD"),
    lang: Optional[str] = Query(None, description="원문 언어 (ko, en, zh...)"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """제품별 VOC 목록 조회 (필터 + 페이지네이션)"""
    countries = country.split(",") if country else None
    platforms = platform.split(",") if platform else None

    return await VocService(db).get_product_voc(
        product_code=code,
        countries=countries,
        platforms=platforms,
        sentiment=sentiment,
        category=category,
        from_date=from_date,
        to_date=to_date,
        lang=lang,
        limit=limit,
        offset=offset,
    )


@router.get("/{code}/stats", response_model=ProductStats)
async def get_product_stats(
    code: str,
    db: AsyncSession = Depends(get_db),
):
    """제품별 VOC 통계 요약"""
    return await VocService(db).get_product_stats(product_code=code)
