"""
T4 딥 인사이트 서비스 (P4 트랙 C) — 7 endpoint.

데이터 소스:
- voc_records.published_at  : 시(hour)/요일(dow) 패턴
- voc_records.sentiment_*   : sentiment 평균/부정율
- voc_records.engagement_*  : 영향력 점수
- voc_keywords + voc_records: 키워드 emerging / new-terms
- timeline_events           : product 출시일 (lifecycle anchor)
- platforms                 : code/region (influence drivers)

주의:
- voc_keywords.extracted_at 은 단일 스냅샷일 수 있으므로 키워드 시간축은
  반드시 join 한 voc_records.published_at 을 사용한다.
- voc_records.published_at 은 future date (2026-12 등) 도 일부 존재하므로
  emerging/new-terms 는 "현재(NOW) 기준" 이 아니라 "데이터 max(published_at) 기준" anchor 로
  계산해 의미있는 비교를 유지한다.
"""
from __future__ import annotations

import logging
import os
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import redis_cache
from app.schemas.insights import (
    CompareLLMResponse,
    EmergingKeywordsResponse,
    HourlyPatternResponse,
    HourlyPoint,
    InfluenceDrivers,
    KeywordTrend,
    LifecyclePoint,
    NewTermEntry,
    NewTermsResponse,
    PlatformInfluenceEntry,
    PlatformInfluenceResponse,
    ProductLifecycleResponse,
    SentimentSwingEntry,
    SentimentSwingResponse,
    WeekdayPatternResponse,
    WeekdayPoint,
)

logger = logging.getLogger(__name__)


# crawler 패키지 import 보장 (compare_insight, llm_provider 재사용).
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
_CRAWLER_ROOT = os.path.join(_REPO_ROOT, "crawler")
if _CRAWLER_ROOT not in sys.path:
    sys.path.insert(0, _CRAWLER_ROOT)


WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _normalize_scores(values: List[float]) -> List[float]:
    """0~100 으로 min-max 정규화. 동일값이면 모두 50.0."""
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if hi - lo < 1e-9:
        return [50.0 for _ in values]
    return [round((v - lo) / (hi - lo) * 100.0, 2) for v in values]


class InsightsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ----------------------------------------------------------------
    # anchor: 데이터의 published_at max 를 "현재" 로 간주
    # ----------------------------------------------------------------
    async def _anchor_date(self) -> date:
        r = await self.db.execute(
            text("SELECT MAX(published_at::date) AS d FROM voc_active "
                 "WHERE published_at <= NOW() + INTERVAL '1 day' "
                 "AND archived_at IS NULL")
        )
        d = r.scalar()
        if d is None:
            return datetime.now(timezone.utc).date()
        return d  # type: ignore[return-value]

    # ================================================================
    # 1) hourly-pattern
    # ================================================================
    async def hourly_pattern(
        self,
        product: Optional[str],
        period_days: int,
    ) -> HourlyPatternResponse:
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        params: Dict[str, Any] = {"d_from": d_from, "d_to": anchor}
        join = ""
        where = ["v.published_at::date >= :d_from",
                 "v.published_at::date <= :d_to",
                 "v.archived_at IS NULL"]
        if product:
            params["product"] = product.upper()
            join = "JOIN products p ON p.id = v.product_id"
            where.append("p.code = :product")

        sql = f"""
            SELECT
                EXTRACT(HOUR FROM v.published_at)::int AS h,
                COUNT(*)                               AS c,
                AVG(v.sentiment_score)                 AS s
            FROM voc_active v
            {join}
            WHERE {' AND '.join(where)}
            GROUP BY h
            ORDER BY h
        """
        rows = (await self.db.execute(text(sql), params)).all()
        by_hour = {int(r.h): (int(r.c), float(r.s or 0)) for r in rows}

        points = [
            HourlyPoint(
                hour=h,
                count=by_hour.get(h, (0, 0.0))[0],
                sent_avg=round(by_hour.get(h, (0, 0.0))[1], 4),
            )
            for h in range(24)
        ]
        total = sum(p.count for p in points)
        peak = max(points, key=lambda p: p.count) if points else None
        return HourlyPatternResponse(
            points=points,
            meta={
                "product": product,
                "period_days": period_days,
                "anchor_date": str(anchor),
                "total": total,
                "peak_hour": peak.hour if peak and peak.count > 0 else None,
            },
        )

    # ================================================================
    # 2) weekday-pattern
    # ================================================================
    async def weekday_pattern(
        self,
        product: Optional[str],
        period_days: int,
    ) -> WeekdayPatternResponse:
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        params: Dict[str, Any] = {"d_from": d_from, "d_to": anchor}
        join = ""
        where = ["v.published_at::date >= :d_from",
                 "v.published_at::date <= :d_to",
                 "v.archived_at IS NULL"]
        if product:
            params["product"] = product.upper()
            join = "JOIN products p ON p.id = v.product_id"
            where.append("p.code = :product")

        # postgres EXTRACT(ISODOW) → 1=Mon..7=Sun, 우리는 0=Mon..6=Sun
        sql = f"""
            SELECT
                (EXTRACT(ISODOW FROM v.published_at)::int - 1) AS wd,
                COUNT(*)                                       AS c,
                AVG(v.sentiment_score)                         AS s,
                SUM(CASE WHEN v.sentiment_label='negative' THEN 1 ELSE 0 END) AS neg
            FROM voc_active v
            {join}
            WHERE {' AND '.join(where)}
            GROUP BY wd
            ORDER BY wd
        """
        rows = (await self.db.execute(text(sql), params)).all()
        by_wd = {
            int(r.wd): (int(r.c), float(r.s or 0), int(r.neg or 0))
            for r in rows
        }

        points: List[WeekdayPoint] = []
        for wd in range(7):
            c, s, neg = by_wd.get(wd, (0, 0.0, 0))
            neg_rate = round((neg / c) * 100, 2) if c else 0.0
            points.append(
                WeekdayPoint(
                    weekday=wd,
                    label=WEEKDAY_LABELS[wd],
                    count=c,
                    sent_avg=round(s, 4),
                    neg_rate=neg_rate,
                )
            )
        return WeekdayPatternResponse(
            points=points,
            meta={
                "product": product,
                "period_days": period_days,
                "anchor_date": str(anchor),
                "total": sum(p.count for p in points),
            },
        )

    # ================================================================
    # 3) emerging-keywords
    # ================================================================
    async def emerging_keywords(
        self,
        period_days: int,
        top_n: int,
    ) -> EmergingKeywordsResponse:
        """직전 period_days vs 그 전 period_days 의 keyword count 비교.

        voc_keywords 의 extracted_at 가 단일 시점인 경우가 있어
        voc_records.published_at 을 기준으로 윈도우를 자른다.
        """
        anchor = await self._anchor_date()
        this_from = anchor - timedelta(days=period_days)
        prev_from = anchor - timedelta(days=2 * period_days)

        sql = """
            WITH base AS (
                SELECT
                    k.keyword,
                    k.lang,
                    CASE
                      WHEN v.published_at::date > :this_from THEN 'this'
                      ELSE 'prev'
                    END AS bucket
                FROM voc_keywords k
                JOIN voc_records v ON v.id = k.voc_id
                WHERE v.published_at::date > :prev_from
                  AND v.published_at::date <= :anchor
            )
            SELECT keyword, lang,
                SUM(CASE WHEN bucket='this' THEN 1 ELSE 0 END) AS this_c,
                SUM(CASE WHEN bucket='prev' THEN 1 ELSE 0 END) AS prev_c
            FROM base
            GROUP BY keyword, lang
            HAVING SUM(CASE WHEN bucket='this' THEN 1 ELSE 0 END)
                 + SUM(CASE WHEN bucket='prev' THEN 1 ELSE 0 END) >= 3
        """
        rows = (
            await self.db.execute(
                text(sql),
                {"prev_from": prev_from, "this_from": this_from, "anchor": anchor},
            )
        ).all()

        trends: List[KeywordTrend] = []
        for r in rows:
            this_c = int(r.this_c)
            prev_c = int(r.prev_c)
            growth = ((this_c - prev_c) / max(prev_c, 1)) * 100.0
            trends.append(
                KeywordTrend(
                    keyword=r.keyword,
                    lang=r.lang,
                    prev_week_count=prev_c,
                    this_week_count=this_c,
                    growth_pct=round(growth, 2),
                )
            )

        # emerging: 증가율 큰 순 (this_c >= prev_c+2)
        emerging = sorted(
            [t for t in trends if t.this_week_count >= t.prev_week_count + 2],
            key=lambda t: (t.growth_pct, t.this_week_count),
            reverse=True,
        )[:top_n]

        # declining: 감소율 큰 순 (prev_c >= this_c+2)
        declining = sorted(
            [t for t in trends if t.prev_week_count >= t.this_week_count + 2],
            key=lambda t: (t.growth_pct, -t.prev_week_count),
        )[:top_n]

        return EmergingKeywordsResponse(
            emerging=emerging,
            declining=declining,
            meta={
                "period_days": period_days,
                "top_n": top_n,
                "anchor_date": str(anchor),
                "this_from": str(this_from),
                "prev_from": str(prev_from),
                "total_candidates": len(trends),
            },
        )

    # ================================================================
    # 4) new-terms
    # ================================================================
    async def new_terms(
        self,
        period_days: int,
    ) -> NewTermsResponse:
        """최근 period_days 에 처음 등장한 키워드.

        "처음 등장" 정의:
        - first_seen = MIN(voc_records.published_at::date)
        - first_seen > (anchor - period_days)
        - 이전 90일(또는 가용한 만큼) 윈도우에는 등장 0건이어야 함
        """
        anchor = await self._anchor_date()
        recent_from = anchor - timedelta(days=period_days)

        sql = """
            SELECT k.keyword, k.lang,
                   MIN(v.published_at::date)                AS first_seen,
                   COUNT(*)                                 AS recent_c
            FROM voc_keywords k
            JOIN voc_records v ON v.id = k.voc_id
            WHERE v.published_at::date > :recent_from
              AND v.published_at::date <= :anchor
              AND NOT EXISTS (
                  SELECT 1 FROM voc_keywords k2
                  JOIN voc_records v2 ON v2.id = k2.voc_id
                  WHERE k2.keyword = k.keyword
                    AND COALESCE(k2.lang,'') = COALESCE(k.lang,'')
                    AND v2.published_at::date <= :recent_from
                    AND v2.published_at::date > :history_from
              )
            GROUP BY k.keyword, k.lang
            HAVING COUNT(*) >= 2
            ORDER BY COUNT(*) DESC, MIN(v.published_at::date) DESC
            LIMIT 50
        """
        rows = (
            await self.db.execute(
                text(sql),
                {
                    "recent_from": recent_from,
                    "anchor": anchor,
                    "history_from": anchor - timedelta(days=period_days + 90),
                },
            )
        ).all()

        items = [
            NewTermEntry(
                keyword=r.keyword,
                lang=r.lang,
                first_seen=str(r.first_seen),
                count_recent=int(r.recent_c),
            )
            for r in rows
        ]
        return NewTermsResponse(
            items=items,
            meta={
                "period_days": period_days,
                "anchor_date": str(anchor),
                "history_window_days": 90,
                "total": len(items),
            },
        )

    # ================================================================
    # 5) sentiment-swing
    # ================================================================
    async def sentiment_swing(
        self,
        period_days: int,
        min_volume: int,
    ) -> SentimentSwingResponse:
        """직전 period_days vs 그 전 period_days 의 product 별 sentiment delta."""
        anchor = await self._anchor_date()
        this_from = anchor - timedelta(days=period_days)
        prev_from = anchor - timedelta(days=2 * period_days)

        sql = """
            SELECT
                p.code AS product,
                AVG(CASE WHEN v.published_at::date > :this_from
                         THEN v.sentiment_score END) AS s_after,
                AVG(CASE WHEN v.published_at::date <= :this_from
                              AND v.published_at::date > :prev_from
                         THEN v.sentiment_score END) AS s_before,
                SUM(CASE WHEN v.published_at::date > :this_from
                         THEN 1 ELSE 0 END) AS n_after,
                SUM(CASE WHEN v.published_at::date <= :this_from
                              AND v.published_at::date > :prev_from
                         THEN 1 ELSE 0 END) AS n_before
            FROM voc_active v
            JOIN products p ON p.id = v.product_id
            WHERE v.published_at::date > :prev_from
              AND v.published_at::date <= :anchor
            GROUP BY p.code
            HAVING SUM(CASE WHEN v.published_at::date > :this_from THEN 1 ELSE 0 END) >= :min_v
               AND SUM(CASE WHEN v.published_at::date <= :this_from
                                  AND v.published_at::date > :prev_from
                             THEN 1 ELSE 0 END) >= :min_v
        """
        rows = (
            await self.db.execute(
                text(sql),
                {
                    "this_from": this_from,
                    "prev_from": prev_from,
                    "anchor": anchor,
                    "min_v": min_volume,
                },
            )
        ).all()

        items: List[SentimentSwingEntry] = []
        for r in rows:
            before = float(r.s_before or 0)
            after = float(r.s_after or 0)
            items.append(
                SentimentSwingEntry(
                    product=r.product,
                    before_sent=round(before, 4),
                    after_sent=round(after, 4),
                    delta_pp=round(after - before, 4),
                    n_before=int(r.n_before or 0),
                    n_after=int(r.n_after or 0),
                )
            )
        # delta 절대값 큰 순으로 정렬
        items.sort(key=lambda x: abs(x.delta_pp), reverse=True)

        return SentimentSwingResponse(
            items=items,
            meta={
                "period_days": period_days,
                "min_volume": min_volume,
                "anchor_date": str(anchor),
                "this_from": str(this_from),
                "prev_from": str(prev_from),
                "total": len(items),
            },
        )

    # ================================================================
    # 6) product-lifecycle
    # ================================================================
    async def product_lifecycle(
        self,
        product: str,
    ) -> ProductLifecycleResponse:
        """timeline_events.release 기준 D+0/7/30/90/180 sentiment + count."""
        # release date 조회 (timeline_events 우선, 없으면 products.released_at)
        r = await self.db.execute(
            text("""
                SELECT event_date
                FROM timeline_events
                WHERE event_type='release' AND product_code = :code
                ORDER BY event_date ASC LIMIT 1
            """),
            {"code": product.upper()},
        )
        ev_date = r.scalar()
        if ev_date is None:
            r2 = await self.db.execute(
                text("SELECT released_at::date FROM products WHERE code = :code"),
                {"code": product.upper()},
            )
            ev_date = r2.scalar()

        offsets = [0, 7, 30, 90, 180]
        points: List[LifecyclePoint] = []

        if ev_date is None:
            return ProductLifecycleResponse(
                product=product,
                release_date=None,
                points=[],
                meta={"reason": "release_date_not_found"},
            )

        for off in offsets:
            # 윈도우: [ev_date + off - 3, ev_date + off + 3]  (각 anchor 기준 ±3일)
            window = 3
            d_from = ev_date + timedelta(days=off - window)
            d_to = ev_date + timedelta(days=off + window)
            r = await self.db.execute(
                text("""
                    SELECT COUNT(*) AS c,
                           AVG(v.sentiment_score) AS s
                    FROM voc_active v
                    JOIN products p ON p.id = v.product_id
                    WHERE p.code = :code
                      AND v.published_at::date >= :d_from
                      AND v.published_at::date <= :d_to
                """),
                {"code": product.upper(), "d_from": d_from, "d_to": d_to},
            )
            row = r.first()
            c = int(row.c or 0)
            s = float(row.s or 0)

            # top categories
            top_cats: List[str] = []
            if c > 0:
                r2 = await self.db.execute(
                    text("""
                        SELECT cat, COUNT(*) AS cc
                        FROM (
                            SELECT UNNEST(v.categories) AS cat
                            FROM voc_active v
                            JOIN products p ON p.id = v.product_id
                            WHERE p.code = :code
                              AND v.published_at::date >= :d_from
                              AND v.published_at::date <= :d_to
                              AND v.categories IS NOT NULL
                        ) t
                        GROUP BY cat
                        ORDER BY cc DESC
                        LIMIT 5
                    """),
                    {"code": product.upper(), "d_from": d_from, "d_to": d_to},
                )
                top_cats = [r3.cat for r3 in r2 if r3.cat]

            points.append(
                LifecyclePoint(
                    d_offset=off,
                    window_from=str(d_from),
                    window_to=str(d_to),
                    count=c,
                    sent_avg=round(s, 4),
                    top_categories=top_cats,
                )
            )

        return ProductLifecycleResponse(
            product=product.upper(),
            release_date=str(ev_date),
            points=points,
            meta={"window_days": 3, "offsets": offsets},
        )

    # ================================================================
    # 7) platform-influence
    # ================================================================
    async def platform_influence(
        self,
        period_days: int,
    ) -> PlatformInfluenceResponse:
        """사이트별 영향력 = normalize(engagement) × normalize(neg_rate) × leading_score.

        leading_score: 다른 사이트보다 키워드/카테고리가 일찍 등장하면 1, 아니면 0~1.
        근사: 사이트별 published_at 평균이 전체 평균보다 빠를수록 높음.
        """
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)

        sql = """
            WITH baseline AS (
                SELECT AVG(EXTRACT(EPOCH FROM v2.published_at)) AS avg_epoch
                FROM voc_active v2
                WHERE v2.published_at::date >= :d_from
                  AND v2.published_at::date <= :anchor
            )
            SELECT
                pl.code                                     AS platform,
                pl.region                                   AS region,
                COUNT(*)                                    AS n,
                AVG(COALESCE(v.likes_count,0)
                  + COALESCE(v.comments_count,0)
                  + COALESCE(v.shares_count,0))::float      AS engagement,
                SUM(CASE WHEN v.sentiment_label='negative' THEN 1 ELSE 0 END)::float
                  / NULLIF(COUNT(*),0)                      AS neg_ratio,
                AVG(EXTRACT(EPOCH FROM v.published_at))
                  - (SELECT avg_epoch FROM baseline)        AS lag_seconds_vs_all
            FROM voc_active v
            JOIN platforms pl ON pl.id = v.platform_id
            WHERE v.published_at::date >= :d_from
              AND v.published_at::date <= :anchor
            GROUP BY pl.code, pl.region
            HAVING COUNT(*) >= 5
        """
        rows = (
            await self.db.execute(
                text(sql),
                {"d_from": d_from, "anchor": anchor},
            )
        ).all()

        # 점수 계산: engagement × neg_rate × leading_factor
        # leading_factor: lag_seconds_vs_all 음수일수록 (먼저) 큰 값 → exp(-x/86400) 스케일
        raw_scores: List[float] = []
        cached: List[Tuple[str, Optional[str], int, float, float, float]] = []
        for r in rows:
            n = int(r.n or 0)
            eng = float(r.engagement or 0)
            neg_rate = float(r.neg_ratio or 0) * 100.0  # 0~100
            lag_sec = float(r.lag_seconds_vs_all or 0)
            lag_days = lag_sec / 86400.0
            # leading factor: lag_days 음수면 선행(좋음), 양수면 후행
            # bounded sigmoid: 1 / (1 + exp(lag_days/2))
            try:
                import math
                lead_factor = 1.0 / (1.0 + math.exp(max(min(lag_days / 2.0, 20), -20)))
            except Exception:
                lead_factor = 0.5
            score = (eng + 1.0) * (neg_rate + 1.0) * lead_factor
            raw_scores.append(score)
            cached.append((r.platform, r.region, n, eng, neg_rate, lag_days))

        norm = _normalize_scores(raw_scores)
        items: List[PlatformInfluenceEntry] = []
        for (code, region, n, eng, neg_rate, lag_days), s in zip(cached, norm):
            items.append(
                PlatformInfluenceEntry(
                    platform=code,
                    region=region,
                    score=s,
                    n=n,
                    drivers=InfluenceDrivers(
                        engagement=round(eng, 3),
                        neg_rate=round(neg_rate, 2),
                        lag_days=round(lag_days, 3),
                    ),
                )
            )
        items.sort(key=lambda x: x.score, reverse=True)

        return PlatformInfluenceResponse(
            items=items,
            meta={
                "period_days": period_days,
                "anchor_date": str(anchor),
                "total": len(items),
            },
        )

    # ================================================================
    # 8) compare-llm (트랙 D) — N개 제품의 LLM 비교 분석
    # ================================================================
    async def _build_compare_payload(
        self, products: List[str], period_days: int
    ) -> Dict[str, Any]:
        """제품 코드 리스트 → compare_insight.generate_compare_narrative 가 받는 payload.

        각 제품 별 (count, sent_avg, neg_count, pos_count, top_categories[3],
        neg_keywords[5]) 을 anchor_date - period_days 윈도우에서 집계.
        """
        anchor = await self._anchor_date()
        d_from = anchor - timedelta(days=period_days)
        codes = [c.upper() for c in products if c]
        if not codes:
            return {"period_days": period_days, "products": []}

        # 제품별 핵심 KPI
        rows = (await self.db.execute(
            text(
                """
                SELECT p.code, p.name_ko,
                       COUNT(*)                                                  AS n,
                       AVG(v.sentiment_score)                                    AS s,
                       SUM(CASE WHEN v.sentiment_label='negative' THEN 1 ELSE 0 END) AS neg,
                       SUM(CASE WHEN v.sentiment_label='positive' THEN 1 ELSE 0 END) AS pos
                  FROM voc_active v
                  JOIN products p ON p.id = v.product_id
                 WHERE p.code = ANY(:codes)
                   AND v.published_at::date >= :d_from
                   AND v.published_at::date <= :anchor
                 GROUP BY p.code, p.name_ko
                """
            ),
            {"codes": codes, "d_from": d_from, "anchor": anchor},
        )).all()
        kpi_by_code = {
            r.code: {
                "code": r.code,
                "name_ko": r.name_ko,
                "count": int(r.n or 0),
                "sent_avg": round(float(r.s or 0), 4),
                "neg_count": int(r.neg or 0),
                "pos_count": int(r.pos or 0),
            }
            for r in rows
        }

        # 제품별 top_categories (3개) — categories array 펼침
        cat_rows = (await self.db.execute(
            text(
                """
                SELECT p.code AS pcode, cat AS code, c.name_ko AS name_ko, COUNT(*) AS n
                  FROM (
                       SELECT v.product_id, UNNEST(v.categories) AS cat
                         FROM voc_active v
                        WHERE v.published_at::date >= :d_from
                          AND v.published_at::date <= :anchor
                          AND v.categories IS NOT NULL
                  ) t
                  JOIN products p ON p.id = t.product_id
                  LEFT JOIN voc_categories c ON c.code = t.cat
                 WHERE p.code = ANY(:codes)
                 GROUP BY p.code, cat, c.name_ko
                """
            ),
            {"codes": codes, "d_from": d_from, "anchor": anchor},
        )).all()
        cats_by_code: Dict[str, List[Dict[str, Any]]] = {}
        for r in cat_rows:
            cats_by_code.setdefault(r.pcode, []).append({
                "code": r.code,
                "name_ko": r.name_ko or r.code,
                "n": int(r.n or 0),
            })
        for code in cats_by_code:
            cats_by_code[code] = sorted(
                cats_by_code[code], key=lambda x: -x["n"]
            )[:3]

        # 제품별 부정 키워드 TOP 5 — voc_keywords + sentiment_label='negative'
        kw_rows = (await self.db.execute(
            text(
                """
                SELECT p.code AS pcode, k.keyword, COUNT(*) AS n
                  FROM voc_keywords k
                  JOIN voc_records v ON v.id = k.voc_id
                  JOIN products p ON p.id = v.product_id
                 WHERE p.code = ANY(:codes)
                   AND v.published_at::date >= :d_from
                   AND v.published_at::date <= :anchor
                   AND v.sentiment_label = 'negative'
                 GROUP BY p.code, k.keyword
                """
            ),
            {"codes": codes, "d_from": d_from, "anchor": anchor},
        )).all()
        kws_by_code: Dict[str, List[Dict[str, Any]]] = {}
        for r in kw_rows:
            kws_by_code.setdefault(r.pcode, []).append({
                "keyword": r.keyword,
                "n": int(r.n or 0),
            })
        for code in kws_by_code:
            kws_by_code[code] = sorted(
                kws_by_code[code], key=lambda x: -x["n"]
            )[:5]

        out_products: List[Dict[str, Any]] = []
        for code in codes:
            base = kpi_by_code.get(code) or {
                "code": code,
                "name_ko": code,
                "count": 0,
                "sent_avg": 0.0,
                "neg_count": 0,
                "pos_count": 0,
            }
            base["top_categories"] = cats_by_code.get(code, [])
            base["neg_keywords"] = kws_by_code.get(code, [])
            out_products.append(base)

        return {
            "period_days": period_days,
            "anchor_date": str(anchor),
            "products": out_products,
        }

    @redis_cache(
        ttl_seconds=900, key_prefix="insights:compare_llm:", model_cls=CompareLLMResponse
    )
    async def compare_llm(
        self,
        products: List[str],
        period_days: int,
    ) -> CompareLLMResponse:
        """N개 제품의 비교 분석 narrative 를 LLM(tier=auto, 14b 기대) 으로 생성.

        키 미설정 / LLM 실패 시 narrative=None, tier_label='none', score=0.0.
        Redis 캐시 15분 — 동일 입력 반복 비용 방지.
        """
        payload = await self._build_compare_payload(products, period_days)
        # crawler.insight 호출은 동기 + 네트워크 — 길어질 수 있으므로 thread 로.
        import asyncio
        from insight.compare_insight import generate_compare_narrative  # type: ignore

        loop = asyncio.get_running_loop()
        narrative, score, tier_label = await loop.run_in_executor(
            None,
            generate_compare_narrative,
            payload,
        )

        return CompareLLMResponse(
            narrative=narrative,
            tier_label=tier_label,
            grounding_score=round(float(score or 0.0), 4),
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            products=[c.upper() for c in products],
            period_days=period_days,
        )


__all__ = ["InsightsService"]
