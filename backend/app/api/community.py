"""
T3 커뮤니티 비교 API (P3-3) — 6 endpoint.

prefix: /api/v1/community
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.community import (
    AnomalyEntry,
    ClustersResponse,
    DispersionResponse,
    EarlySignalResponse,
    PlatformHealthResponse,
    ProductMatrixResponse,
)
from app.services.community_service import CommunityService


router = APIRouter(prefix="/community", tags=["community"])


@router.get("/platforms/health", response_model=PlatformHealthResponse)
async def platforms_health(
    region: Optional[str] = Query(None, description="region 필터 (예: KR, US, GLOBAL)"),
    db: AsyncSession = Depends(get_db),
):
    """60+ 플랫폼의 24h/7d 활동량 + 감성 + status (platform_health MV)."""
    return await CommunityService(db).health(region=region)


@router.get("/platforms/product-matrix", response_model=ProductMatrixResponse)
async def platforms_product_matrix(
    since: Optional[str] = Query(None, description="YYYY-MM-DD (기본: 7d 전)"),
    products: Optional[List[str]] = Query(None, description="제품 코드 다중 (예: GS25)"),
    db: AsyncSession = Depends(get_db),
):
    """platform × product 셀 매트릭스 (n / sent_avg / neg_rate)."""
    try:
        return await CommunityService(db).product_matrix(
            since=since,
            products=products,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/platforms/dispersion", response_model=DispersionResponse)
async def platforms_dispersion(
    product_id: Optional[int] = Query(None),
    since: Optional[str] = Query(None, description="YYYY-MM-DD (기본: 14d 전)"),
    db: AsyncSession = Depends(get_db),
):
    """플랫폼별 sentiment_score 분산 boxplot + outlier 30개."""
    try:
        return await CommunityService(db).dispersion(
            product_id=product_id,
            since=since,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/platforms/early-signal", response_model=EarlySignalResponse)
async def platforms_early_signal(
    product_id: Optional[int] = Query(None),
    category: Optional[str] = Query(None, description="VOC 카테고리 (예: battery)"),
    db: AsyncSession = Depends(get_db),
):
    """선행 플랫폼 검출 + 28일 timeline (top 8 platform)."""
    return await CommunityService(db).early_signal(
        product_id=product_id,
        category=category,
    )


@router.get("/platforms/clusters", response_model=ClustersResponse)
async def platforms_clusters(
    k: int = Query(6, ge=2, le=10),
    db: AsyncSession = Depends(get_db),
):
    """플랫폼 sentiment 패턴 (pos_rate, neg_rate) 의 KMeans 클러스터링."""
    return await CommunityService(db).clusters(k=k)


@router.get("/platforms/anomalies", response_model=List[AnomalyEntry])
async def platforms_anomalies(
    db: AsyncSession = Depends(get_db),
):
    """이상 신호 4종: dead_7d / idle_24h / extreme_negative_7d / drop_rate."""
    return await CommunityService(db).anomalies()
