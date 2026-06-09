from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, text
import sqlalchemy as sa
from typing import Optional, List
from datetime import datetime, timedelta

from app.core.cache import redis_cache
from app.models import Product, VocRecord, VocCategory
from app.schemas.analytics import (
    SentimentTrendResponse,
    SentimentDataPoint,
    CategoryDistResponse,
    CategoryDistItem,
    CountryHeatmapResponse,
    CountryVOC,
    TopIssuesResponse,
    IssueRanking,
    CompareResponse,
    ProductCompareItem,
    KeywordTrackResponse,
    KeywordTrackPoint,
    CohortCompareResponse,
    CohortSentimentMetric,
    CohortCategoryMetric,
    CohortCategoryItem,
    SiteHealthResponse,
    SiteHealthItem,
    RecentIssuesResponse,
    RecentIssueItem,
)

CATEGORIES = [
    "battery", "camera", "display", "performance",
    "software", "build_quality", "price", "design",
    "connectivity", "ai_features", "accessories", "comparison",
]


class AnalyticsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get_product_id(self, code: str) -> Optional[int]:
        stmt = select(Product.id).where(Product.code == code.upper())
        return (await self.db.execute(stmt)).scalar_one_or_none()

    # ── Sentiment Trend ────────────────────────────────────

    @redis_cache(ttl_seconds=300, key_prefix="analytics:", model_cls=SentimentTrendResponse)
    async def get_sentiment_trend(
        self, product_code: str, period_days: int = 90, granularity: str = "week"
    ) -> dict:
        product_id = await self._get_product_id(product_code)
        since = datetime.utcnow() - timedelta(days=period_days)

        if granularity == "day":
            trunc = "day"
        elif granularity == "month":
            trunc = "month"
        else:
            trunc = "week"

        stmt = text("""
            SELECT
                date_trunc(:trunc, published_at)::date AS period,
                SUM(CASE WHEN sentiment_label = 'positive' THEN 1 ELSE 0 END) AS positive,
                SUM(CASE WHEN sentiment_label = 'negative' THEN 1 ELSE 0 END) AS negative,
                SUM(CASE WHEN sentiment_label = 'neutral'  THEN 1 ELSE 0 END) AS neutral,
                ROUND(AVG(sentiment_score)::numeric, 3)                        AS avg_score
            FROM voc_active
            WHERE product_id = :product_id
              AND published_at >= :since
              AND published_at IS NOT NULL
            GROUP BY period
            ORDER BY period
        """)

        rows = (await self.db.execute(stmt, {
            "trunc": trunc, "product_id": product_id, "since": since
        })).all()

        data = [
            SentimentDataPoint(
                date=str(r.period),
                positive=r.positive or 0,
                negative=r.negative or 0,
                neutral=r.neutral or 0,
                avg_score=float(r.avg_score or 0),
            )
            for r in rows
        ]
        return SentimentTrendResponse(product_code=product_code, granularity=granularity, data=data)

    # ── Category Distribution ─────────────────────────────

    @redis_cache(ttl_seconds=300, key_prefix="analytics:", model_cls=CategoryDistResponse)
    async def get_category_distribution(
        self, product_code: str, period_days: int = 30
    ) -> dict:
        product_id = await self._get_product_id(product_code)
        since = datetime.utcnow() - timedelta(days=period_days)

        stmt = text("""
            SELECT
                unnest(categories) AS category,
                COUNT(*)           AS cnt
            FROM voc_active
            WHERE product_id = :product_id
              AND collected_at >= :since
              AND categories IS NOT NULL
            GROUP BY category
            ORDER BY cnt DESC
        """)
        rows = (await self.db.execute(stmt, {"product_id": product_id, "since": since})).all()

        total = sum(r.cnt for r in rows) or 1
        data = [
            CategoryDistItem(
                category=r.category,
                count=r.cnt,
                percentage=round(r.cnt / total * 100, 1),
            )
            for r in rows
        ]
        return CategoryDistResponse(product_code=product_code, data=data)

    # ── Country Heatmap ────────────────────────────────────

    @redis_cache(ttl_seconds=300, key_prefix="analytics:", model_cls=CountryHeatmapResponse)
    async def get_country_heatmap(
        self, product_code: str, period_days: int = 30
    ) -> dict:
        product_id = await self._get_product_id(product_code)
        since = datetime.utcnow() - timedelta(days=period_days)

        stmt = text("""
            SELECT
                country_code,
                COUNT(*)                                                              AS cnt,
                ROUND(AVG(sentiment_score)::numeric, 3)                              AS avg_score,
                ROUND(
                    SUM(CASE WHEN sentiment_label = 'positive' THEN 1 ELSE 0 END)::numeric
                    / NULLIF(COUNT(*), 0) * 100, 1
                )                                                                     AS pos_rate
            FROM voc_active
            WHERE product_id = :product_id
              AND collected_at >= :since
              AND country_code IS NOT NULL
            GROUP BY country_code
            ORDER BY cnt DESC
        """)
        rows = (await self.db.execute(stmt, {"product_id": product_id, "since": since})).all()

        data = [
            CountryVOC(
                country_code=r.country_code,
                count=r.cnt,
                avg_score=float(r.avg_score or 0),
                positive_rate=float(r.pos_rate or 0),
            )
            for r in rows
        ]
        return CountryHeatmapResponse(product_code=product_code, data=data)

    # ── Top Issues ─────────────────────────────────────────

    @redis_cache(ttl_seconds=300, key_prefix="analytics:", model_cls=TopIssuesResponse)
    async def get_top_issues(
        self, product_code: str, period_days: int = 30, top_n: int = 10
    ) -> dict:
        product_id = await self._get_product_id(product_code)
        since = datetime.utcnow() - timedelta(days=period_days)

        stmt = text("""
            SELECT
                cat                                                                   AS category,
                COUNT(*)                                                              AS cnt,
                ROUND(
                    SUM(CASE WHEN sentiment_label = 'negative' THEN 1 ELSE 0 END)::numeric
                    / NULLIF(COUNT(*), 0) * 100, 1
                )                                                                     AS neg_rate
            FROM voc_active,
                 unnest(categories) AS cat
            WHERE product_id = :product_id
              AND collected_at >= :since
              AND categories IS NOT NULL
            GROUP BY cat
            ORDER BY cnt DESC
            LIMIT :top_n
        """)
        rows = (await self.db.execute(stmt, {
            "product_id": product_id, "since": since, "top_n": top_n
        })).all()

        # 샘플 텍스트 (카테고리별 부정 VOC 3건)
        issues = []
        for i, r in enumerate(rows, 1):
            sample_stmt = text("""
                SELECT content_translated
                FROM voc_active
                WHERE product_id = :product_id
                  AND collected_at >= :since
                  AND :category = ANY(categories)
                  AND sentiment_label = 'negative'
                  AND content_translated IS NOT NULL
                ORDER BY engagement_score DESC NULLS LAST
                LIMIT 3
            """)
            samples = (await self.db.execute(sample_stmt, {
                "product_id": product_id, "since": since, "category": r.category
            })).scalars().all()

            issues.append(IssueRanking(
                rank=i,
                category=r.category,
                count=r.cnt,
                negative_rate=float(r.neg_rate or 0),
                sample_texts=list(samples),
            ))

        return TopIssuesResponse(product_code=product_code, period_days=period_days, issues=issues)

    # ── Product Compare ────────────────────────────────────

    async def compare_products(
        self, product_codes: List[str], period_days: int = 30
    ) -> dict:
        since = datetime.utcnow() - timedelta(days=period_days)
        items = []

        for code in product_codes:
            product_id = await self._get_product_id(code)
            name_stmt = select(Product.name_en).where(Product.id == product_id)
            name = (await self.db.execute(name_stmt)).scalar() or code

            scores = {}
            for cat in ["battery", "camera", "display", "performance",
                         "software", "build_quality", "price", "design"]:
                # 감성점수 -1~1 → 0~100 정규화. 식 전체를 numeric 캐스트해야
                # ROUND(numeric, int) 시그니처에 매칭됨 (double precision 불가)
                stmt = text("""
                    SELECT
                        ROUND(
                            (((AVG(sentiment_score) + 1) / 2 * 100))::numeric, 1
                        ) AS score
                    FROM voc_active
                    WHERE product_id = :product_id
                      AND collected_at >= :since
                      AND :cat = ANY(categories)
                """)
                score = (await self.db.execute(stmt, {
                    "product_id": product_id, "since": since, "cat": cat
                })).scalar()
                scores[cat] = float(score or 50.0)

            items.append(ProductCompareItem(product_code=code, product_name=name, **scores))

        return CompareResponse(products=items)

    # ── Keyword Track ──────────────────────────────────────

    async def get_keyword_track(
        self, keyword: str, period_days: int = 30, granularity: str = "day"
    ) -> KeywordTrackResponse:
        since = datetime.utcnow() - timedelta(days=period_days)
        trunc = granularity if granularity in ("day", "week", "month") else "day"
        pattern = f"%{keyword}%"

        stmt = text("""
            SELECT
                date_trunc(:trunc, COALESCE(published_at, collected_at))::date AS period,
                COUNT(*)                                                       AS cnt,
                SUM(CASE WHEN sentiment_label = 'positive' THEN 1 ELSE 0 END)  AS positive,
                SUM(CASE WHEN sentiment_label = 'negative' THEN 1 ELSE 0 END)  AS negative,
                SUM(CASE WHEN sentiment_label = 'neutral'  THEN 1 ELSE 0 END)  AS neutral,
                ROUND(AVG(sentiment_score)::numeric, 3)                        AS avg_score
            FROM voc_active
            WHERE COALESCE(published_at, collected_at) >= :since
              AND (
                    content_translated ILIKE :pattern
                 OR content_original   ILIKE :pattern
              )
            GROUP BY period
            ORDER BY period
        """)
        rows = (await self.db.execute(stmt, {
            "trunc": trunc, "since": since, "pattern": pattern
        })).all()

        data = [
            KeywordTrackPoint(
                date=str(r.period),
                count=r.cnt or 0,
                positive=r.positive or 0,
                negative=r.negative or 0,
                neutral=r.neutral or 0,
                avg_score=float(r.avg_score or 0),
            )
            for r in rows
        ]
        total = sum(p.count for p in data)
        return KeywordTrackResponse(
            keyword=keyword,
            period_days=period_days,
            granularity=trunc,
            total_matches=total,
            data=data,
        )

    # ── Cohort Compare ─────────────────────────────────────

    async def cohort_compare(
        self, product_codes: List[str], dimension: str = "sentiment",
        period_days: int = 30,
    ) -> CohortCompareResponse:
        since = datetime.utcnow() - timedelta(days=period_days)
        dim = dimension if dimension in ("sentiment", "category") else "sentiment"

        sentiment_items: List[CohortSentimentMetric] = []
        category_items: List[CohortCategoryMetric] = []

        for code in product_codes:
            product_id = await self._get_product_id(code)
            if product_id is None:
                continue
            name_stmt = select(Product.name_en).where(Product.id == product_id)
            name = (await self.db.execute(name_stmt)).scalar() or code

            if dim == "sentiment":
                stmt = text("""
                    SELECT
                        COUNT(*)                                                              AS total,
                        SUM(CASE WHEN sentiment_label = 'positive' THEN 1 ELSE 0 END)         AS positive,
                        SUM(CASE WHEN sentiment_label = 'negative' THEN 1 ELSE 0 END)         AS negative,
                        SUM(CASE WHEN sentiment_label = 'neutral'  THEN 1 ELSE 0 END)         AS neutral,
                        ROUND(AVG(sentiment_score)::numeric, 3)                               AS avg_score
                    FROM voc_active
                    WHERE product_id = :product_id
                      AND collected_at >= :since
                """)
                r = (await self.db.execute(stmt, {
                    "product_id": product_id, "since": since
                })).one()
                total = r.total or 0
                sentiment_items.append(CohortSentimentMetric(
                    product_code=code,
                    product_name=name,
                    total=total,
                    positive=r.positive or 0,
                    negative=r.negative or 0,
                    neutral=r.neutral or 0,
                    positive_rate=round((r.positive or 0) / total * 100, 1) if total else 0.0,
                    negative_rate=round((r.negative or 0) / total * 100, 1) if total else 0.0,
                    avg_score=float(r.avg_score or 0),
                ))
            else:  # category
                stmt = text("""
                    SELECT
                        unnest(categories) AS category,
                        COUNT(*)           AS cnt
                    FROM voc_active
                    WHERE product_id = :product_id
                      AND collected_at >= :since
                      AND categories IS NOT NULL
                    GROUP BY category
                    ORDER BY cnt DESC
                """)
                rows = (await self.db.execute(stmt, {
                    "product_id": product_id, "since": since
                })).all()
                cat_items = [CohortCategoryItem(category=r.category, count=r.cnt) for r in rows]
                total = sum(r.cnt for r in rows)
                category_items.append(CohortCategoryMetric(
                    product_code=code,
                    product_name=name,
                    total=total,
                    categories=cat_items,
                ))

        return CohortCompareResponse(
            dimension=dim,
            period_days=period_days,
            products=product_codes,
            sentiment=sentiment_items if dim == "sentiment" else None,
            category=category_items if dim == "category" else None,
        )

    # ── Site Health ────────────────────────────────────────

    @redis_cache(ttl_seconds=300, key_prefix="analytics:", model_cls=SiteHealthResponse)
    async def get_site_health(self) -> SiteHealthResponse:
        stmt = text("""
            SELECT
                p.code                                                                    AS platform_code,
                p.name                                                                    AS platform_name,
                p.region                                                                  AS region,
                SUM(CASE WHEN v.collected_at >= NOW() - INTERVAL '24 hours' THEN 1 ELSE 0 END) AS count_24h,
                SUM(CASE WHEN v.collected_at >= NOW() - INTERVAL '7 days'   THEN 1 ELSE 0 END) AS count_7d,
                COALESCE(AVG(LENGTH(v.content_original))
                    FILTER (WHERE v.collected_at >= NOW() - INTERVAL '7 days'), 0)        AS avg_len,
                COALESCE(
                    SUM(CASE
                        WHEN v.collected_at >= NOW() - INTERVAL '7 days'
                         AND v.categories IS NOT NULL
                         AND array_length(v.categories, 1) > 0
                        THEN 1 ELSE 0
                    END)::numeric
                    / NULLIF(SUM(CASE WHEN v.collected_at >= NOW() - INTERVAL '7 days' THEN 1 ELSE 0 END), 0)
                    * 100, 0
                )                                                                          AS tagged_rate
            FROM platforms p
            LEFT JOIN voc_records v ON v.platform_id = p.id
            WHERE p.is_active = true
            GROUP BY p.code, p.name, p.region
            ORDER BY count_7d DESC NULLS LAST, p.code
        """)
        rows = (await self.db.execute(stmt)).all()
        sites = [
            SiteHealthItem(
                platform_code=r.platform_code,
                platform_name=r.platform_name,
                region=r.region,
                count_24h=int(r.count_24h or 0),
                count_7d=int(r.count_7d or 0),
                avg_content_length=round(float(r.avg_len or 0), 1),
                tagged_rate=round(float(r.tagged_rate or 0), 1),
            )
            for r in rows
        ]
        return SiteHealthResponse(
            generated_at=datetime.utcnow().isoformat(),
            sites=sites,
        )

    # ── Recent Issues ──────────────────────────────────────

    async def get_recent_issues(
        self, product_code: str, top_n: int = 10
    ) -> RecentIssuesResponse:
        product_id = await self._get_product_id(product_code)
        stmt = text("""
            SELECT
                v.id                                                  AS id,
                p.code                                                AS platform_code,
                v.country_code                                        AS country_code,
                v.sentiment_score                                     AS sentiment_score,
                v.categories                                          AS categories,
                COALESCE(v.content_translated, v.content_original)    AS content,
                v.published_at                                        AS published_at,
                v.engagement_score                                    AS engagement_score
            FROM voc_active v
            LEFT JOIN platforms p ON p.id = v.platform_id
            WHERE v.product_id = :product_id
              AND v.sentiment_label = 'negative'
              AND COALESCE(v.content_translated, v.content_original) IS NOT NULL
            ORDER BY COALESCE(v.published_at, v.collected_at) DESC
            LIMIT :top_n
        """)
        rows = (await self.db.execute(stmt, {
            "product_id": product_id, "top_n": top_n
        })).all()

        items = [
            RecentIssueItem(
                id=r.id,
                platform_code=r.platform_code,
                country_code=r.country_code,
                sentiment_score=float(r.sentiment_score) if r.sentiment_score is not None else None,
                categories=list(r.categories) if r.categories else None,
                content=r.content,
                published_at=r.published_at.isoformat() if r.published_at else None,
                engagement_score=float(r.engagement_score) if r.engagement_score is not None else None,
            )
            for r in rows
        ]
        return RecentIssuesResponse(
            product_code=product_code,
            top_n=top_n,
            issues=items,
        )
