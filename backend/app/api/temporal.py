"""
T2 시계열+LLM Backend (P2-3) — 3 endpoint.

- GET  /api/v1/analytics/temporal-series
- GET  /api/v1/analytics/temporal-compare
- POST /api/v1/analytics/llm-narrative
"""
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.temporal import (
    LLMNarrativeRequest,
    LLMNarrativeResponse,
    TemporalCompareResponse,
    TemporalSeriesResponse,
)
from app.services.temporal_service import TemporalService


router = APIRouter(prefix="/analytics", tags=["temporal"])


@router.get("/temporal-series", response_model=TemporalSeriesResponse)
async def temporal_series(
    product: Optional[str] = Query(None, description="제품 코드 (예: GS25)"),
    categories: Optional[List[str]] = Query(
        None, description="카테고리 필터 (반복 가능, OR)"
    ),
    from_date: str = Query(..., description="시작일 YYYY-MM-DD"),
    to_date: str = Query(..., description="종료일 YYYY-MM-DD"),
    bucket: str = Query("day", pattern="^(day|week|month)$"),
    metric: str = Query("both", pattern="^(count|sent_avg|both)$"),
    lang: Optional[str] = Query(None, description="언어 코드 (예: ko, en)"),
    include_events: bool = Query(True),
    include_changepoints: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    """기간 시계열 + 이벤트 마커 + change-point 검출."""
    try:
        return await TemporalService(db).get_series(
            product=product,
            categories=categories,
            from_date=from_date,
            to_date=to_date,
            bucket=bucket,
            metric=metric,
            lang=lang,
            include_events=include_events,
            include_changepoints=include_changepoints,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/temporal-compare", response_model=TemporalCompareResponse)
async def temporal_compare(
    mode: str = Query(..., pattern="^(products|periods|categories)$"),
    keys: List[str] = Query(..., description="비교 키 2개 (예: GS25, GS26)"),
    from_date: str = Query(..., description="시작일 YYYY-MM-DD (periods 모드 시 무시)"),
    to_date: str = Query(..., description="종료일 YYYY-MM-DD"),
    bucket: str = Query("day", pattern="^(day|week|month)$"),
    db: AsyncSession = Depends(get_db),
):
    """2 시리즈 비교 + 일별 diff (delta_count / delta_sent)."""
    if len(keys) < 2:
        raise HTTPException(status_code=400, detail="keys must contain >=2 entries")
    try:
        return await TemporalService(db).compare(
            mode=mode,
            keys=keys[:2],
            from_date=from_date,
            to_date=to_date,
            bucket=bucket,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/llm-narrative", response_model=LLMNarrativeResponse)
async def llm_narrative(
    body: LLMNarrativeRequest = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """series_payload → 한국어 narrative 3-5 문단 (24h Redis 캐시).

    ollama qwen2.5:7b 호출 (env: OPENAI_BASE_URL=http://127.0.0.1:11434/v1).
    """
    return await TemporalService(db).llm_narrative(
        series_payload=body.series_payload,
        lang=body.lang,
    )
