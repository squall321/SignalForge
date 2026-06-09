"""
Knowledge Graph API (P2 T1).

- GET /api/v1/kg/graph
- GET /api/v1/kg/node/{node_id}/samples
- GET /api/v1/kg/search
"""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.kg import KGGraphResponse, KGNodeSample, KGSearchHit
from app.services.kg_service import KGService

router = APIRouter(prefix="/kg", tags=["knowledge-graph"])


@router.get("/graph", response_model=KGGraphResponse)
async def kg_graph(
    start: Optional[date] = Query(None, description="시작일 (YYYY-MM-DD)"),
    end: Optional[date] = Query(None, description="종료일 (YYYY-MM-DD)"),
    edge_types: Optional[List[str]] = Query(
        None,
        description="product_category | product_platform | product_country | all (default 전체)",
    ),
    product_ids: Optional[List[str]] = Query(
        None, description="제품 코드 다중 (예: GS25, GS26U)"
    ),
    top_n: int = Query(80, ge=1, le=500),
    min_weight: int = Query(5, ge=1),
    lang: Optional[str] = Query(None, description="ko | en — product label 선호 언어"),
    db: AsyncSession = Depends(get_db),
):
    """기간/엣지타입/제품 필터를 적용한 노드-엣지 그래프 반환."""
    return await KGService(db).get_graph(
        start=start,
        end=end,
        edge_types=edge_types,
        product_ids=product_ids,
        top_n=top_n,
        min_weight=min_weight,
        lang=lang,
    )


@router.get("/node/{node_id}/samples", response_model=List[KGNodeSample])
async def kg_node_samples(
    node_id: str,
    limit: int = Query(5, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """노드(product/category/platform/country) 와 매칭되는 원문 VOC 샘플."""
    if ":" not in node_id:
        raise HTTPException(
            status_code=400, detail="node_id 는 'type:label' 형식이어야 합니다."
        )
    return await KGService(db).get_node_samples(node_id=node_id, limit=limit)


@router.get("/search", response_model=List[KGSearchHit])
async def kg_search(
    q: str = Query(..., min_length=1, description="검색어 (한국어/영어)"),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """products / platforms / voc_keywords 합집합 텍스트 검색."""
    return await KGService(db).search(q=q, limit=limit)
