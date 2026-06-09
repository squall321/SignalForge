"""
Dashboard 통합 overview 라우터 (P1-2 MVP).

GET /api/v1/dashboard/overview
  ?period=7d|30d|90d (default 30d)
  &product=GS26U
  &country=KR
  &platform=reddit
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.dashboard import DashboardOverviewResponse
from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/overview", response_model=DashboardOverviewResponse)
async def dashboard_overview(
    period: str = Query("30d", pattern="^(24h|7d|30d|90d)$", description="조회 기간"),
    product: Optional[str] = Query(None, description="제품 코드 (예: GS26U)"),
    country: Optional[str] = Query(None, description="국가 코드 (예: KR)"),
    platform: Optional[str] = Query(None, description="플랫폼 코드 (예: reddit)"),
    db: AsyncSession = Depends(get_db),
):
    """대시보드 단일 호출: KPI + 14일 트렌드 + Top 5 사이트."""
    return await DashboardService(db).get_overview(
        period=period,
        product=product,
        country=country,
        platform=platform,
    )
