"""
Dashboard overview 집계 서비스 (P1-2 MVP).

GET /api/v1/dashboard/overview 의 단일 엔드포인트가 필요로 하는
KPI / 14일 트렌드 / Top 사이트 5건 을 한 번에 계산해 반환한다.

- period: 7d | 30d | 90d (default 30d)
- product / country / platform 은 선택적 단일값 필터
- trend14d 는 period 와 무관하게 항상 직전 14일 일별 윈도우
- alert_count: 동일 필터 안에서 제품별 negative 비율 > 50% 이면서
  최소 10건 이상 수집된 제품 수.
"""
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import redis_cache
from app.schemas.dashboard import (
    DashboardOverviewResponse,
    DashboardKPIs,
    TrendPoint,
    TopSiteItem,
)


_PERIOD_DAYS = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}


class DashboardService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── helper ────────────────────────────────────────────

    @staticmethod
    def _period_days(period: str) -> int:
        return _PERIOD_DAYS.get(period, 30)

    @staticmethod
    def _is_mv_eligible(
        period: str,
        product: Optional[str],
        country: Optional[str],
        platform: Optional[str],
    ) -> bool:
        """kpi_overview MV 활용 가능 case.

        MV 는 24h 무필터 KPI 집계만 보관하므로, period='24h' + filter 없음
        조건일 때만 KPI 부분을 MV 로 대체할 수 있다.
        """
        return period == "24h" and not product and not country and not platform

    @staticmethod
    def _build_filter_sql(
        product: Optional[str],
        country: Optional[str],
        platform: Optional[str],
    ) -> str:
        """선택적 필터 WHERE 절 조각.
        - product / platform 은 코드로 받아 join 한 결과를 사용.
        - country 는 voc_records.country_code 와 직접 비교.
        """
        parts: List[str] = []
        if product:
            parts.append("AND p.code = :product")
        if country:
            parts.append("AND v.country_code = :country")
        if platform:
            parts.append("AND pf.code = :platform")
        return "\n              ".join(parts)

    # ── overview ──────────────────────────────────────────

    @redis_cache(ttl_seconds=120, key_prefix="dashboard:", model_cls=DashboardOverviewResponse)
    async def get_overview(
        self,
        period: str = "30d",
        product: Optional[str] = None,
        country: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> DashboardOverviewResponse:
        days = self._period_days(period)
        since = datetime.utcnow() - timedelta(days=days)
        since_14 = datetime.utcnow() - timedelta(days=14)

        params: Dict[str, Any] = {"since": since, "since_14": since_14}
        if product:
            params["product"] = product.upper()
        if country:
            params["country"] = country.upper()
        if platform:
            params["platform"] = platform

        filter_sql = self._build_filter_sql(product, country, platform)

        # 1) KPIs --------------------------------------------------
        # R18 트랙 B: 24h + 무필터 case 는 kpi_overview MV 활용 (< 1ms).
        # alert_count 만 별도 집계 (MV 미포함). 다른 case 는 기존 SQL.
        if self._is_mv_eligible(period, product, country, platform):
            mv_stmt = text("""
                SELECT
                    voc_24h,
                    COALESCE(ROUND((neg_rate_24h * 100)::numeric, 1), 0) AS neg_rate,
                    top_product_24h
                FROM kpi_overview
                WHERE id = 1
            """)
            mv_row = (await self.db.execute(mv_stmt)).one_or_none()

            # alert_count 는 24h per-product breakdown 필요 → 별도 1쿼리
            alert_stmt = text("""
                SELECT COUNT(*) AS alert_count FROM (
                    SELECT p.code,
                           COUNT(*) AS cnt,
                           SUM(CASE WHEN v.sentiment_label='negative' THEN 1 ELSE 0 END)::numeric
                             / NULLIF(COUNT(*),0) * 100 AS neg_rate
                    FROM voc_active v JOIN products p ON p.id=v.product_id
                    WHERE v.collected_at >= now() - interval '24 hours'
                      AND v.archived_at IS NULL
                    GROUP BY p.code
                ) q
                WHERE q.cnt >= 10 AND q.neg_rate > 50
            """)
            alert_row = (await self.db.execute(alert_stmt)).one()

            if mv_row is not None:
                kpis = DashboardKPIs(
                    total_voc=int(mv_row.voc_24h or 0),
                    neg_rate=float(mv_row.neg_rate or 0),
                    top_product=mv_row.top_product_24h,
                    alert_count=int(alert_row.alert_count or 0),
                )
            else:
                # MV 비어있음 (REFRESH 전) → fallback to raw 0건
                kpis = DashboardKPIs(
                    total_voc=0, neg_rate=0.0, top_product=None,
                    alert_count=int(alert_row.alert_count or 0),
                )
        else:
            kpi_stmt = text(f"""
                WITH base AS (
                    SELECT v.*, p.code AS p_code, pf.code AS pf_code
                    FROM voc_active v
                    LEFT JOIN products  p  ON p.id  = v.product_id
                    LEFT JOIN platforms pf ON pf.id = v.platform_id
                    WHERE v.collected_at >= :since
                      AND v.archived_at IS NULL
                      {filter_sql}
                ),
                top_p AS (
                    SELECT p_code, COUNT(*) AS cnt
                    FROM base
                    WHERE p_code IS NOT NULL
                    GROUP BY p_code
                    ORDER BY cnt DESC
                    LIMIT 1
                ),
                per_product AS (
                    SELECT
                        p_code,
                        COUNT(*) AS cnt,
                        SUM(CASE WHEN sentiment_label = 'negative' THEN 1 ELSE 0 END)::numeric
                            / NULLIF(COUNT(*), 0) * 100 AS neg_rate
                    FROM base
                    WHERE p_code IS NOT NULL
                    GROUP BY p_code
                )
                SELECT
                    (SELECT COUNT(*) FROM base)                                       AS total_voc,
                    COALESCE(
                        ROUND(
                            (SELECT SUM(CASE WHEN sentiment_label = 'negative' THEN 1 ELSE 0 END)::numeric
                                    / NULLIF(COUNT(*), 0) * 100
                             FROM base)
                        , 1),
                        0
                    )                                                                 AS neg_rate,
                    (SELECT p_code FROM top_p)                                        AS top_product,
                    (SELECT COUNT(*) FROM per_product
                      WHERE cnt >= 10 AND neg_rate > 50)                              AS alert_count
            """)
            kpi_row = (await self.db.execute(kpi_stmt, params)).one()

            kpis = DashboardKPIs(
                total_voc=int(kpi_row.total_voc or 0),
                neg_rate=float(kpi_row.neg_rate or 0),
                top_product=kpi_row.top_product,
                alert_count=int(kpi_row.alert_count or 0),
            )

        # 2) trend14d ---------------------------------------------
        trend_stmt = text(f"""
            SELECT
                date_trunc('day', v.collected_at)::date AS d,
                COUNT(*)                                AS cnt,
                ROUND(AVG(v.sentiment_score)::numeric, 3) AS sent_avg
            FROM voc_active v
            LEFT JOIN products  p  ON p.id  = v.product_id
            LEFT JOIN platforms pf ON pf.id = v.platform_id
            WHERE v.collected_at >= :since_14
              AND v.archived_at IS NULL
              {filter_sql}
            GROUP BY d
            ORDER BY d
        """)
        trend_rows = (await self.db.execute(trend_stmt, params)).all()
        trend14d = [
            TrendPoint(
                date=str(r.d),
                count=int(r.cnt or 0),
                sent_avg=float(r.sent_avg or 0),
            )
            for r in trend_rows
        ]

        # 3) top_sites (period 기준, 상위 5) -----------------------
        sites_stmt = text(f"""
            SELECT
                pf.code                                AS code,
                COUNT(*)                               AS cnt,
                ROUND(AVG(v.sentiment_score)::numeric, 3) AS sent_avg
            FROM voc_active v
            LEFT JOIN products  p  ON p.id  = v.product_id
            JOIN      platforms pf ON pf.id = v.platform_id
            WHERE v.collected_at >= :since
              AND v.archived_at IS NULL
              {filter_sql}
            GROUP BY pf.code
            ORDER BY cnt DESC
            LIMIT 5
        """)
        site_rows = (await self.db.execute(sites_stmt, params)).all()
        top_sites = [
            TopSiteItem(
                code=r.code,
                count=int(r.cnt or 0),
                sent_avg=float(r.sent_avg or 0),
            )
            for r in site_rows
        ]

        return DashboardOverviewResponse(
            period=period,
            filters={
                "product": product,
                "country": country,
                "platform": platform,
            },
            kpis=kpis,
            trend14d=trend14d,
            top_sites=top_sites,
        )
