"""
P3.6 트랙 A — 심층 분석 8 endpoint 서비스.

데이터 소스 매핑 (실제 스키마 기준):
- voc_records.published_at      : 시간 축
- voc_records.categories        : TEXT[] (UNNEST 필요)
- voc_records.sentiment_label   : 'positive'|'negative'|'neutral'
- voc_records.sentiment_score   : -1..1
- voc_records.country_code      : ISO-2 (27 distinct)
- voc_keywords.voc_id           : voc_records.id 와 join (record_id 아님)
- platforms.region              : 사이트 region/country (RU/US/KR/JP 등)
- timeline_events.event_type    : 'release' (이벤트 매칭)

anchor:
- voc_records.published_at 의 max(future cap +1d) 를 "현재" 로 간주.
- emerging/new-terms 와 일관되게 anchor - period_days 윈도우 사용.
"""
from __future__ import annotations

import logging
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import redis_cache
from app.schemas.deep import (
    AnomalyContextResponse,
    AnomalyDrilldownHourResponse,
    AnomalyDrilldownResponse,
    AnomalySummary,
    DrilldownHourPlatformRef,
    DrilldownHourProductRef,
    DrilldownHourVocItem,
    AnomalyWithDriversEntry,
    AnomalyWithDriversResponse,
    CategoryMomentumItem,
    CategoryMomentumResponse,
    CategoryProductMatrixResponse,
    CooccurEdge,
    CooccurNode,
    CooccurPair,
    CountrySentimentGapResponse,
    CountrySentimentItem,
    DiffusionEdge,
    DiffusionHop,
    DiffusionKeyword,
    DrilldownHourBucket,
    DrilldownKeyword,
    DrilldownPlatform,
    DrilldownProduct,
    EngagementBucket,
    EngagementByCategory,
    EngagementSentimentResponse,
    InfluenceDrivers,
    InfluenceRankItem,
    InfluenceRankResponse,
    IssueLifecycleResponse,
    KeywordCooccurrenceResponse,
    KeywordDelta,
    KeywordDetailCategory,
    KeywordDetailPlatformStat,
    KeywordDetailProductStat,
    KeywordDetailRelated,
    KeywordDetailResponse,
    KeywordDetailSample,
    KeywordDetailStats,
    KeywordNetworkResponse,
    LifecycleCategoryAvg,
    LifecycleFunnelExample,
    LifecycleFunnelResponse,
    LifecycleFunnelStage,
    LifecycleItem,
    MatchedEvent,
    MatrixCell,
    MomentumWeekPoint,
    NetworkEdge,
    NetworkNode,
    NewTermSurvivalResponse,
    ProductFunnelResponse,
    ProductFunnelStage,
    SentimentDriverItem,
    SentimentDriverResponse,
    SiteDiffusionResponse,
    SpikeEntry,
    SurvivalItem,
    SurvivalSummary,
    TopDriver,
    TopGapEntry,
    # R9 galaxy-history
    GalaxyTimelineResponse,
    GalaxyTimelineModel,
    CrisisCasesResponse,
    CrisisCase,
    CrisisCaseTimelinePoint,
    CrisisCaseKeyword,
    CrisisCaseSite,
    SeriesComparisonResponse,
    SeriesComparisonSeries,
    SeriesComparisonGenPoint,
)


# 위기 사례 카탈로그 (R9 트랙 A)
# product_code 는 products.code (GN7=Note 7, GZF1=Galaxy Fold, GS22U=Galaxy S22 Ultra)
# period 는 위기 발생 전후 기간 — DB anchor 와 무관하게 고정.
CRISIS_CATALOG: List[Dict[str, Any]] = [
    {
        "code": "GN7",
        "title": "Galaxy Note 7 발화",
        "description": "2016년 8월 출시 직후 배터리 발화 사건 → 9월 1차 리콜, 10월 단종.",
        "period_start": "2016-08-19",
        "period_end": "2016-12-31",
    },
    {
        "code": "GZF1",
        "title": "Galaxy Fold 1 디스플레이 결함",
        "description": "2019년 4월 리뷰 단계에서 화면 결함이 보고되어 출시 연기, 9월 재출시.",
        "period_start": "2019-04-15",
        "period_end": "2019-12-31",
    },
    {
        "code": "GS22U",
        "title": "Galaxy S22 GoS 게임 성능 제한",
        "description": "2022년 2월 출시 후 GOS(Game Optimizing Service) 성능 제한 논란.",
        "period_start": "2022-02-25",
        "period_end": "2022-06-30",
    },
    {
        "code": "GZFL3",
        "title": "Galaxy Z Flip 3 힌지 논란",
        "description": "2021년 8월 출시 직후 힌지 헐거움/주름 부각, 2022년 초까지 품질 이슈 보고.",
        "period_start": "2021-08-01",
        "period_end": "2022-03-31",
    },
    {
        "code": "GS20",
        "title": "Galaxy S20 5G 가격 논란",
        "description": "2020년 2월 5G 기본 탑재로 100만원대 진입, 가격 정책 논란.",
        "period_start": "2020-02-01",
        "period_end": "2020-12-31",
    },
]

logger = logging.getLogger(__name__)


CACHE_TTL = 600


class DeepService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ----------------------------------------------------------------
    async def _anchor_date(self) -> date:
        r = await self.db.execute(
            text(
                "SELECT MAX(published_at::date) AS d FROM voc_active "
                "WHERE published_at <= NOW() + INTERVAL '1 day' "
                "AND archived_at IS NULL"
            )
        )
        d = r.scalar()
        if d is None:
            return datetime.now(timezone.utc).date()
        return d  # type: ignore[return-value]

    # ================================================================
    # 1) issue-lifecycle
    # ================================================================
    @redis_cache(ttl_seconds=CACHE_TTL, key_prefix="deep:", model_cls=IssueLifecycleResponse)
    async def issue_lifecycle(
        self,
        category: Optional[str],
        period_days: int,
        top_n: int,
    ) -> IssueLifecycleResponse:
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        params: Dict[str, Any] = {"d_from": d_from, "d_to": anchor, "top_n": top_n}
        cat_filter = ""
        if category:
            params["cat"] = category
            cat_filter = "AND :cat = ANY(vr.categories)"

        sql = f"""
            WITH neg AS (
                SELECT vr.id,
                       UNNEST(vr.categories) AS category,
                       vk.keyword,
                       vr.published_at::date AS d
                FROM voc_active vr
                JOIN voc_keywords vk ON vk.voc_id = vr.id
                WHERE vr.sentiment_label = 'negative'
                  AND vr.archived_at IS NULL
                  AND vr.published_at::date >= :d_from
                  AND vr.published_at::date <= :d_to
                  AND vr.categories IS NOT NULL
                  {cat_filter}
            ),
            daily AS (
                SELECT keyword, category, d, COUNT(*) AS cnt
                FROM neg
                GROUP BY keyword, category, d
            ),
            agg AS (
                SELECT keyword,
                       category,
                       MIN(d) AS first_seen,
                       MAX(d) AS last_seen,
                       (array_agg(d ORDER BY cnt DESC))[1] AS peak_day,
                       MAX(cnt) AS intensity
                FROM daily
                GROUP BY keyword, category
            )
            SELECT category, keyword, first_seen, peak_day, last_seen,
                   (peak_day - first_seen) AS days_to_peak,
                   (last_seen - first_seen) AS lifespan,
                   intensity
            FROM agg
            WHERE (last_seen - first_seen) >= 1
            ORDER BY lifespan DESC, intensity DESC
            LIMIT :top_n
        """
        rows = (await self.db.execute(text(sql), params)).all()

        items = [
            LifecycleItem(
                category=r.category,
                keyword=r.keyword,
                first_seen=str(r.first_seen),
                peak_day=str(r.peak_day),
                last_seen=str(r.last_seen),
                days_to_peak=int(r.days_to_peak or 0),
                lifespan=int(r.lifespan or 0),
                intensity=int(r.intensity or 0),
            )
            for r in rows
        ]

        # category avg (전체 모집단 — top_n 와 별개)
        avg_sql = f"""
            WITH neg AS (
                SELECT UNNEST(vr.categories) AS category,
                       vk.keyword,
                       vr.published_at::date AS d
                FROM voc_active vr
                JOIN voc_keywords vk ON vk.voc_id = vr.id
                WHERE vr.sentiment_label = 'negative'
                  AND vr.archived_at IS NULL
                  AND vr.published_at::date >= :d_from
                  AND vr.published_at::date <= :d_to
                  AND vr.categories IS NOT NULL
                  {cat_filter}
            ),
            daily AS (
                SELECT category, keyword, d, COUNT(*) AS cnt
                FROM neg GROUP BY category, keyword, d
            ),
            agg AS (
                SELECT category, keyword,
                       MIN(d) AS first_seen, MAX(d) AS last_seen,
                       (array_agg(d ORDER BY cnt DESC))[1] AS peak_day
                FROM daily GROUP BY category, keyword
            )
            SELECT category,
                   AVG(last_seen - first_seen)::float AS avg_lifespan,
                   AVG(peak_day - first_seen)::float  AS avg_days_to_peak,
                   COUNT(*) AS n_issues
            FROM agg
            WHERE (last_seen - first_seen) >= 1
            GROUP BY category
            ORDER BY n_issues DESC
        """
        avg_rows = (await self.db.execute(text(avg_sql), params)).all()
        category_avg = [
            LifecycleCategoryAvg(
                category=r.category or "(none)",
                avg_lifespan=round(float(r.avg_lifespan or 0), 2),
                avg_days_to_peak=round(float(r.avg_days_to_peak or 0), 2),
                n_issues=int(r.n_issues or 0),
            )
            for r in avg_rows
        ]

        return IssueLifecycleResponse(
            items=items,
            category_avg=category_avg,
            meta={
                "anchor_date": str(anchor),
                "period_days": period_days,
                "category": category,
                "top_n": top_n,
                "total": len(items),
            },
        )

    # ================================================================
    # 2) category-product-matrix
    # ================================================================
    @redis_cache(ttl_seconds=CACHE_TTL, key_prefix="deep:", model_cls=CategoryProductMatrixResponse)
    async def category_product_matrix(
        self,
        period_days: int,
        top_products: int,
    ) -> CategoryProductMatrixResponse:
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        sql = """
            WITH top_p AS (
                SELECT product_id, COUNT(*) AS n
                FROM voc_active
                WHERE published_at::date >= :d_from
                  AND published_at::date <= :d_to
                  AND product_id IS NOT NULL
                GROUP BY product_id
                ORDER BY n DESC
                LIMIT :top_products
            ),
            base AS (
                SELECT p.code AS product,
                       UNNEST(vr.categories) AS category,
                       vr.sentiment_label AS s
                FROM voc_active vr
                JOIN top_p ON top_p.product_id = vr.product_id
                JOIN products p ON p.id = vr.product_id
                WHERE vr.published_at::date >= :d_from
                  AND vr.published_at::date <= :d_to
                  AND vr.categories IS NOT NULL
            ),
            cell AS (
                SELECT product, category,
                       AVG(CASE WHEN s='positive' THEN 1.0
                                WHEN s='negative' THEN -1.0
                                ELSE 0.0 END) AS score,
                       COUNT(*) AS n
                FROM base
                GROUP BY product, category
                HAVING COUNT(*) >= 5
            ),
            stats AS (
                SELECT category, AVG(score) AS mu, STDDEV(score) AS sd
                FROM cell GROUP BY category
            )
            SELECT c.product, c.category, c.score, c.n,
                   CASE WHEN s.sd IS NULL OR s.sd = 0 THEN 0
                        ELSE (c.score - s.mu) / s.sd END AS zscore
            FROM cell c JOIN stats s USING (category)
            ORDER BY c.product, c.category
        """
        rows = (
            await self.db.execute(
                text(sql),
                {"d_from": d_from, "d_to": anchor, "top_products": top_products},
            )
        ).all()

        cells: List[MatrixCell] = []
        products_set: set = set()
        cats_set: set = set()
        for r in rows:
            z = float(r.zscore or 0)
            flag = (
                "outlier_neg" if z <= -2.0
                else "outlier_pos" if z >= 2.0
                else "normal"
            )
            cells.append(
                MatrixCell(
                    product=r.product,
                    category=r.category,
                    score=round(float(r.score or 0), 4),
                    n=int(r.n or 0),
                    zscore=round(z, 3),
                    flag=flag,
                )
            )
            products_set.add(r.product)
            cats_set.add(r.category)

        return CategoryProductMatrixResponse(
            products=sorted(products_set),
            categories=sorted(cats_set),
            cells=cells,
            meta={
                "anchor_date": str(anchor),
                "period_days": period_days,
                "top_products": top_products,
                "min_cell_n": 5,
                "total_cells": len(cells),
            },
        )

    # ================================================================
    # 3) site-diffusion
    # ================================================================
    @redis_cache(ttl_seconds=CACHE_TTL, key_prefix="deep:", model_cls=SiteDiffusionResponse)
    async def site_diffusion(
        self,
        period_days: int,
        min_sites: int,
        top_keywords: int,
    ) -> SiteDiffusionResponse:
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        sql = """
            WITH kw_site AS (
                SELECT vk.keyword,
                       pl.code AS site,
                       MIN(vr.published_at::date) AS first_seen
                FROM voc_active vr
                JOIN voc_keywords vk ON vk.voc_id = vr.id
                JOIN platforms pl ON pl.id = vr.platform_id
                WHERE vr.published_at::date >= :d_from
                  AND vr.published_at::date <= :d_to
                GROUP BY vk.keyword, pl.code
            ),
            multi AS (
                SELECT keyword
                FROM kw_site
                GROUP BY keyword
                HAVING COUNT(DISTINCT site) >= :min_sites
            ),
            ranked AS (
                SELECT k.keyword, k.site, k.first_seen,
                       ROW_NUMBER() OVER (PARTITION BY k.keyword
                                          ORDER BY k.first_seen, k.site) AS hop
                FROM kw_site k
                JOIN multi m ON m.keyword = k.keyword
            )
            SELECT keyword, site, first_seen, hop,
                   first_seen - LAG(first_seen) OVER (PARTITION BY keyword ORDER BY hop) AS lag_days
            FROM ranked
            ORDER BY keyword, hop
        """
        rows = (
            await self.db.execute(
                text(sql),
                {"d_from": d_from, "d_to": anchor, "min_sites": min_sites},
            )
        ).all()

        by_kw: Dict[str, List[Any]] = {}
        for r in rows:
            by_kw.setdefault(r.keyword, []).append(r)

        keywords: List[DiffusionKeyword] = []
        edge_acc: Dict[Tuple[str, str], List[int]] = {}
        # 처음 top_keywords 개로 한정
        for kw, items in list(by_kw.items())[:top_keywords]:
            items_sorted = sorted(items, key=lambda x: x.hop)
            path = [
                DiffusionHop(
                    site=it.site,
                    first_seen=str(it.first_seen),
                    hop=int(it.hop),
                    lag_days=int(it.lag_days) if it.lag_days is not None else None,
                )
                for it in items_sorted
            ]
            if not path:
                continue
            origin = path[0].site
            terminal = path[-1].site
            total_span = (
                items_sorted[-1].first_seen - items_sorted[0].first_seen
            ).days
            keywords.append(
                DiffusionKeyword(
                    keyword=kw,
                    path=path,
                    total_span_days=total_span,
                    origin_site=origin,
                    terminal_site=terminal,
                )
            )
            # edge: hop n → hop n+1
            for a, b in zip(items_sorted, items_sorted[1:]):
                edge_acc.setdefault((a.site, b.site), []).append(
                    int((b.first_seen - a.first_seen).days)
                )

        edges = [
            DiffusionEdge(
                from_site=fs,
                to_site=ts,
                count=len(lags),
                avg_lag=round(statistics.fmean(lags) if lags else 0.0, 2),
            )
            for (fs, ts), lags in sorted(
                edge_acc.items(), key=lambda kv: -len(kv[1])
            )
        ]

        return SiteDiffusionResponse(
            keywords=keywords,
            edges=edges,
            meta={
                "anchor_date": str(anchor),
                "period_days": period_days,
                "min_sites": min_sites,
                "top_keywords": top_keywords,
                "total_keywords": len(keywords),
                "total_edges": len(edges),
            },
        )

    # ================================================================
    # 4) country-sentiment-gap
    # ================================================================
    @redis_cache(ttl_seconds=CACHE_TTL, key_prefix="deep:", model_cls=CountrySentimentGapResponse)
    async def country_sentiment_gap(
        self,
        period_days: int,
        top_products: int,
        min_n: int,
    ) -> CountrySentimentGapResponse:
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        sql = """
            WITH top_p AS (
                SELECT product_id, COUNT(*) AS n
                FROM voc_active
                WHERE published_at::date >= :d_from
                  AND published_at::date <= :d_to
                  AND product_id IS NOT NULL
                GROUP BY product_id
                ORDER BY n DESC
                LIMIT :top_products
            ),
            base AS (
                SELECT p.code AS product,
                       COALESCE(vr.country_code, pl.region, 'UNK') AS country,
                       vr.sentiment_label AS s
                FROM voc_active vr
                JOIN top_p ON top_p.product_id = vr.product_id
                JOIN products p ON p.id = vr.product_id
                LEFT JOIN platforms pl ON pl.id = vr.platform_id
                WHERE vr.published_at::date >= :d_from
                  AND vr.published_at::date <= :d_to
            ),
            agg AS (
                SELECT product, country,
                       SUM(CASE WHEN s='positive' THEN 1 ELSE 0 END) AS pos,
                       SUM(CASE WHEN s='negative' THEN 1 ELSE 0 END) AS neg,
                       COUNT(*) AS total
                FROM base
                GROUP BY product, country
                HAVING COUNT(*) >= :min_n
            ),
            scored AS (
                SELECT product, country,
                       (pos - neg)::float / NULLIF(total,0) AS score,
                       total
                FROM agg
            )
            SELECT product, country, score, total,
                   score - AVG(score) OVER (PARTITION BY product) AS gap_vs_global
            FROM scored
            ORDER BY product, score DESC
        """
        rows = (
            await self.db.execute(
                text(sql),
                {
                    "d_from": d_from,
                    "d_to": anchor,
                    "top_products": top_products,
                    "min_n": min_n,
                },
            )
        ).all()

        items: List[CountrySentimentItem] = []
        per_product: Dict[str, List[Tuple[str, float]]] = {}
        for r in rows:
            score = round(float(r.score or 0), 4)
            gap = round(float(r.gap_vs_global or 0), 4)
            items.append(
                CountrySentimentItem(
                    product=r.product,
                    country=r.country,
                    score=score,
                    n=int(r.total or 0),
                    gap_vs_global=gap,
                )
            )
            per_product.setdefault(r.product, []).append((r.country, score))

        top_gaps: List[TopGapEntry] = []
        for prod, lst in per_product.items():
            if len(lst) < 2:
                continue
            hi = max(lst, key=lambda x: x[1])
            lo = min(lst, key=lambda x: x[1])
            top_gaps.append(
                TopGapEntry(
                    product=prod,
                    country_high=hi[0],
                    country_low=lo[0],
                    gap=round(hi[1] - lo[1], 4),
                )
            )
        top_gaps.sort(key=lambda x: x.gap, reverse=True)

        return CountrySentimentGapResponse(
            items=items,
            top_gaps=top_gaps[:20],
            meta={
                "anchor_date": str(anchor),
                "period_days": period_days,
                "top_products": top_products,
                "min_n": min_n,
                "total_items": len(items),
            },
        )

    # ================================================================
    # 5) engagement-sentiment
    # ================================================================
    @redis_cache(ttl_seconds=CACHE_TTL, key_prefix="deep:", model_cls=EngagementSentimentResponse)
    async def engagement_sentiment(
        self,
        period_days: int,
    ) -> EngagementSentimentResponse:
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        sql = """
            WITH base AS (
                SELECT vr.id,
                       UNNEST(vr.categories) AS category,
                       vr.sentiment_label AS s,
                       COALESCE(vr.comments_count,0)
                         + COALESCE(vr.likes_count,0)
                         + COALESCE(vr.shares_count,0) AS eng
                FROM voc_active vr
                WHERE vr.published_at::date >= :d_from
                  AND vr.published_at::date <= :d_to
                  AND vr.categories IS NOT NULL
            ),
            q AS (
                SELECT *, NTILE(5) OVER (ORDER BY eng) AS bucket
                FROM base
            )
            SELECT bucket, category,
                   AVG(CASE WHEN s='positive' THEN 1.0
                            WHEN s='negative' THEN -1.0
                            ELSE 0.0 END) AS score,
                   SUM(CASE WHEN s='negative' THEN 1 ELSE 0 END)::float
                     / NULLIF(COUNT(*),0) AS neg_ratio,
                   COUNT(*) AS n,
                   MIN(eng) AS eng_min,
                   MAX(eng) AS eng_max
            FROM q
            GROUP BY bucket, category
            ORDER BY bucket, category
        """
        rows = (
            await self.db.execute(
                text(sql),
                {"d_from": d_from, "d_to": anchor},
            )
        ).all()

        # 버킷별 (전체 카테고리 통합)
        bucket_agg: Dict[int, Dict[str, Any]] = {}
        by_cat: Dict[str, List[Tuple[int, float]]] = {}
        for r in rows:
            b = int(r.bucket)
            entry = bucket_agg.setdefault(
                b,
                {"n": 0, "neg": 0.0, "score_sum": 0.0,
                 "eng_min": r.eng_min, "eng_max": r.eng_max},
            )
            n = int(r.n or 0)
            entry["n"] += n
            entry["neg"] += float(r.neg_ratio or 0) * n
            entry["score_sum"] += float(r.score or 0) * n
            entry["eng_min"] = min(entry["eng_min"], int(r.eng_min or 0))
            entry["eng_max"] = max(entry["eng_max"], int(r.eng_max or 0))
            by_cat.setdefault(r.category, []).append(
                (b, float(r.neg_ratio or 0))
            )

        buckets: List[EngagementBucket] = []
        for b in sorted(bucket_agg.keys()):
            d = bucket_agg[b]
            n = max(d["n"], 1)
            buckets.append(
                EngagementBucket(
                    bucket=b,
                    eng_range=f"{int(d['eng_min'])}~{int(d['eng_max'])}",
                    score=round(d["score_sum"] / n, 4),
                    neg_ratio=round(d["neg"] / n, 4),
                    n=int(d["n"]),
                )
            )

        # 카테고리별 corr(bucket, neg_ratio) — Spearman 근사
        by_category: List[EngagementByCategory] = []
        for cat, pairs in by_cat.items():
            if len(pairs) < 2:
                continue
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            try:
                # Pearson on rank-equivalent ints — sufficient sign indicator
                mx = sum(xs) / len(xs)
                my = sum(ys) / len(ys)
                num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
                dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
                dy = math.sqrt(sum((y - my) ** 2 for y in ys))
                corr = num / (dx * dy) if dx > 0 and dy > 0 else 0.0
            except Exception:
                corr = 0.0
            top_b = max(pairs, key=lambda p: p[1])[0]
            by_category.append(
                EngagementByCategory(
                    category=cat,
                    corr_eng_neg=round(corr, 4),
                    top_bucket=int(top_b),
                )
            )
        by_category.sort(key=lambda x: abs(x.corr_eng_neg), reverse=True)

        return EngagementSentimentResponse(
            buckets=buckets,
            by_category=by_category,
            meta={
                "anchor_date": str(anchor),
                "period_days": period_days,
                "n_buckets": 5,
                "total_categories": len(by_category),
            },
        )

    # ================================================================
    # 6) new-term-survival
    # ================================================================
    @redis_cache(ttl_seconds=CACHE_TTL, key_prefix="deep:", model_cls=NewTermSurvivalResponse)
    async def new_term_survival(
        self,
        period_days: int,
        lookback_window: int,
        min_mentions: int,
    ) -> NewTermSurvivalResponse:
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        sql = """
            WITH kw_first AS (
                SELECT vk.keyword,
                       MIN(vr.published_at::date) AS first_day
                FROM voc_active vr
                JOIN voc_keywords vk ON vk.voc_id = vr.id
                GROUP BY vk.keyword
                HAVING MIN(vr.published_at::date) >= :d_from
                   AND MIN(vr.published_at::date) <= :d_to
            ),
            daily AS (
                SELECT vk.keyword, vr.published_at::date AS d, COUNT(*) AS c
                FROM voc_active vr
                JOIN voc_keywords vk ON vk.voc_id = vr.id
                GROUP BY vk.keyword, vr.published_at::date
            ),
            span AS (
                SELECT k.keyword,
                       k.first_day,
                       MAX(d.d) AS last_day,
                       COUNT(DISTINCT d.d) AS active_days,
                       SUM(d.c) AS total
                FROM kw_first k
                JOIN daily d ON d.keyword = k.keyword
                WHERE d.d BETWEEN k.first_day AND k.first_day + (:lookback)::int
                GROUP BY k.keyword, k.first_day
                HAVING SUM(d.c) >= :min_mentions
            )
            SELECT keyword, first_day, last_day,
                   (last_day - first_day) AS survival_days,
                   active_days,
                   total,
                   CASE WHEN active_days >= 5 AND (last_day - first_day) >= 7 THEN 'sustained'
                        WHEN active_days <= 2 THEN 'flash'
                        ELSE 'mid' END AS cls
            FROM span
            ORDER BY survival_days DESC, total DESC
        """
        rows = (
            await self.db.execute(
                text(sql),
                {
                    "d_from": d_from,
                    "d_to": anchor,
                    "lookback": lookback_window,
                    "min_mentions": min_mentions,
                },
            )
        ).all()

        items = [
            SurvivalItem(
                keyword=r.keyword,
                first_day=str(r.first_day),
                last_day=str(r.last_day),
                survival_days=int(r.survival_days or 0),
                active_days=int(r.active_days or 0),
                total=int(r.total or 0),
                cls=r.cls,
            )
            for r in rows
        ]
        counts = {"sustained": 0, "mid": 0, "flash": 0}
        for it in items:
            counts[it.cls] = counts.get(it.cls, 0) + 1
        avg_surv = (
            round(sum(it.survival_days for it in items) / len(items), 2)
            if items else 0.0
        )

        return NewTermSurvivalResponse(
            items=items,
            summary=SurvivalSummary(
                sustained=counts["sustained"],
                mid=counts["mid"],
                flash=counts["flash"],
                avg_survival=avg_surv,
            ),
            meta={
                "anchor_date": str(anchor),
                "period_days": period_days,
                "lookback_window": lookback_window,
                "min_mentions": min_mentions,
                "total": len(items),
            },
        )

    # ================================================================
    # 7) keyword-cooccurrence
    # ================================================================
    @redis_cache(ttl_seconds=CACHE_TTL, key_prefix="deep:", model_cls=KeywordCooccurrenceResponse)
    async def keyword_cooccurrence(
        self,
        period_days: int,
        min_edge_weight: int,
        top_nodes: int,
    ) -> KeywordCooccurrenceResponse:
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        sql = """
            WITH base AS (
                SELECT a.voc_id, a.keyword AS k1, b.keyword AS k2
                FROM voc_keywords a
                JOIN voc_keywords b
                  ON a.voc_id = b.voc_id AND a.keyword < b.keyword
                JOIN voc_records vr ON vr.id = a.voc_id
                WHERE vr.published_at::date >= :d_from
                  AND vr.published_at::date <= :d_to
            ),
            edges AS (
                SELECT k1, k2, COUNT(*) AS w
                FROM base
                GROUP BY k1, k2
                HAVING COUNT(*) >= :min_edge_weight
            ),
            kw_freq AS (
                SELECT vk.keyword, COUNT(*) AS f
                FROM voc_keywords vk
                JOIN voc_records vr ON vr.id = vk.voc_id
                WHERE vr.published_at::date >= :d_from
                  AND vr.published_at::date <= :d_to
                GROUP BY vk.keyword
            ),
            total_n AS (
                SELECT COUNT(DISTINCT id) AS n FROM voc_active
                WHERE published_at::date >= :d_from
                  AND published_at::date <= :d_to
            ),
            kw_sent AS (
                SELECT vk.keyword,
                       AVG(CASE WHEN vr.sentiment_label='positive' THEN 1.0
                                WHEN vr.sentiment_label='negative' THEN -1.0
                                ELSE 0.0 END) AS bias
                FROM voc_keywords vk
                JOIN voc_records vr ON vr.id = vk.voc_id
                WHERE vr.published_at::date >= :d_from
                  AND vr.published_at::date <= :d_to
                GROUP BY vk.keyword
            )
            SELECT e.k1, e.k2, e.w,
                   f1.f AS f1, f2.f AS f2,
                   tn.n AS n_total,
                   s1.bias AS s1, s2.bias AS s2
            FROM edges e
            JOIN kw_freq f1 ON f1.keyword = e.k1
            JOIN kw_freq f2 ON f2.keyword = e.k2
            JOIN kw_sent s1 ON s1.keyword = e.k1
            JOIN kw_sent s2 ON s2.keyword = e.k2
            CROSS JOIN total_n tn
            ORDER BY e.w DESC
        """
        rows = (
            await self.db.execute(
                text(sql),
                {
                    "d_from": d_from,
                    "d_to": anchor,
                    "min_edge_weight": min_edge_weight,
                },
            )
        ).all()

        # 노드 degree 집계
        node_deg: Dict[str, int] = {}
        node_sent: Dict[str, float] = {}
        all_edges: List[Tuple[str, str, int, float, float, float]] = []
        for r in rows:
            w = int(r.w)
            n_total = max(int(r.n_total or 1), 1)
            # lift = (w/n_total) / ((f1/n_total)*(f2/n_total))
            f1 = max(int(r.f1 or 1), 1)
            f2 = max(int(r.f2 or 1), 1)
            lift = (w * n_total) / (f1 * f2)
            sent_skew = (float(r.s1 or 0) + float(r.s2 or 0)) / 2.0
            node_deg[r.k1] = node_deg.get(r.k1, 0) + w
            node_deg[r.k2] = node_deg.get(r.k2, 0) + w
            node_sent[r.k1] = float(r.s1 or 0)
            node_sent[r.k2] = float(r.s2 or 0)
            all_edges.append((r.k1, r.k2, w, round(lift, 3), sent_skew, w))

        # 상위 top_nodes 만 유지 (degree 기준)
        top_node_ids = set(
            k for k, _ in sorted(node_deg.items(), key=lambda x: -x[1])[:top_nodes]
        )
        nodes = [
            CooccurNode(
                id=k,
                degree=int(node_deg[k]),
                sentiment_bias=round(node_sent.get(k, 0.0), 4),
            )
            for k in sorted(top_node_ids, key=lambda k: -node_deg[k])
        ]
        edges = [
            CooccurEdge(from_node=k1, to=k2, weight=int(w), lift=float(lift))
            for k1, k2, w, lift, _, _ in all_edges
            if k1 in top_node_ids and k2 in top_node_ids
        ]
        top_pairs = sorted(
            all_edges, key=lambda x: (-x[3], -x[2])
        )[:30]
        top_pairs_out = [
            CooccurPair(
                k1=k1, k2=k2, weight=int(w), lift=float(lift),
                sentiment_skew=round(float(skew), 4),
            )
            for k1, k2, w, lift, skew, _ in top_pairs
        ]

        return KeywordCooccurrenceResponse(
            nodes=nodes,
            edges=edges,
            top_pairs=top_pairs_out,
            meta={
                "anchor_date": str(anchor),
                "period_days": period_days,
                "min_edge_weight": min_edge_weight,
                "top_nodes": top_nodes,
                "total_nodes": len(nodes),
                "total_edges": len(edges),
            },
        )

    # ================================================================
    # 8) anomaly-context
    # ================================================================
    @redis_cache(ttl_seconds=CACHE_TTL, key_prefix="deep:", model_cls=AnomalyContextResponse)
    async def anomaly_context(
        self,
        period_days: int,
        z_threshold: float,
    ) -> AnomalyContextResponse:
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        # 1) spike 탐지 (category 별 daily count 의 z)
        spike_sql = """
            WITH daily AS (
                SELECT published_at::date AS d,
                       UNNEST(categories) AS category,
                       COUNT(*) AS c
                FROM voc_active
                WHERE published_at::date >= :d_from
                  AND published_at::date <= :d_to
                  AND categories IS NOT NULL
                GROUP BY 1, 2
            ),
            stats AS (
                SELECT category, AVG(c)::float AS mu, STDDEV(c)::float AS sd
                FROM daily GROUP BY category
            )
            SELECT d.d, d.category, d.c,
                   CASE WHEN s.sd IS NULL OR s.sd = 0 THEN 0
                        ELSE (d.c - s.mu) / s.sd END AS z
            FROM daily d JOIN stats s USING (category)
            WHERE (s.sd IS NOT NULL AND s.sd > 0
                   AND (d.c - s.mu) / s.sd >= :z_th)
            ORDER BY z DESC
            LIMIT 20
        """
        spike_rows = (
            await self.db.execute(
                text(spike_sql),
                {"d_from": d_from, "d_to": anchor, "z_th": z_threshold},
            )
        ).all()

        spikes: List[SpikeEntry] = []
        for sp in spike_rows:
            d_after_from = sp.d - timedelta(days=1)
            d_before_from = sp.d - timedelta(days=4)
            d_before_to = sp.d - timedelta(days=2)

            # 키워드 delta
            kw_sql = """
                WITH before AS (
                    SELECT vk.keyword, COUNT(*) AS c
                    FROM voc_active vr
                    JOIN voc_keywords vk ON vk.voc_id = vr.id
                    WHERE :cat = ANY(vr.categories)
                      AND vr.published_at::date BETWEEN :b_from AND :b_to
                    GROUP BY vk.keyword
                ),
                after AS (
                    SELECT vk.keyword, COUNT(*) AS c
                    FROM voc_active vr
                    JOIN voc_keywords vk ON vk.voc_id = vr.id
                    WHERE :cat = ANY(vr.categories)
                      AND vr.published_at::date BETWEEN :a_from AND :a_to
                    GROUP BY vk.keyword
                )
                SELECT COALESCE(a.keyword, b.keyword) AS keyword,
                       COALESCE(b.c, 0) AS bc,
                       COALESCE(a.c, 0) AS ac,
                       (COALESCE(a.c, 0) - COALESCE(b.c, 0)) AS delta
                FROM after a FULL OUTER JOIN before b ON a.keyword = b.keyword
                ORDER BY delta DESC NULLS LAST
                LIMIT 10
            """
            kw_rows = (
                await self.db.execute(
                    text(kw_sql),
                    {
                        "cat": sp.category,
                        "b_from": d_before_from,
                        "b_to": d_before_to,
                        "a_from": d_after_from,
                        "a_to": sp.d,
                    },
                )
            ).all()
            kw_delta = [
                KeywordDelta(
                    keyword=r.keyword,
                    before=int(r.bc or 0),
                    after=int(r.ac or 0),
                    delta=int(r.delta or 0),
                )
                for r in kw_rows
                if int(r.delta or 0) > 0
            ]

            # timeline 이벤트 매칭 (spike 일자 ±2 일)
            ev_sql = """
                SELECT title, event_date
                FROM timeline_events
                WHERE event_date BETWEEN :d_lo AND :d_hi
                ORDER BY event_date
            """
            ev_rows = (
                await self.db.execute(
                    text(ev_sql),
                    {
                        "d_lo": sp.d - timedelta(days=2),
                        "d_hi": sp.d + timedelta(days=2),
                    },
                )
            ).all()
            events = [
                MatchedEvent(
                    title=r.title,
                    event_date=str(r.event_date),
                    lag_days=int((sp.d - r.event_date).days),
                )
                for r in ev_rows
            ]

            inferred: Optional[str] = None
            if events:
                inferred = f"event:{events[0].title}"
            elif kw_delta:
                inferred = f"keyword_surge:{kw_delta[0].keyword}"

            spikes.append(
                SpikeEntry(
                    date=str(sp.d),
                    category=sp.category,
                    count=int(sp.c),
                    z=round(float(sp.z or 0), 3),
                    top_keywords_delta=kw_delta[:5],
                    matched_events=events,
                    inferred_cause=inferred,
                )
            )

        return AnomalyContextResponse(
            spikes=spikes,
            meta={
                "anchor_date": str(anchor),
                "period_days": period_days,
                "z_threshold": z_threshold,
                "total_spikes": len(spikes),
            },
        )

    # ================================================================
    # 9) sentiment-driver — P3.7 트랙 B 결합 카드
    # 직전 14일 vs 그 전 14일 negative rate delta 가 큰 *키워드* 추출.
    # ================================================================
    @redis_cache(ttl_seconds=600, key_prefix="deep:", model_cls=SentimentDriverResponse)
    async def sentiment_driver(
        self,
        period_days: int,
        top_n: int,
    ) -> SentimentDriverResponse:
        anchor = await self._anchor_date()
        half = max(period_days // 2, 1)
        after_from = anchor - timedelta(days=half)
        before_from = anchor - timedelta(days=half * 2)
        before_to = after_from - timedelta(days=1)

        sql = """
            WITH base AS (
                SELECT vk.keyword,
                       vk.lang,
                       vr.published_at::date AS d,
                       vr.sentiment_label AS s,
                       vr.categories AS cats
                FROM voc_active vr
                JOIN voc_keywords vk ON vk.voc_id = vr.id
                WHERE vr.published_at::date >= :b_from
                  AND vr.published_at::date <= :a_to
            ),
            before AS (
                SELECT keyword, lang,
                       SUM(CASE WHEN s='negative' THEN 1 ELSE 0 END) AS neg,
                       COUNT(*) AS n
                FROM base
                WHERE d BETWEEN :b_from AND :b_to
                GROUP BY keyword, lang
            ),
            after AS (
                SELECT keyword, lang,
                       SUM(CASE WHEN s='negative' THEN 1 ELSE 0 END) AS neg,
                       COUNT(*) AS n,
                       (
                         SELECT array_agg(c)
                         FROM (
                            SELECT UNNEST(cats) AS c
                            FROM base b2
                            WHERE b2.keyword = base.keyword
                              AND b2.lang IS NOT DISTINCT FROM base.lang
                              AND b2.d BETWEEN :a_from AND :a_to
                              AND b2.cats IS NOT NULL
                            GROUP BY c
                            ORDER BY COUNT(*) DESC
                            LIMIT 3
                         ) t
                       ) AS top_cats
                FROM base
                WHERE d BETWEEN :a_from AND :a_to
                GROUP BY keyword, lang
            )
            SELECT a.keyword,
                   a.lang,
                   COALESCE(b.neg::float / NULLIF(b.n,0), 0) AS before_rate,
                   COALESCE(a.neg::float / NULLIF(a.n,0), 0) AS after_rate,
                   COALESCE(b.n, 0) AS n_before,
                   a.n AS n_after,
                   a.top_cats
            FROM after a
            LEFT JOIN before b
              ON b.keyword = a.keyword
             AND b.lang IS NOT DISTINCT FROM a.lang
            WHERE a.n >= 5
              AND COALESCE(b.n, 0) >= 5
            ORDER BY (
                COALESCE(a.neg::float / NULLIF(a.n,0), 0)
              - COALESCE(b.neg::float / NULLIF(b.n,0), 0)
            ) DESC
            LIMIT :top_n
        """
        rows = (
            await self.db.execute(
                text(sql),
                {
                    "b_from": before_from,
                    "b_to": before_to,
                    "a_from": after_from,
                    "a_to": anchor,
                    "top_n": top_n,
                },
            )
        ).all()

        items: List[SentimentDriverItem] = []
        for r in rows:
            before_rate = float(r.before_rate or 0)
            after_rate = float(r.after_rate or 0)
            delta_pp = round((after_rate - before_rate) * 100.0, 2)
            cats = list(r.top_cats) if r.top_cats else []
            items.append(
                SentimentDriverItem(
                    keyword=r.keyword,
                    lang=r.lang,
                    before_neg_rate=round(before_rate, 4),
                    after_neg_rate=round(after_rate, 4),
                    delta_pp=delta_pp,
                    n_before=int(r.n_before or 0),
                    n_after=int(r.n_after or 0),
                    related_categories=[c for c in cats if c][:3],
                )
            )

        return SentimentDriverResponse(
            items=items,
            meta={
                "anchor_date": str(anchor),
                "period_days": period_days,
                "before_window": f"{before_from}~{before_to}",
                "after_window": f"{after_from}~{anchor}",
                "top_n": top_n,
                "total": len(items),
            },
        )

    # ================================================================
    # 10) anomaly-with-drivers — P3.7 트랙 B 결합 카드
    # anomaly-context 의 spike 결과 + 각 anomaly day 직전 24h 키워드
    # 변화 (top 5).
    # ================================================================
    @redis_cache(ttl_seconds=300, key_prefix="deep:", model_cls=AnomalyWithDriversResponse)
    async def anomaly_with_drivers(
        self,
        period_days: int,
        z_threshold: float,
    ) -> AnomalyWithDriversResponse:
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        spike_sql = """
            WITH daily AS (
                SELECT published_at::date AS d,
                       UNNEST(categories) AS category,
                       COUNT(*) AS c
                FROM voc_active
                WHERE published_at::date >= :d_from
                  AND published_at::date <= :d_to
                  AND categories IS NOT NULL
                GROUP BY 1, 2
            ),
            stats AS (
                SELECT category, AVG(c)::float AS mu, STDDEV(c)::float AS sd
                FROM daily GROUP BY category
            )
            SELECT d.d, d.category, d.c, s.mu,
                   CASE WHEN s.sd IS NULL OR s.sd = 0 THEN 0
                        ELSE (d.c - s.mu) / s.sd END AS z
            FROM daily d JOIN stats s USING (category)
            WHERE (s.sd IS NOT NULL AND s.sd > 0
                   AND (d.c - s.mu) / s.sd >= :z_th)
            ORDER BY z DESC
            LIMIT 15
        """
        spike_rows = (
            await self.db.execute(
                text(spike_sql),
                {"d_from": d_from, "d_to": anchor, "z_th": z_threshold},
            )
        ).all()

        # 키워드 직전 24h vs 그 전 24h
        anomalies: List[AnomalyWithDriversEntry] = []
        for sp in spike_rows:
            day = sp.d
            a_from = day - timedelta(days=1)
            a_to = day
            b_from = day - timedelta(days=2)
            b_to = day - timedelta(days=1)

            kw_sql = """
                WITH before AS (
                    SELECT vk.keyword, COUNT(*) AS c
                    FROM voc_active vr
                    JOIN voc_keywords vk ON vk.voc_id = vr.id
                    WHERE :cat = ANY(vr.categories)
                      AND vr.published_at::date >= :b_from
                      AND vr.published_at::date <  :b_to
                    GROUP BY vk.keyword
                ),
                aft AS (
                    SELECT vk.keyword, COUNT(*) AS c,
                           AVG(CASE WHEN vr.sentiment_label='positive' THEN 1.0
                                    WHEN vr.sentiment_label='negative' THEN -1.0
                                    ELSE 0.0 END) AS sent
                    FROM voc_active vr
                    JOIN voc_keywords vk ON vk.voc_id = vr.id
                    WHERE :cat = ANY(vr.categories)
                      AND vr.published_at::date >= :a_from
                      AND vr.published_at::date <= :a_to
                    GROUP BY vk.keyword
                )
                SELECT a.keyword,
                       COALESCE(b.c, 0) AS bc,
                       a.c AS ac,
                       a.sent AS sent
                FROM aft a
                LEFT JOIN before b ON b.keyword = a.keyword
                WHERE a.c >= 2
                ORDER BY (a.c - COALESCE(b.c,0)) DESC, a.c DESC
                LIMIT 5
            """
            kw_rows = (
                await self.db.execute(
                    text(kw_sql),
                    {
                        "cat": sp.category,
                        "b_from": b_from,
                        "b_to": b_to,
                        "a_from": a_from,
                        "a_to": a_to,
                    },
                )
            ).all()
            drivers = [
                TopDriver(
                    keyword=r.keyword,
                    delta_pct=round(
                        (int(r.ac or 0) - int(r.bc or 0))
                        / max(int(r.bc or 0), 1) * 100.0,
                        2,
                    ),
                    sentiment=round(float(r.sent or 0.0), 4),
                )
                for r in kw_rows
            ]
            anomalies.append(
                AnomalyWithDriversEntry(
                    date=str(day),
                    metric="category_daily_count",
                    category=sp.category,
                    z=round(float(sp.z or 0), 3),
                    baseline=round(float(sp.mu or 0), 2),
                    value=float(sp.c or 0),
                    top_drivers=drivers,
                )
            )

        return AnomalyWithDriversResponse(
            anomalies=anomalies,
            meta={
                "anchor_date": str(anchor),
                "period_days": period_days,
                "z_threshold": z_threshold,
                "total": len(anomalies),
            },
        )

    # ================================================================
    # D1) category-momentum
    # ================================================================
    @redis_cache(ttl_seconds=900, key_prefix="deep:", model_cls=CategoryMomentumResponse)
    async def category_momentum(
        self,
        period_days: int,
        bucket: str,
    ) -> CategoryMomentumResponse:
        """카테고리 12개 × 주별 share(%) + 최근 4주 momentum slope."""
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        trunc = "week" if bucket not in ("day",) else "day"

        sql = f"""
            WITH base AS (
                SELECT DATE_TRUNC('{trunc}', vr.published_at)::date AS wk,
                       UNNEST(vr.categories) AS code
                FROM voc_active vr
                WHERE vr.published_at::date >= :d_from
                  AND vr.published_at::date <= :d_to
                  AND vr.categories IS NOT NULL
            ),
            per AS (
                SELECT wk, code, COUNT(*) AS n FROM base GROUP BY wk, code
            ),
            tot AS (
                SELECT wk, SUM(n) AS t FROM per GROUP BY wk
            )
            SELECT p.wk, p.code, p.n,
                   (p.n::float / NULLIF(t.t,0)) * 100.0 AS share_pct
            FROM per p JOIN tot t USING (wk)
            ORDER BY p.code, p.wk
        """
        rows = (
            await self.db.execute(
                text(sql), {"d_from": d_from, "d_to": anchor}
            )
        ).all()

        cat_rows = (
            await self.db.execute(text("SELECT code, name_ko FROM voc_categories"))
        ).all()
        name_map = {r.code: r.name_ko for r in cat_rows}

        by_code: Dict[str, List[Tuple[Any, int, float]]] = {}
        for r in rows:
            by_code.setdefault(r.code, []).append(
                (r.wk, int(r.n), float(r.share_pct or 0))
            )

        def _slope(ys: List[float]) -> float:
            n = len(ys)
            if n < 2:
                return 0.0
            xs = list(range(n))
            mx = sum(xs) / n
            my = sum(ys) / n
            num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            den = sum((x - mx) ** 2 for x in xs)
            return num / den if den > 0 else 0.0

        categories: List[CategoryMomentumItem] = []
        for code, pts in by_code.items():
            series = [
                MomentumWeekPoint(week=str(wk), share_pct=round(sp, 3), n=n)
                for wk, n, sp in pts
            ]
            recent = [p.share_pct for p in series[-4:]]
            categories.append(
                CategoryMomentumItem(
                    code=code,
                    name_ko=name_map.get(code),
                    series=series,
                    momentum_slope=round(_slope(recent), 4),
                )
            )
        categories.sort(key=lambda c: -c.momentum_slope)

        return CategoryMomentumResponse(
            categories=categories,
            meta={
                "anchor_date": str(anchor),
                "period_days": period_days,
                "bucket": trunc,
                "total_categories": len(categories),
            },
        )

    # ================================================================
    # D2) keyword-network
    # ================================================================
    @redis_cache(ttl_seconds=900, key_prefix="deep:", model_cls=KeywordNetworkResponse)
    async def keyword_network(
        self,
        period_days: int,
        min_cooccur: int,
        max_nodes: int,
    ) -> KeywordNetworkResponse:
        """키워드 동시 출현 네트워크 (union-find community)."""
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        sql = """
            WITH pair AS (
                SELECT a.keyword AS k1, b.keyword AS k2
                FROM voc_keywords a
                JOIN voc_keywords b
                  ON a.voc_id = b.voc_id AND a.keyword < b.keyword
                JOIN voc_records vr ON vr.id = a.voc_id
                WHERE vr.published_at::date >= :d_from
                  AND vr.published_at::date <= :d_to
            ),
            ed AS (
                SELECT k1, k2, COUNT(*) AS w
                FROM pair GROUP BY k1, k2
                HAVING COUNT(*) >= :min_cooccur
            ),
            kw_freq AS (
                SELECT vk.keyword, COUNT(*) AS f,
                       MODE() WITHIN GROUP (ORDER BY vr.language_detected) AS lang
                FROM voc_keywords vk JOIN voc_records vr ON vr.id = vk.voc_id
                WHERE vr.published_at::date >= :d_from
                  AND vr.published_at::date <= :d_to
                GROUP BY vk.keyword
            )
            SELECT e.k1, e.k2, e.w,
                   f1.f AS f1, f1.lang AS l1,
                   f2.f AS f2, f2.lang AS l2
            FROM ed e
            JOIN kw_freq f1 ON f1.keyword = e.k1
            JOIN kw_freq f2 ON f2.keyword = e.k2
            ORDER BY e.w DESC
        """
        rows = (
            await self.db.execute(
                text(sql),
                {"d_from": d_from, "d_to": anchor, "min_cooccur": min_cooccur},
            )
        ).all()

        deg: Dict[str, int] = {}
        meta_map: Dict[str, Tuple[int, Optional[str]]] = {}
        edge_list: List[Tuple[str, str, int]] = []
        for r in rows:
            deg[r.k1] = deg.get(r.k1, 0) + int(r.w)
            deg[r.k2] = deg.get(r.k2, 0) + int(r.w)
            meta_map[r.k1] = (int(r.f1 or 0), r.l1)
            meta_map[r.k2] = (int(r.f2 or 0), r.l2)
            edge_list.append((r.k1, r.k2, int(r.w)))

        top_ids = {k for k, _ in sorted(deg.items(), key=lambda x: -x[1])[:max_nodes]}

        parent: Dict[str, str] = {k: k for k in top_ids}

        def _find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def _union(a: str, b: str) -> None:
            ra, rb = _find(a), _find(b)
            if ra != rb:
                parent[ra] = rb

        edges_out: List[NetworkEdge] = []
        for k1, k2, w in edge_list:
            if k1 in top_ids and k2 in top_ids:
                _union(k1, k2)
                edges_out.append(NetworkEdge(source=k1, target=k2, weight=w))

        root_to_id: Dict[str, int] = {}
        nodes_out: List[NetworkNode] = []
        for k in sorted(top_ids, key=lambda k: -deg[k]):
            root = _find(k)
            cid = root_to_id.setdefault(root, len(root_to_id))
            freq, lang = meta_map.get(k, (0, None))
            nodes_out.append(
                NetworkNode(
                    id=k, keyword=k, lang=lang, freq=freq, community_id=cid
                )
            )

        return KeywordNetworkResponse(
            nodes=nodes_out,
            edges=edges_out,
            meta={
                "anchor_date": str(anchor),
                "period_days": period_days,
                "min_cooccur": min_cooccur,
                "max_nodes": max_nodes,
                "total_nodes": len(nodes_out),
                "total_edges": len(edges_out),
                "total_communities": len(root_to_id),
            },
        )

    # ================================================================
    # D3) lifecycle-funnel
    # ================================================================
    @redis_cache(ttl_seconds=900, key_prefix="deep:", model_cls=LifecycleFunnelResponse)
    async def lifecycle_funnel(
        self,
        period_days: int,
    ) -> LifecycleFunnelResponse:
        """신규 키워드 단계별 잔존 (신규→성장→정체→감소)."""
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        sql = """
            WITH kw_first AS (
                SELECT vk.keyword, MIN(vr.published_at::date) AS first_day
                FROM voc_active vr
                JOIN voc_keywords vk ON vk.voc_id = vr.id
                GROUP BY vk.keyword
                HAVING MIN(vr.published_at::date) >= :d_from
                   AND MIN(vr.published_at::date) <= :d_to
            ),
            daily AS (
                SELECT vk.keyword, vr.published_at::date AS d, COUNT(*) AS c
                FROM voc_keywords vk JOIN voc_records vr ON vr.id = vk.voc_id
                GROUP BY vk.keyword, vr.published_at::date
            ),
            agg AS (
                SELECT k.keyword,
                       k.first_day,
                       MAX(d.d) AS last_day,
                       (MAX(d.d) - k.first_day) AS days_alive,
                       MAX(d.c) AS peak_count,
                       SUM(d.c) AS total
                FROM kw_first k JOIN daily d ON d.keyword = k.keyword
                WHERE d.d BETWEEN k.first_day AND k.first_day + 30
                GROUP BY k.keyword, k.first_day
                HAVING SUM(d.c) >= 3
            )
            SELECT keyword, days_alive, peak_count, total,
                   CASE
                     WHEN days_alive <= 2 THEN '신규'
                     WHEN days_alive BETWEEN 3 AND 7 AND peak_count >= 3 THEN '성장'
                     WHEN days_alive BETWEEN 8 AND 21 THEN '정체'
                     ELSE '감소' END AS stage
            FROM agg
            ORDER BY total DESC
        """
        rows = (
            await self.db.execute(
                text(sql), {"d_from": d_from, "d_to": anchor}
            )
        ).all()

        bucket: Dict[str, List[Any]] = {}
        for r in rows:
            bucket.setdefault(r.stage, []).append(r)

        order = ["신규", "성장", "정체", "감소"]
        stages: List[LifecycleFunnelStage] = []
        for st in order:
            items = bucket.get(st, [])
            examples = [
                LifecycleFunnelExample(
                    keyword=r.keyword,
                    days_alive=int(r.days_alive or 0),
                    peak_count=int(r.peak_count or 0),
                )
                for r in items[:5]
            ]
            stages.append(
                LifecycleFunnelStage(
                    stage=st, n_keywords=len(items), examples=examples
                )
            )

        return LifecycleFunnelResponse(
            stages=stages,
            meta={
                "anchor_date": str(anchor),
                "period_days": period_days,
                "total_new_keywords": sum(s.n_keywords for s in stages),
            },
        )

    # ================================================================
    # D4) influence-rank
    # ================================================================
    @redis_cache(ttl_seconds=900, key_prefix="deep:", model_cls=InfluenceRankResponse)
    async def influence_rank(
        self,
        period_days: int,
        top_n: int,
    ) -> InfluenceRankResponse:
        """사이트 영향력 = engagement × neg_rate × lead_days × reach."""
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        base_sql = """
            SELECT pl.code, pl.name, pl.region,
                   COUNT(*) AS n,
                   AVG(COALESCE(vr.engagement_score,
                                COALESCE(vr.comments_count,0)
                                + COALESCE(vr.likes_count,0)
                                + COALESCE(vr.shares_count,0))) AS eng,
                   SUM(CASE WHEN vr.sentiment_label='negative' THEN 1 ELSE 0 END)::float
                     / NULLIF(COUNT(*),0) AS neg_rate
            FROM voc_active vr
            JOIN platforms pl ON pl.id = vr.platform_id
            WHERE vr.published_at::date >= :d_from
              AND vr.published_at::date <= :d_to
            GROUP BY pl.code, pl.name, pl.region
            HAVING COUNT(*) >= 20
        """
        base_rows = (
            await self.db.execute(
                text(base_sql), {"d_from": d_from, "d_to": anchor}
            )
        ).all()

        # lead_days: 글로벌 first_seen 대비 사이트의 평균 선행 일수
        lead_sql = """
            WITH kw_site AS (
                SELECT vk.keyword, pl.code AS site,
                       MIN(vr.published_at::date) AS fd
                FROM voc_active vr
                JOIN voc_keywords vk ON vk.voc_id = vr.id
                JOIN platforms pl ON pl.id = vr.platform_id
                WHERE vr.published_at::date >= :d_from
                  AND vr.published_at::date <= :d_to
                GROUP BY vk.keyword, pl.code
            ),
            multi AS (
                SELECT keyword FROM kw_site
                GROUP BY keyword HAVING COUNT(DISTINCT site) >= 2
            ),
            global_first AS (
                SELECT k.keyword, MIN(k.fd) AS gfd
                FROM kw_site k JOIN multi m ON m.keyword = k.keyword
                GROUP BY k.keyword
            )
            SELECT k.site,
                   AVG(g.gfd - k.fd)::float AS lead_days
            FROM kw_site k
            JOIN multi m ON m.keyword = k.keyword
            JOIN global_first g ON g.keyword = k.keyword
            GROUP BY k.site
        """
        lead_rows = (
            await self.db.execute(
                text(lead_sql), {"d_from": d_from, "d_to": anchor}
            )
        ).all()
        lead_map = {r.site: float(r.lead_days or 0) for r in lead_rows}

        if not base_rows:
            return InfluenceRankResponse(
                items=[],
                meta={
                    "anchor_date": str(anchor),
                    "period_days": period_days,
                    "total_sites": 0,
                },
            )

        eng_vals = [float(r.eng or 0) for r in base_rows]
        reach_vals = [int(r.n) for r in base_rows]
        max_eng = max(eng_vals) or 1.0
        max_reach = max(reach_vals) or 1.0

        items: List[InfluenceRankItem] = []
        for r in base_rows:
            eng_n = float(r.eng or 0) / max_eng
            reach_n = int(r.n) / max_reach
            neg = float(r.neg_rate or 0)
            lead = lead_map.get(r.code, 0.0)
            # tanh 로 -∞..∞ → -1..1 → 0..1
            lead_norm = (math.tanh(lead / 3.0) + 1.0) / 2.0
            score = round(
                0.3 * eng_n + 0.25 * neg + 0.25 * lead_norm + 0.2 * reach_n, 4
            )
            items.append(
                InfluenceRankItem(
                    platform=r.name or r.code,
                    code=r.code,
                    region=r.region,
                    score=score,
                    drivers=InfluenceDrivers(
                        engagement=round(eng_n, 4),
                        neg_rate=round(neg, 4),
                        lead_days=round(lead, 3),
                        reach=round(reach_n, 4),
                    ),
                )
            )
        items.sort(key=lambda x: -x.score)
        items = items[:top_n]

        return InfluenceRankResponse(
            items=items,
            meta={
                "anchor_date": str(anchor),
                "period_days": period_days,
                "top_n": top_n,
                "total_sites": len(items),
                "weights": {
                    "engagement": 0.30, "neg_rate": 0.25,
                    "lead_days": 0.25, "reach": 0.20,
                },
            },
        )

    # ================================================================
    # D5) product-funnel
    # ================================================================
    @redis_cache(ttl_seconds=900, key_prefix="deep:", model_cls=ProductFunnelResponse)
    async def product_funnel(
        self,
        product: str,
        period_days: int,
    ) -> ProductFunnelResponse:
        """제품의 출시-인지-관심-구매고려-실사용-이탈 단계 추정."""
        prod = product.upper()
        anchor = await self._anchor_date()

        r = await self.db.execute(
            text("""
                SELECT event_date FROM timeline_events
                WHERE event_type='release' AND product_code = :code
                ORDER BY event_date ASC LIMIT 1
            """),
            {"code": prod},
        )
        rel = r.scalar()
        if rel is None:
            r2 = await self.db.execute(
                text("SELECT released_at FROM products WHERE code = :code"),
                {"code": prod},
            )
            rel = r2.scalar()

        if rel is None:
            return ProductFunnelResponse(
                product=prod,
                stages=[],
                meta={"reason": "release_date_not_found", "anchor_date": str(anchor)},
            )

        stage_defs = [
            ("출시", -7, 0),
            ("인지", 1, 14),
            ("관심", 15, 30),
            ("구매고려", 31, 60),
            ("실사용", 61, 120),
            ("이탈", 121, period_days),
        ]

        stages: List[ProductFunnelStage] = []
        for stage, off_lo, off_hi in stage_defs:
            if off_lo > off_hi:
                continue
            d_lo = rel + timedelta(days=off_lo)
            d_hi = rel + timedelta(days=off_hi)
            if d_lo > anchor:
                stages.append(
                    ProductFunnelStage(
                        stage=stage,
                        period=f"{d_lo}~{d_hi}",
                        count=0,
                        sent_avg=0.0,
                        top_keywords=[],
                    )
                )
                continue
            d_hi_eff = min(d_hi, anchor)

            agg = (
                await self.db.execute(
                    text("""
                        SELECT COUNT(*) AS n,
                               AVG(CASE WHEN sentiment_label='positive' THEN 1.0
                                        WHEN sentiment_label='negative' THEN -1.0
                                        ELSE 0.0 END) AS s
                        FROM voc_active vr
                        JOIN products p ON p.id = vr.product_id
                        WHERE p.code = :code
                          AND vr.published_at::date BETWEEN :d_lo AND :d_hi
                    """),
                    {"code": prod, "d_lo": d_lo, "d_hi": d_hi_eff},
                )
            ).first()
            n = int(agg.n or 0) if agg else 0
            s = float(agg.s or 0) if agg else 0.0

            kw_rows = (
                await self.db.execute(
                    text("""
                        SELECT vk.keyword, COUNT(*) AS c
                        FROM voc_active vr
                        JOIN products p ON p.id = vr.product_id
                        JOIN voc_keywords vk ON vk.voc_id = vr.id
                        WHERE p.code = :code
                          AND vr.published_at::date BETWEEN :d_lo AND :d_hi
                        GROUP BY vk.keyword
                        ORDER BY c DESC LIMIT 5
                    """),
                    {"code": prod, "d_lo": d_lo, "d_hi": d_hi_eff},
                )
            ).all()
            top_kw = [r.keyword for r in kw_rows]

            stages.append(
                ProductFunnelStage(
                    stage=stage,
                    period=f"{d_lo}~{d_hi_eff}",
                    count=n,
                    sent_avg=round(s, 4),
                    top_keywords=top_kw,
                )
            )

        return ProductFunnelResponse(
            product=prod,
            stages=stages,
            meta={
                "anchor_date": str(anchor),
                "release_date": str(rel),
                "period_days": period_days,
                "n_stages": len(stages),
            },
        )


    # ================================================================
    # 16) anomaly-drilldown — 트랙 B 확장
    # 특정 anomaly day 의 *시간대 × 제품 × 키워드 × 사이트* 3차원 cross drill-down.
    # 4개 SQL 을 asyncio.gather 로 병렬 실행, 빈 결과는 200 + 빈 배열.
    # ================================================================
    @redis_cache(ttl_seconds=300, key_prefix="deep:", model_cls=AnomalyDrilldownResponse)
    async def anomaly_drilldown(
        self,
        target_date: date,
        z_threshold: float,
        top_k: int,
    ) -> AnomalyDrilldownResponse:
        # 1) anomaly summary: target_date 의 category 별 daily count z 중 최대값.
        #    baseline 윈도우 = target_date 기준 14일 lookback (target_date 제외).
        baseline_from = target_date - timedelta(days=14)
        baseline_to = target_date - timedelta(days=1)

        async def _summary() -> AnomalySummary:
            sql = """
                WITH daily AS (
                    SELECT published_at::date AS d,
                           UNNEST(categories) AS category,
                           COUNT(*) AS c
                    FROM voc_active
                    WHERE published_at::date >= :b_from
                      AND published_at::date <= :d_to
                      AND categories IS NOT NULL
                    GROUP BY 1, 2
                ),
                stats AS (
                    SELECT category,
                           AVG(c) FILTER (WHERE d < :tgt)::float AS mu,
                           STDDEV(c) FILTER (WHERE d < :tgt)::float AS sd
                    FROM daily GROUP BY category
                ),
                today AS (
                    SELECT category, c FROM daily WHERE d = :tgt
                )
                SELECT t.c AS value, s.mu AS baseline,
                       CASE WHEN s.sd IS NULL OR s.sd = 0 THEN 0
                            ELSE (t.c - s.mu) / s.sd END AS z
                FROM today t JOIN stats s USING (category)
                ORDER BY z DESC NULLS LAST
                LIMIT 1
            """
            r = (
                await self.db.execute(
                    text(sql),
                    {"b_from": baseline_from, "d_to": target_date, "tgt": target_date},
                )
            ).first()
            if r is None:
                return AnomalySummary(z=0.0, value=0.0, baseline=0.0)
            return AnomalySummary(
                z=round(float(r.z or 0), 3),
                value=float(r.value or 0),
                baseline=round(float(r.baseline or 0), 2),
            )

        async def _hourly() -> List[DrilldownHourBucket]:
            sql = """
                SELECT EXTRACT(HOUR FROM published_at AT TIME ZONE 'UTC')::int AS h,
                       COUNT(*) AS c,
                       AVG(CASE WHEN sentiment_label='positive' THEN 1.0
                                WHEN sentiment_label='negative' THEN -1.0
                                ELSE 0.0 END) AS sent,
                       SUM(CASE WHEN sentiment_label='negative' THEN 1 ELSE 0 END)::float
                         / NULLIF(COUNT(*),0) AS neg
                FROM voc_active
                WHERE published_at::date = :tgt
                GROUP BY h
            """
            rows = (
                await self.db.execute(text(sql), {"tgt": target_date})
            ).all()
            by_hour = {
                int(r.h): (int(r.c or 0), float(r.sent or 0), float(r.neg or 0))
                for r in rows
            }
            return [
                DrilldownHourBucket(
                    hour=h,
                    count=by_hour.get(h, (0, 0.0, 0.0))[0],
                    sent_avg=round(by_hour.get(h, (0, 0.0, 0.0))[1], 4),
                    neg_rate=round(by_hour.get(h, (0, 0.0, 0.0))[2], 4),
                )
                for h in range(24)
            ]

        async def _products() -> List[DrilldownProduct]:
            sql = """
                SELECT p.code AS code,
                       p.name_ko AS name_ko,
                       COUNT(*) AS c,
                       SUM(CASE WHEN vr.sentiment_label='negative' THEN 1 ELSE 0 END)::float
                         / NULLIF(COUNT(*),0) AS neg
                FROM voc_active vr
                JOIN products p ON p.id = vr.product_id
                WHERE vr.published_at::date = :tgt
                GROUP BY p.code, p.name_ko
                ORDER BY c DESC
                LIMIT 5
            """
            rows = (
                await self.db.execute(text(sql), {"tgt": target_date})
            ).all()
            return [
                DrilldownProduct(
                    code=r.code,
                    name_ko=r.name_ko,
                    count=int(r.c or 0),
                    neg_rate=round(float(r.neg or 0), 4),
                )
                for r in rows
            ]

        async def _keywords() -> List[DrilldownKeyword]:
            # target_date count + 14일 baseline 평균 + 동일 day product top 3.
            sql = """
                WITH today AS (
                    SELECT vk.keyword, vk.lang, COUNT(*) AS c
                    FROM voc_active vr
                    JOIN voc_keywords vk ON vk.voc_id = vr.id
                    WHERE vr.published_at::date = :tgt
                    GROUP BY vk.keyword, vk.lang
                ),
                base AS (
                    SELECT vk.keyword, vk.lang,
                           COUNT(*)::float / 14.0 AS avg_c
                    FROM voc_active vr
                    JOIN voc_keywords vk ON vk.voc_id = vr.id
                    WHERE vr.published_at::date >= :b_from
                      AND vr.published_at::date <= :b_to
                    GROUP BY vk.keyword, vk.lang
                ),
                merged AS (
                    SELECT t.keyword, t.lang, t.c,
                           COALESCE(b.avg_c, 0) AS avg_c
                    FROM today t
                    LEFT JOIN base b
                      ON b.keyword = t.keyword
                     AND b.lang IS NOT DISTINCT FROM t.lang
                )
                SELECT keyword, lang, c, avg_c
                FROM merged
                WHERE c >= 2
                ORDER BY c DESC
                LIMIT :top_k
            """
            rows = (
                await self.db.execute(
                    text(sql),
                    {
                        "tgt": target_date,
                        "b_from": baseline_from,
                        "b_to": baseline_to,
                        "top_k": top_k,
                    },
                )
            ).all()
            if not rows:
                return []

            # 각 키워드의 target_date 내 동시 등장 product top 3 (배치 1 SQL)
            keywords_list = [r.keyword for r in rows]
            rel_sql = """
                SELECT vk.keyword, p.code AS pcode, COUNT(*) AS c
                FROM voc_active vr
                JOIN voc_keywords vk ON vk.voc_id = vr.id
                JOIN products p ON p.id = vr.product_id
                WHERE vr.published_at::date = :tgt
                  AND vk.keyword = ANY(:kws)
                GROUP BY vk.keyword, p.code
            """
            rel_rows = (
                await self.db.execute(
                    text(rel_sql),
                    {"tgt": target_date, "kws": keywords_list},
                )
            ).all()
            rel_map: Dict[str, List[Tuple[str, int]]] = {}
            for rr in rel_rows:
                rel_map.setdefault(rr.keyword, []).append((rr.pcode, int(rr.c or 0)))
            for k in rel_map:
                rel_map[k].sort(key=lambda x: x[1], reverse=True)

            return [
                DrilldownKeyword(
                    keyword=r.keyword,
                    lang=r.lang,
                    count=int(r.c or 0),
                    delta_pct=round(
                        (int(r.c or 0) - float(r.avg_c or 0))
                        / max(float(r.avg_c or 0), 1.0) * 100.0,
                        2,
                    ),
                    related_products=[p for p, _ in rel_map.get(r.keyword, [])[:3]],
                )
                for r in rows
            ]

        async def _platforms() -> List[DrilldownPlatform]:
            sql = """
                SELECT pl.code AS code, pl.name AS name, COUNT(*) AS c
                FROM voc_active vr
                JOIN platforms pl ON pl.id = vr.platform_id
                WHERE vr.published_at::date = :tgt
                GROUP BY pl.code, pl.name
                ORDER BY c DESC
                LIMIT 5
            """
            rows = (
                await self.db.execute(text(sql), {"tgt": target_date})
            ).all()
            return [
                DrilldownPlatform(
                    code=r.code, name=r.name, count=int(r.c or 0)
                )
                for r in rows
            ]

        # AsyncSession 은 단일 connection 에 직렬 실행이어야 하므로 sequentially.
        # (asyncio.gather 사용 시 SQLAlchemy IllegalStateChangeError 발생)
        summary = await _summary()
        hourly = await _hourly()
        products = await _products()
        keywords = await _keywords()
        platforms = await _platforms()

        return AnomalyDrilldownResponse(
            date=str(target_date),
            anomaly_summary=summary,
            hourly=hourly,
            products=products,
            keywords=keywords,
            platforms=platforms,
            meta={
                "z_threshold": z_threshold,
                "top_k": top_k,
                "baseline_window": f"{baseline_from}~{baseline_to}",
                "n_hourly": sum(1 for h in hourly if h.count > 0),
                "n_products": len(products),
                "n_keywords": len(keywords),
                "n_platforms": len(platforms),
            },
        )

    # ── E3) anomaly-drilldown-hour ───────────────────────────────
    @redis_cache(ttl_seconds=300, key_prefix="deep:", model_cls=AnomalyDrilldownHourResponse)
    async def anomaly_drilldown_hour(
        self,
        target_date: date,
        hour: int,
        limit: int,
        offset: int,
    ) -> AnomalyDrilldownHourResponse:
        """해당 1시간 발생한 VoC 본문 리스트.

        정렬: negative 우선 → engagement_score 내림차순.
        """
        # total count
        count_sql = """
            SELECT COUNT(*) AS c
            FROM voc_active vr
            WHERE vr.published_at::date = :tgt
              AND EXTRACT(HOUR FROM vr.published_at AT TIME ZONE 'UTC')::int = :h
        """
        total_row = (
            await self.db.execute(
                text(count_sql), {"tgt": target_date, "h": hour}
            )
        ).first()
        total = int(total_row.c or 0) if total_row else 0

        # paged items with joins
        items_sql = """
            SELECT vr.id AS id,
                   p.code AS pcode, p.name_ko AS pname,
                   pl.code AS plcode, pl.name AS plname,
                   vr.content_original AS content,
                   vr.sentiment_label AS sentiment_label,
                   vr.sentiment_score AS sentiment_score,
                   vr.engagement_score AS engagement_score,
                   vr.source_url AS url,
                   vr.published_at AS published_at
            FROM voc_active vr
            LEFT JOIN products p ON p.id = vr.product_id
            LEFT JOIN platforms pl ON pl.id = vr.platform_id
            WHERE vr.published_at::date = :tgt
              AND EXTRACT(HOUR FROM vr.published_at AT TIME ZONE 'UTC')::int = :h
            ORDER BY
                CASE WHEN vr.sentiment_label='negative' THEN 0
                     WHEN vr.sentiment_label='positive' THEN 2
                     ELSE 1 END ASC,
                COALESCE(vr.engagement_score, 0) DESC,
                vr.id DESC
            LIMIT :lim OFFSET :off
        """
        rows = (
            await self.db.execute(
                text(items_sql),
                {"tgt": target_date, "h": hour, "lim": limit, "off": offset},
            )
        ).all()

        items: List[DrilldownHourVocItem] = []
        for r in rows:
            content = r.content or ""
            preview = content[:200]
            pub_iso: Optional[str] = None
            if r.published_at is not None:
                try:
                    pub_iso = r.published_at.astimezone(timezone.utc).isoformat()
                except Exception:
                    pub_iso = str(r.published_at)
            product_ref = (
                DrilldownHourProductRef(code=r.pcode, name_ko=r.pname)
                if r.pcode else None
            )
            platform_ref = (
                DrilldownHourPlatformRef(code=r.plcode, name=r.plname)
                if r.plcode else None
            )
            items.append(
                DrilldownHourVocItem(
                    id=int(r.id),
                    product=product_ref,
                    platform=platform_ref,
                    content_preview=preview,
                    sentiment_label=r.sentiment_label,
                    sentiment_score=(
                        round(float(r.sentiment_score), 4)
                        if r.sentiment_score is not None else None
                    ),
                    engagement_score=(
                        round(float(r.engagement_score), 4)
                        if r.engagement_score is not None else None
                    ),
                    url=r.url,
                    published_at=pub_iso,
                )
            )

        return AnomalyDrilldownHourResponse(
            date=str(target_date),
            hour=hour,
            total=total,
            items=items,
            meta={
                "limit": limit,
                "offset": offset,
                "returned": len(items),
            },
        )

    # ================================================================
    # UX R2 트랙 A — keyword-detail
    # ================================================================
    @redis_cache(ttl_seconds=300, key_prefix="deep:", model_cls=KeywordDetailResponse)
    async def keyword_detail(
        self,
        keyword: str,
        lang: Optional[str],
        period_days: int,
        limit: int,
    ) -> KeywordDetailResponse:
        """KeywordNetwork node 클릭 시 표시되는 키워드 상세.

        4개 SQL:
          1) stats   : total / sentiment_avg
          2) samples : neg 우선 + 최신순 limit 개
          3) related : 공출현 키워드 top 10 + 카테고리 분포
          4) breakdown : top_products / top_platforms

        참고: AsyncSession 은 단일 connection 직렬 실행이므로 sequential await.
        """
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        params: Dict[str, Any] = {
            "kw": keyword,
            "d_from": d_from,
            "d_to": anchor,
            "lim": int(limit),
        }
        lang_filter = ""
        if lang:
            lang_filter = "AND vr.language_detected = :lang"
            params["lang"] = lang

        # 1) stats — total + sentiment_avg
        stats_sql = f"""
            SELECT COUNT(DISTINCT vr.id) AS n,
                   AVG(vr.sentiment_score) AS s
            FROM voc_active vr
            JOIN voc_keywords vk ON vk.voc_id = vr.id
            WHERE vk.keyword = :kw
              AND vr.published_at::date >= :d_from
              AND vr.published_at::date <= :d_to
              {lang_filter}
        """
        stats_row = (await self.db.execute(text(stats_sql), params)).first()
        total_count = int((stats_row.n if stats_row else 0) or 0)
        sentiment_avg = float(stats_row.s) if (stats_row and stats_row.s is not None) else 0.0

        # 2) samples (neg 우선, 최신순)
        samples_sql = f"""
            SELECT vr.id AS id,
                   vr.content_original AS content,
                   vr.sentiment_label AS sentiment_label,
                   p.code AS pcode,
                   pl.code AS plcode,
                   vr.source_url AS url,
                   vr.published_at AS published_at
            FROM voc_active vr
            JOIN voc_keywords vk ON vk.voc_id = vr.id
            LEFT JOIN products p ON p.id = vr.product_id
            LEFT JOIN platforms pl ON pl.id = vr.platform_id
            WHERE vk.keyword = :kw
              AND vr.published_at::date >= :d_from
              AND vr.published_at::date <= :d_to
              {lang_filter}
            ORDER BY
                CASE WHEN vr.sentiment_label='negative' THEN 0
                     WHEN vr.sentiment_label='positive' THEN 2
                     ELSE 1 END ASC,
                vr.published_at DESC NULLS LAST,
                vr.id DESC
            LIMIT :lim
        """
        sample_rows = (await self.db.execute(text(samples_sql), params)).all()
        samples: List[KeywordDetailSample] = []
        for r in sample_rows:
            content = r.content or ""
            preview = content[:200]
            pub_iso: Optional[str] = None
            if r.published_at is not None:
                try:
                    pub_iso = r.published_at.astimezone(timezone.utc).isoformat()
                except Exception:
                    pub_iso = str(r.published_at)
            samples.append(
                KeywordDetailSample(
                    id=int(r.id),
                    content_preview=preview,
                    sentiment_label=r.sentiment_label,
                    product=r.pcode,
                    platform=r.plcode,
                    url=r.url,
                    published_at=pub_iso,
                )
            )

        # 3a) related keywords (cooccurrence top 10)
        related_sql = f"""
            SELECT vk2.keyword AS kw,
                   MODE() WITHIN GROUP (ORDER BY vr.language_detected) AS lang,
                   COUNT(DISTINCT vr.id) AS c
            FROM voc_active vr
            JOIN voc_keywords vk1 ON vk1.voc_id = vr.id
            JOIN voc_keywords vk2 ON vk2.voc_id = vr.id
            WHERE vk1.keyword = :kw
              AND vk2.keyword <> :kw
              AND vr.published_at::date >= :d_from
              AND vr.published_at::date <= :d_to
              {lang_filter}
            GROUP BY vk2.keyword
            ORDER BY c DESC
            LIMIT 10
        """
        related_rows = (await self.db.execute(text(related_sql), params)).all()
        related_keywords: List[KeywordDetailRelated] = [
            KeywordDetailRelated(
                keyword=str(r.kw),
                lang=r.lang,
                cooccur_count=int(r.c or 0),
            )
            for r in related_rows
        ]

        # 3b) categories breakdown (top 8)
        cat_sql = f"""
            SELECT UNNEST(vr.categories) AS cat, COUNT(*) AS c
            FROM voc_active vr
            JOIN voc_keywords vk ON vk.voc_id = vr.id
            WHERE vk.keyword = :kw
              AND vr.published_at::date >= :d_from
              AND vr.published_at::date <= :d_to
              AND vr.categories IS NOT NULL
              {lang_filter}
            GROUP BY cat
            ORDER BY c DESC
            LIMIT 8
        """
        cat_rows = (await self.db.execute(text(cat_sql), params)).all()
        categories_out: List[KeywordDetailCategory] = [
            KeywordDetailCategory(category=str(r.cat), count=int(r.c or 0))
            for r in cat_rows if r.cat
        ]

        # 4) top products + top platforms
        prod_sql = f"""
            SELECT p.code AS code, p.name_ko AS name_ko, COUNT(*) AS c
            FROM voc_active vr
            JOIN voc_keywords vk ON vk.voc_id = vr.id
            JOIN products p ON p.id = vr.product_id
            WHERE vk.keyword = :kw
              AND vr.published_at::date >= :d_from
              AND vr.published_at::date <= :d_to
              {lang_filter}
            GROUP BY p.code, p.name_ko
            ORDER BY c DESC
            LIMIT 3
        """
        prod_rows = (await self.db.execute(text(prod_sql), params)).all()
        top_products = [
            KeywordDetailProductStat(
                code=str(r.code), name_ko=r.name_ko, count=int(r.c or 0)
            )
            for r in prod_rows
        ]

        plat_sql = f"""
            SELECT pl.code AS code, pl.name AS name, COUNT(*) AS c
            FROM voc_active vr
            JOIN voc_keywords vk ON vk.voc_id = vr.id
            JOIN platforms pl ON pl.id = vr.platform_id
            WHERE vk.keyword = :kw
              AND vr.published_at::date >= :d_from
              AND vr.published_at::date <= :d_to
              {lang_filter}
            GROUP BY pl.code, pl.name
            ORDER BY c DESC
            LIMIT 3
        """
        plat_rows = (await self.db.execute(text(plat_sql), params)).all()
        top_platforms = [
            KeywordDetailPlatformStat(
                code=str(r.code), name=r.name, count=int(r.c or 0)
            )
            for r in plat_rows
        ]

        return KeywordDetailResponse(
            keyword=keyword,
            lang=lang,
            period_days=period_days,
            stats=KeywordDetailStats(
                total_count=total_count,
                sentiment_avg=round(sentiment_avg, 4),
                top_products=top_products,
                top_platforms=top_platforms,
            ),
            samples=samples,
            related_keywords=related_keywords,
            categories=categories_out,
            meta={
                "anchor_date": str(anchor),
                "window": f"{d_from}~{anchor}",
                "limit": int(limit),
                "n_samples": len(samples),
                "n_related": len(related_keywords),
            },
        )


    # ================================================================
    # R9 트랙 A — galaxy-timeline
    # 시리즈별 모든 모델(출시순) 의 출시 +/- 7일 voc 통계 + 출시 후 180일 peak.
    # ================================================================
    @redis_cache(ttl_seconds=900, key_prefix="deep:", model_cls=GalaxyTimelineResponse)
    async def galaxy_timeline(
        self,
        series: str,
        product: Optional[str] = None,
    ) -> GalaxyTimelineResponse:
        series_u = series.upper().strip()
        # 시리즈 필터 — products 의 series_code 단일 매칭
        q_models = """
            SELECT p.id, p.code, p.name_en AS name, p.series_code AS series,
                   p.released_at
            FROM products p
            WHERE p.series_code = :series
              {prod_filter}
              AND p.released_at IS NOT NULL
            ORDER BY p.released_at ASC, p.code ASC
        """
        params: Dict[str, Any] = {"series": series_u}
        prod_filter = ""
        if product:
            prod_filter = "AND p.code = :code"
            params["code"] = product.upper()
        rows = (
            await self.db.execute(text(q_models.format(prod_filter=prod_filter)), params)
        ).all()

        out_models: List[GalaxyTimelineModel] = []
        for r in rows:
            rel: date = r.released_at  # type: ignore[assignment]
            d_lo7 = rel - timedelta(days=7)
            d_hi7 = rel + timedelta(days=7)
            d_hi180 = rel + timedelta(days=180)

            stats_7d = (
                await self.db.execute(
                    text("""
                        SELECT COUNT(*) AS n,
                               AVG(CASE WHEN sentiment_label='positive' THEN 1.0
                                        WHEN sentiment_label='negative' THEN -1.0
                                        ELSE 0.0 END) AS s,
                               SUM(CASE WHEN sentiment_label='negative' THEN 1 ELSE 0 END)::float
                                 / NULLIF(COUNT(*),0) AS neg_rate
                        FROM voc_active
                        WHERE product_id = :pid
                          AND published_at::date BETWEEN :lo AND :hi
                    """),
                    {"pid": r.id, "lo": d_lo7, "hi": d_hi7},
                )
            ).first()

            peak_row = (
                await self.db.execute(
                    text("""
                        SELECT MAX(c) AS peak FROM (
                          SELECT published_at::date AS d, COUNT(*) AS c
                          FROM voc_active
                          WHERE product_id = :pid
                            AND published_at::date BETWEEN :lo AND :hi
                          GROUP BY d
                        ) t
                    """),
                    {"pid": r.id, "lo": rel, "hi": d_hi180},
                )
            ).first()

            total_row = (
                await self.db.execute(
                    text("""
                        SELECT COUNT(*) AS n FROM voc_active
                        WHERE product_id = :pid
                          AND published_at::date BETWEEN :lo AND :hi
                    """),
                    {"pid": r.id, "lo": rel, "hi": d_hi180},
                )
            ).first()

            out_models.append(
                GalaxyTimelineModel(
                    code=str(r.code),
                    name=str(r.name),
                    series=str(r.series),
                    released_at=str(rel),
                    voc_7d_count=int(stats_7d.n or 0) if stats_7d else 0,
                    sent_avg=round(float(stats_7d.s or 0), 4) if stats_7d else 0.0,
                    neg_rate=round(float(stats_7d.neg_rate or 0), 4) if stats_7d else 0.0,
                    peak_count=int((peak_row.peak if peak_row else 0) or 0),
                    total_count=int(total_row.n or 0) if total_row else 0,
                )
            )

        return GalaxyTimelineResponse(
            series=series_u,
            models=out_models,
            meta={
                "n_models": len(out_models),
                "window_7d": "released_at +/- 7days",
                "window_peak": "released_at + 0..180days",
            },
        )

    # ================================================================
    # R9 트랙 A — crisis-cases
    # 사전정의된 위기 사례별 timeline + top keywords + top sites.
    # ================================================================
    @redis_cache(ttl_seconds=900, key_prefix="deep:", model_cls=CrisisCasesResponse)
    async def crisis_cases(self) -> CrisisCasesResponse:
        out: List[CrisisCase] = []
        for spec in CRISIS_CATALOG:
            r = (
                await self.db.execute(
                    text("SELECT id FROM products WHERE code = :code"),
                    {"code": spec["code"]},
                )
            ).first()
            if r is None:
                out.append(
                    CrisisCase(
                        code=spec["code"],
                        title=spec["title"],
                        description=spec["description"],
                        period_start=spec["period_start"],
                        period_end=spec["period_end"],
                        total_voc=0,
                        neg_rate=0.0,
                        timeline=[],
                        top_keywords=[],
                        top_sites=[],
                    )
                )
                continue
            pid = int(r.id)
            p_lo = datetime.strptime(spec["period_start"], "%Y-%m-%d").date()
            p_hi = datetime.strptime(spec["period_end"], "%Y-%m-%d").date()

            agg = (
                await self.db.execute(
                    text("""
                        SELECT COUNT(*) AS n,
                               SUM(CASE WHEN sentiment_label='negative' THEN 1 ELSE 0 END)::float
                                 / NULLIF(COUNT(*),0) AS neg_rate
                        FROM voc_active
                        WHERE product_id = :pid
                          AND published_at::date BETWEEN :lo AND :hi
                    """),
                    {"pid": pid, "lo": p_lo, "hi": p_hi},
                )
            ).first()
            total = int(agg.n or 0) if agg else 0
            neg_rate = round(float(agg.neg_rate or 0), 4) if agg else 0.0

            tl_rows = (
                await self.db.execute(
                    text("""
                        SELECT published_at::date AS d, COUNT(*) AS c
                        FROM voc_active
                        WHERE product_id = :pid
                          AND published_at::date BETWEEN :lo AND :hi
                        GROUP BY d ORDER BY d
                    """),
                    {"pid": pid, "lo": p_lo, "hi": p_hi},
                )
            ).all()
            timeline = [
                CrisisCaseTimelinePoint(day=str(row.d), count=int(row.c))
                for row in tl_rows
            ]

            kw_rows = (
                await self.db.execute(
                    text("""
                        SELECT vk.keyword, COUNT(*) AS c
                        FROM voc_active vr
                        JOIN voc_keywords vk ON vk.voc_id = vr.id
                        WHERE vr.product_id = :pid
                          AND vr.published_at::date BETWEEN :lo AND :hi
                        GROUP BY vk.keyword
                        ORDER BY c DESC LIMIT 10
                    """),
                    {"pid": pid, "lo": p_lo, "hi": p_hi},
                )
            ).all()
            top_keywords = [
                CrisisCaseKeyword(keyword=str(row.keyword), count=int(row.c))
                for row in kw_rows
            ]

            site_rows = (
                await self.db.execute(
                    text("""
                        SELECT pl.code AS site, COUNT(*) AS c
                        FROM voc_active vr
                        JOIN platforms pl ON pl.id = vr.platform_id
                        WHERE vr.product_id = :pid
                          AND vr.published_at::date BETWEEN :lo AND :hi
                        GROUP BY pl.code
                        ORDER BY c DESC LIMIT 10
                    """),
                    {"pid": pid, "lo": p_lo, "hi": p_hi},
                )
            ).all()
            top_sites = [
                CrisisCaseSite(site=str(row.site), count=int(row.c))
                for row in site_rows
            ]

            out.append(
                CrisisCase(
                    code=spec["code"],
                    title=spec["title"],
                    description=spec["description"],
                    period_start=spec["period_start"],
                    period_end=spec["period_end"],
                    total_voc=total,
                    neg_rate=neg_rate,
                    timeline=timeline,
                    top_keywords=top_keywords,
                    top_sites=top_sites,
                )
            )

        return CrisisCasesResponse(
            cases=out,
            meta={"n_cases": len(out)},
        )

    # ================================================================
    # R9 트랙 A — series-comparison
    # 여러 시리즈의 세대 별(출시순) sentiment / count 추이.
    # ================================================================
    @redis_cache(ttl_seconds=900, key_prefix="deep:", model_cls=SeriesComparisonResponse)
    async def series_comparison(
        self,
        series: List[str],
    ) -> SeriesComparisonResponse:
        # series 라벨 (display)
        SERIES_LABEL = {
            "GS": "Galaxy S",
            "GN": "Galaxy Note",
            "GZ": "Galaxy Z (Fold/Flip)",
            "GZF": "Galaxy Z Fold",
            "GZFL": "Galaxy Z Flip",
            "GW": "Galaxy Watch",
            "GB": "Galaxy Buds",
            "GA": "Galaxy A",
            "GM": "Galaxy M",
            "GJ": "Galaxy J",
            "GF": "Galaxy F",
        }
        series_list: List[SeriesComparisonSeries] = []
        for s in series:
            s_u = s.upper().strip()
            rows = (
                await self.db.execute(
                    text("""
                        SELECT p.code, p.name_en AS name, p.released_at,
                               COUNT(v.id) AS n,
                               AVG(CASE WHEN v.sentiment_label='positive' THEN 1.0
                                        WHEN v.sentiment_label='negative' THEN -1.0
                                        ELSE 0.0 END) AS s,
                               SUM(CASE WHEN v.sentiment_label='negative' THEN 1 ELSE 0 END)::float
                                 / NULLIF(COUNT(v.id),0) AS neg_rate
                        FROM products p
                        LEFT JOIN voc_records v ON v.product_id = p.id
                        WHERE p.series_code = :s
                          AND p.released_at IS NOT NULL
                        GROUP BY p.id, p.code, p.name_en, p.released_at
                        ORDER BY p.released_at ASC, p.code ASC
                    """),
                    {"s": s_u},
                )
            ).all()
            points: List[SeriesComparisonGenPoint] = []
            for idx, r in enumerate(rows, start=1):
                points.append(
                    SeriesComparisonGenPoint(
                        gen=idx,
                        code=str(r.code),
                        name=str(r.name),
                        released_at=str(r.released_at) if r.released_at else None,
                        count=int(r.n or 0),
                        sent_avg=round(float(r.s or 0), 4),
                        neg_rate=round(float(r.neg_rate or 0), 4),
                    )
                )
            series_list.append(
                SeriesComparisonSeries(
                    series=s_u,
                    label=SERIES_LABEL.get(s_u, s_u),
                    points=points,
                )
            )
        return SeriesComparisonResponse(
            series_list=series_list,
            meta={
                "n_series": len(series_list),
                "n_models_total": sum(len(s.points) for s in series_list),
            },
        )


__all__ = ["DeepService"]
