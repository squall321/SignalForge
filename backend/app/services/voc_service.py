from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, cast, text
from sqlalchemy.dialects.postgresql import ARRAY
import sqlalchemy as sa
from typing import Optional, List
from datetime import datetime, timedelta

from app.models import Product, Platform, VocRecord, CrawlJob
from app.schemas.voc import ProductRead, VocListResponse, VocRecordRead, ProductStats, CrawlJobRead


class VocService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Products ──────────────────────────────────────────

    async def get_products(
        self, series: Optional[str] = None, is_active: bool = True
    ) -> List[Product]:
        stmt = select(Product).where(Product.is_active == is_active)
        if series:
            stmt = stmt.where(Product.series_code == series.upper())
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_product_by_code(self, code: str) -> Optional[Product]:
        stmt = select(Product).where(Product.code == code.upper())
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    # ── VOC ───────────────────────────────────────────────

    async def get_product_voc(
        self,
        product_code: str,
        countries: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None,
        sentiment: Optional[str] = None,
        category: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        lang: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        product = await self.get_product_by_code(product_code)
        if not product:
            return {"total": 0, "items": [], "limit": limit, "offset": offset}

        conditions = [VocRecord.product_id == product.id]

        if countries:
            conditions.append(VocRecord.country_code.in_([c.upper() for c in countries]))
        if sentiment:
            conditions.append(VocRecord.sentiment_label == sentiment)
        if category:
            conditions.append(VocRecord.categories.any(category))
        if lang:
            conditions.append(VocRecord.language_detected == lang)
        if from_date:
            conditions.append(VocRecord.published_at >= datetime.fromisoformat(from_date))
        if to_date:
            conditions.append(VocRecord.published_at <= datetime.fromisoformat(to_date))

        if platforms:
            platform_stmt = select(Platform.id).where(Platform.code.in_(platforms))
            platform_result = await self.db.execute(platform_stmt)
            platform_ids = [r[0] for r in platform_result.all()]
            if platform_ids:
                conditions.append(VocRecord.platform_id.in_(platform_ids))

        where_clause = and_(*conditions)

        # 총 건수
        count_stmt = select(func.count()).select_from(VocRecord).where(where_clause)
        total = (await self.db.execute(count_stmt)).scalar()

        # 데이터 조회
        stmt = (
            select(VocRecord)
            .where(where_clause)
            .order_by(VocRecord.collected_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        items = result.scalars().all()

        return {
            "total": total,
            "items": items,
            "limit": limit,
            "offset": offset,
        }

    async def get_product_stats(self, product_code: str) -> dict:
        product = await self.get_product_by_code(product_code)
        if not product:
            return {}

        stmt = select(
            func.count().label("total"),
            func.sum(sa.case((VocRecord.sentiment_label == "positive", 1), else_=0)).label("positive"),
            func.sum(sa.case((VocRecord.sentiment_label == "negative", 1), else_=0)).label("negative"),
            func.sum(sa.case((VocRecord.sentiment_label == "neutral", 1), else_=0)).label("neutral"),
            func.avg(VocRecord.sentiment_score).label("avg_score"),
            func.max(VocRecord.collected_at).label("latest"),
        ).where(VocRecord.product_id == product.id)

        row = (await self.db.execute(stmt)).one()
        total = row.total or 0
        pos = row.positive or 0
        neg = row.negative or 0

        return {
            "product_code": product.code,
            "product_name": product.name_en,
            "total_voc": total,
            "positive_count": pos,
            "negative_count": neg,
            "neutral_count": row.neutral or 0,
            "positive_rate": round(pos / total * 100, 1) if total else 0.0,
            "negative_rate": round(neg / total * 100, 1) if total else 0.0,
            "avg_sentiment_score": round(float(row.avg_score or 0), 3),
            "latest_collected_at": row.latest,
        }

    # ── CrawlJobs ─────────────────────────────────────────

    async def get_crawl_jobs(self, limit: int = 50) -> List[CrawlJob]:
        stmt = select(CrawlJob).order_by(CrawlJob.id.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def trigger_crawl_job(
        self, platform_code: str, product_code: Optional[str] = None
    ) -> CrawlJob:
        """크롤링 작업 레코드 생성 후 Celery 태스크 발행"""
        platform_stmt = select(Platform).where(Platform.code == platform_code)
        platform = (await self.db.execute(platform_stmt)).scalar_one_or_none()

        product = None
        if product_code:
            product = await self.get_product_by_code(product_code)

        job = CrawlJob(
            platform_id=platform.id if platform else None,
            product_id=product.id if product else None,
            status="pending",
        )
        self.db.add(job)
        await self.db.flush()

        # Celery 태스크 발행 (지연 import로 순환 참조 방지)
        try:
            from crawler.tasks import crawl_platform  # type: ignore
            crawl_platform.delay(platform_code, product_code, job.id)
        except ImportError:
            pass  # 크롤러가 분리된 컨테이너에 있을 경우

        return job
