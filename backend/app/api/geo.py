"""
T4 국가 지도 (Geo) Backend — 4 endpoint (P3-2).

- GET /api/v1/analytics/country/choropleth
- GET /api/v1/analytics/country/{code}/drilldown
- GET /api/v1/analytics/country/diffusion
- GET /api/v1/analytics/country/product-compare

데이터 소스: country_daily MV (0004_p3_objects.py) + voc_records.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.geo import (
    ChoroplethResponse,
    DiffusionResponse,
    DrilldownResponse,
    ProductCompareResponse,
)
from app.services.geo_service import GeoService


router = APIRouter(prefix="/analytics/country", tags=["geo"])


@router.get("/choropleth", response_model=ChoroplethResponse)
async def country_choropleth(
    product_id: Optional[int] = Query(None, description="제품 id (NULL → 전 제품 합산)"),
    date_from: Optional[str] = Query(None, description="시작일 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="종료일 YYYY-MM-DD"),
    metric: str = Query("n", pattern="^(n|sent_avg|sent_z)$"),
    db: AsyncSession = Depends(get_db),
):
    """전 세계 choropleth 데이터 (≤ 500ms 목표)."""
    try:
        return await GeoService(db).choropleth(
            product_id=product_id,
            date_from=date_from,
            date_to=date_to,
            metric=metric,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{code}/drilldown", response_model=DrilldownResponse)
async def country_drilldown(
    code: str,
    date_from: Optional[str] = Query(None, description="시작일 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="종료일 YYYY-MM-DD"),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """국가 1개 → top_sites / top_products / top_categories."""
    if len(code) != 2:
        raise HTTPException(status_code=400, detail="code must be ISO2 (2 chars)")
    try:
        return await GeoService(db).drilldown(
            code=code,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/diffusion", response_model=DiffusionResponse)
async def country_diffusion(
    product_id: Optional[int] = Query(None, description="제품 id (NULL → 전 제품 합산)"),
    date_from: Optional[str] = Query(None, description="시작일 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="종료일 YYYY-MM-DD"),
    granularity: str = Query("day", pattern="^(day|week)$"),
    db: AsyncSession = Depends(get_db),
):
    """시간 슬라이더용 일별/주별 frames."""
    try:
        return await GeoService(db).diffusion(
            product_id=product_id,
            date_from=date_from,
            date_to=date_to,
            granularity=granularity,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/product-compare", response_model=ProductCompareResponse)
async def country_product_compare(
    product_id: int = Query(..., description="제품 id (필수)"),
    countries: str = Query(..., description="ISO2 콤마 구분 (예: KR,US,JP)"),
    date_from: Optional[str] = Query(None, description="시작일 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="종료일 YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
):
    """제품 1개 × 다국가 sent_avg + 95% CI."""
    iso2_list = [c.strip() for c in countries.split(",") if c.strip()]
    if not iso2_list:
        raise HTTPException(status_code=400, detail="countries empty")
    try:
        return await GeoService(db).product_compare(
            product_id=product_id,
            countries=iso2_list,
            date_from=date_from,
            date_to=date_to,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
