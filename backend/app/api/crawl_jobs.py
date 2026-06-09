from fastapi import APIRouter, Depends, Body, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.database import get_db
from app.schemas.voc import CrawlJobRead, CrawlJobTriggerRequest
from app.services.voc_service import VocService

router = APIRouter(prefix="/crawl-jobs", tags=["crawl-jobs"])


@router.get("", response_model=List[CrawlJobRead])
async def list_crawl_jobs(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """크롤링 작업 이력 조회"""
    return await VocService(db).get_crawl_jobs(limit=limit)


@router.post("/trigger", response_model=CrawlJobRead)
async def trigger_crawl(
    request: CrawlJobTriggerRequest,
    db: AsyncSession = Depends(get_db),
):
    """수동 크롤링 트리거"""
    return await VocService(db).trigger_crawl_job(
        platform_code=request.platform_code,
        product_code=request.product_code,
    )
