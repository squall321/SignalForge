"""
T4 국가 지도 서비스 (P3-2).

데이터 소스:
- country_daily (MV)  : day × country_code × product_id 일별 집계
                        (P3-1 0004_p3_objects.py, 1016행)
- voc_records         : drilldown 시 platform/categories breakdown 용
- platforms / products / voc_categories : code → name 해석용

4 endpoint:
- choropleth     : 전 세계지도 색칠 (n / sent_avg / sent_z / covered)
- drilldown      : 1 국가 → top_sites / top_products / top_categories
- diffusion      : 시간 슬라이더용 일별 frames (제품 1개 한정 권장)
- product-compare: 제품 1개 × 다국가 sent_avg + 95% CI

product_key = COALESCE(product_id, -1) (P3-1 NOTE 참조).
"""
from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import redis_cache
from app.schemas.geo import (
    ChoroplethItem,
    ChoroplethResponse,
    ChoroplethTotals,
    DiffusionFrame,
    DiffusionItem,
    DiffusionResponse,
    DrilldownCategory,
    DrilldownProduct,
    DrilldownResponse,
    DrilldownSite,
    ProductCompareResponse,
    ProductCompareRow,
)


# ── helpers ─────────────────────────────────────────────────────────
def _parse_date(d: Optional[str]) -> Optional[date]:
    if d is None or d == "":
        return None
    return datetime.strptime(d, "%Y-%m-%d").date()


def _default_range(
    date_from: Optional[str], date_to: Optional[str]
) -> tuple[date, date]:
    """기간 누락 시 country_daily 기준 최근 30일 (없으면 오늘 기준)."""
    d_to = _parse_date(date_to) or date.today()
    d_from = _parse_date(date_from) or (d_to - timedelta(days=30))
    if d_from > d_to:
        raise ValueError("date_from > date_to")
    return d_from, d_to


def _z_scores(values: List[float]) -> List[float]:
    """단순 z-score. n<2 또는 std=0 이면 0 으로."""
    if len(values) < 2:
        return [0.0 for _ in values]
    try:
        mu = statistics.mean(values)
        sigma = statistics.pstdev(values)
    except statistics.StatisticsError:
        return [0.0 for _ in values]
    if sigma == 0:
        return [0.0 for _ in values]
    return [round((v - mu) / sigma, 4) for v in values]


def _ci_wald(sent_avg: float, n: int) -> tuple[float, float]:
    """sentiment 평균의 95% Wald CI 근사.

    가정: sentiment_score 의 표준편차를 σ≈0.5 (-1~1 범위에서 보수적 가정) 로,
    sample SE = σ / sqrt(n). n=0 → (avg, avg).
    실 σ 가 필요하면 voc_records 재조회 필요하나 비용이 큼 — country 평균 CI
    는 표시용이므로 보수적 근사로 충분.
    """
    if n <= 0:
        return (sent_avg, sent_avg)
    se = 0.5 / math.sqrt(n)
    return (round(sent_avg - 1.96 * se, 4), round(sent_avg + 1.96 * se, 4))


# ── service ────────────────────────────────────────────────────────
class GeoService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # --------------------------------------------------------------
    # 1) choropleth
    # --------------------------------------------------------------
    @redis_cache(ttl_seconds=300, key_prefix="geo:", model_cls=ChoroplethResponse)
    async def choropleth(
        self,
        product_id: Optional[int],
        date_from: Optional[str],
        date_to: Optional[str],
        metric: str = "n",
    ) -> ChoroplethResponse:
        """전 세계 국가별 집계 — n / sent_avg / sent_z / covered.

        - product_id NULL 이면 모든 product_id 합산.
        - sent_z 는 응답 직전 in-memory 계산 (n>0 국가들만).
        """
        if metric not in {"n", "sent_avg", "sent_z"}:
            raise ValueError(f"unknown metric: {metric}")

        d_from, d_to = _default_range(date_from, date_to)
        params: Dict[str, Any] = {"d_from": d_from, "d_to": d_to}

        where = ["day >= :d_from", "day <= :d_to"]
        if product_id is not None:
            where.append("product_key = :pid")
            params["pid"] = product_id

        sql = f"""
            SELECT
                country_code AS iso2,
                SUM(n)::int  AS n,
                CASE WHEN SUM(n) > 0 THEN
                    (SUM(sent_avg * n) / SUM(n))::float
                ELSE 0 END   AS sent_avg
            FROM country_daily
            WHERE {' AND '.join(where)}
            GROUP BY country_code
            ORDER BY n DESC
        """
        rows = (await self.db.execute(text(sql), params)).all()

        sents = [float(r.sent_avg) for r in rows if int(r.n) > 0]
        zs = _z_scores(sents)
        z_iter = iter(zs)
        items: List[ChoroplethItem] = []
        for r in rows:
            n = int(r.n or 0)
            sent = float(r.sent_avg or 0.0)
            covered = n > 0
            z = next(z_iter) if covered else 0.0
            items.append(
                ChoroplethItem(
                    iso2=r.iso2,
                    n=n,
                    sent_avg=round(sent, 4),
                    sent_z=z,
                    covered=covered,
                )
            )

        # totals
        total_n = sum(it.n for it in items)
        weighted_sent = (
            sum(it.sent_avg * it.n for it in items) / total_n if total_n else 0.0
        )
        totals = ChoroplethTotals(
            countries=sum(1 for it in items if it.covered),
            n=total_n,
            sent_avg=round(weighted_sent, 4),
        )

        return ChoroplethResponse(
            items=items,
            totals=totals,
            meta={
                "product_id": product_id,
                "date_from": str(d_from),
                "date_to": str(d_to),
                "metric": metric,
                "source": "country_daily",
            },
        )

    # --------------------------------------------------------------
    # 2) drilldown
    # --------------------------------------------------------------
    async def drilldown(
        self,
        code: str,
        date_from: Optional[str],
        date_to: Optional[str],
        limit: int = 10,
    ) -> DrilldownResponse:
        """국가 1개 → top_sites / top_products / top_categories (voc_records 직조회).

        country_daily MV 에는 platform/category 차원이 없어 voc_records 사용.
        기간 누락 시 최근 30일.
        """
        d_from, d_to = _default_range(date_from, date_to)
        iso2 = code.upper()
        params: Dict[str, Any] = {
            "iso2": iso2,
            "d_from": d_from,
            "d_to": d_to,
            "d_to_next": d_to + timedelta(days=1),
            "lim": limit,
        }

        # 국가 총합
        total_sql = """
            SELECT SUM(n)::int AS n,
                   CASE WHEN SUM(n) > 0
                        THEN (SUM(sent_avg * n) / SUM(n))::float
                        ELSE 0 END AS sent_avg
            FROM country_daily
            WHERE country_code = :iso2
              AND day >= :d_from
              AND day <= :d_to
        """
        tot = (await self.db.execute(text(total_sql), params)).first()
        total_n = int(tot.n or 0) if tot else 0
        total_sent = float(tot.sent_avg or 0.0) if tot else 0.0

        # top_sites (platform 별)
        sites_sql = """
            SELECT pl.code AS code, pl.name AS name,
                   COUNT(*)::int AS n,
                   AVG(v.sentiment_score)::float AS sent_avg
            FROM voc_active v
            JOIN platforms pl ON pl.id = v.platform_id
            WHERE v.country_code = :iso2
              AND v.collected_at >= :d_from
              AND v.collected_at < :d_to_next
            GROUP BY pl.code, pl.name
            ORDER BY n DESC
            LIMIT :lim
        """
        site_rows = (await self.db.execute(text(sites_sql), params)).all()
        top_sites = [
            DrilldownSite(
                code=r.code,
                name=r.name,
                n=int(r.n),
                sent_avg=round(float(r.sent_avg or 0.0), 4),
            )
            for r in site_rows
        ]

        # top_products
        prod_sql = """
            SELECT p.code AS code, p.name_ko AS name,
                   COUNT(*)::int AS n,
                   AVG(v.sentiment_score)::float AS sent_avg
            FROM voc_active v
            JOIN products p ON p.id = v.product_id
            WHERE v.country_code = :iso2
              AND v.collected_at >= :d_from
              AND v.collected_at < :d_to_next
            GROUP BY p.code, p.name_ko
            ORDER BY n DESC
            LIMIT :lim
        """
        prod_rows = (await self.db.execute(text(prod_sql), params)).all()
        top_products = [
            DrilldownProduct(
                code=r.code,
                name=r.name,
                n=int(r.n),
                sent_avg=round(float(r.sent_avg or 0.0), 4),
            )
            for r in prod_rows
        ]

        # top_categories (categories[] unnest)
        cat_sql = """
            SELECT cat AS category,
                   COUNT(*)::int AS n,
                   AVG(v.sentiment_score)::float AS sent_avg
            FROM voc_active v, unnest(v.categories) AS cat
            WHERE v.country_code = :iso2
              AND v.collected_at >= :d_from
              AND v.collected_at < :d_to_next
              AND v.categories IS NOT NULL
              AND array_length(v.categories, 1) > 0
            GROUP BY cat
            ORDER BY n DESC
            LIMIT :lim
        """
        cat_rows = (await self.db.execute(text(cat_sql), params)).all()
        # category 코드 → name_ko 룩업
        cat_codes = [r.category for r in cat_rows]
        name_map: Dict[str, str] = {}
        if cat_codes:
            map_sql = """
                SELECT code, name_ko
                FROM voc_categories
                WHERE code = ANY(:codes)
            """
            map_rows = (await self.db.execute(
                text(map_sql), {"codes": cat_codes}
            )).all()
            name_map = {r.code: r.name_ko for r in map_rows}
        top_categories = [
            DrilldownCategory(
                category=r.category,
                name=name_map.get(r.category),
                n=int(r.n),
                sent_avg=round(float(r.sent_avg or 0.0), 4),
            )
            for r in cat_rows
        ]

        return DrilldownResponse(
            iso2=iso2,
            n=total_n,
            sent_avg=round(total_sent, 4),
            top_sites=top_sites,
            top_products=top_products,
            top_categories=top_categories,
        )

    # --------------------------------------------------------------
    # 3) diffusion
    # --------------------------------------------------------------
    async def diffusion(
        self,
        product_id: Optional[int],
        date_from: Optional[str],
        date_to: Optional[str],
        granularity: str = "day",
    ) -> DiffusionResponse:
        """시간 슬라이더용 일별 frames (frame = day, items = [iso2,n]).

        granularity: day | week (week → date_trunc('week')).
        product_id NULL 이면 전 제품 합산.
        """
        if granularity not in {"day", "week"}:
            raise ValueError(f"unknown granularity: {granularity}")

        d_from, d_to = _default_range(date_from, date_to)
        params: Dict[str, Any] = {"d_from": d_from, "d_to": d_to}
        where = ["day >= :d_from", "day <= :d_to"]
        if product_id is not None:
            where.append("product_key = :pid")
            params["pid"] = product_id

        sql = f"""
            SELECT
                date_trunc('{granularity}', day)::date AS bucket_day,
                country_code AS iso2,
                SUM(n)::int  AS n
            FROM country_daily
            WHERE {' AND '.join(where)}
            GROUP BY 1, 2
            ORDER BY 1, n DESC
        """
        rows = (await self.db.execute(text(sql), params)).all()

        # bucket_day → [items]
        by_day: Dict[str, List[DiffusionItem]] = {}
        for r in rows:
            key = str(r.bucket_day)
            by_day.setdefault(key, []).append(
                DiffusionItem(iso2=r.iso2, n=int(r.n))
            )

        frames = [DiffusionFrame(day=k, items=v) for k, v in by_day.items()]
        # 시간순 정렬 (dict 가 3.7+ 에선 보존되지만 안전상 재정렬)
        frames.sort(key=lambda f: f.day)

        return DiffusionResponse(
            frames=frames,
            meta={
                "product_id": product_id,
                "date_from": str(d_from),
                "date_to": str(d_to),
                "granularity": granularity,
                "source": "country_daily",
            },
        )

    # --------------------------------------------------------------
    # 4) product-compare
    # --------------------------------------------------------------
    async def product_compare(
        self,
        product_id: int,
        countries: List[str],
        date_from: Optional[str],
        date_to: Optional[str],
    ) -> ProductCompareResponse:
        """제품 1개 × 다국가 sent_avg + 95% CI."""
        if product_id is None:
            raise ValueError("product_id required")
        if not countries:
            raise ValueError("countries required (>=1)")

        iso2_list = [c.upper() for c in countries]
        d_from, d_to = _default_range(date_from, date_to)
        params: Dict[str, Any] = {
            "pid": product_id,
            "iso2_list": iso2_list,
            "d_from": d_from,
            "d_to": d_to,
        }

        sql = """
            SELECT
                country_code AS iso2,
                SUM(n)::int  AS n,
                CASE WHEN SUM(n) > 0
                     THEN (SUM(sent_avg * n) / SUM(n))::float
                     ELSE 0 END AS sent_avg
            FROM country_daily
            WHERE product_key = :pid
              AND country_code = ANY(:iso2_list)
              AND day >= :d_from
              AND day <= :d_to
            GROUP BY country_code
        """
        rows = (await self.db.execute(text(sql), params)).all()
        by_iso2 = {r.iso2: r for r in rows}

        result_rows: List[ProductCompareRow] = []
        for iso2 in iso2_list:
            r = by_iso2.get(iso2)
            if r is None:
                # 데이터 없는 국가도 행 유지 (n=0)
                result_rows.append(
                    ProductCompareRow(
                        country=iso2, n=0, sent_avg=0.0, ci_lo=0.0, ci_hi=0.0
                    )
                )
                continue
            n = int(r.n)
            sent = round(float(r.sent_avg or 0.0), 4)
            lo, hi = _ci_wald(sent, n)
            result_rows.append(
                ProductCompareRow(
                    country=iso2, n=n, sent_avg=sent, ci_lo=lo, ci_hi=hi
                )
            )

        return ProductCompareResponse(
            rows=result_rows,
            meta={
                "product_id": product_id,
                "countries": iso2_list,
                "date_from": str(d_from),
                "date_to": str(d_to),
                "source": "country_daily",
            },
        )


__all__ = ["GeoService", "_z_scores", "_ci_wald", "_default_range"]
