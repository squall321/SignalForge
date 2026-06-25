# MCP 보강 도구 — 미노출 데이터 차원(플랫폼/engagement/언어) 집계.
# 전부 voc_active 기반, products 는 LEFT JOIN(미태깅 82% 누락 방지), period_days=0 이면 전기간.
from typing import Optional, List
from db import get_db_session
from sqlalchemy import text


async def get_platform_breakdown_tool(
    product_code: Optional[str] = None, period_days: int = 30, top_n: int = 20
) -> List[dict]:
    """플랫폼(커뮤니티/사이트)별 VOC 분포 + 감성. platform_id 는 100% 채워져 전수 커버.

    어느 채널에서 말이 많은지/감성이 어떤지 본다. product_code 지정 시 해당 제품만.
    """
    top_n = max(1, min(top_n, 100))
    pfilt = "AND p.code = :code" if product_code else ""
    dfilt = "AND v.collected_at >= NOW() - make_interval(days => :days)" if period_days else ""
    stmt = text(f"""
        SELECT COALESCE(pl.name, 'unknown') AS platform,
               COUNT(*) AS voc_count,
               ROUND(AVG(v.sentiment_score)::numeric, 3) AS avg_score,
               ROUND(SUM(CASE WHEN v.sentiment_label='negative' THEN 1 ELSE 0 END)::numeric
                     / NULLIF(COUNT(*),0) * 100, 1) AS negative_rate
        FROM voc_active v
        LEFT JOIN platforms pl ON pl.id = v.platform_id
        {"JOIN products p ON p.id = v.product_id" if product_code else ""}
        WHERE TRUE {pfilt} {dfilt}
        GROUP BY platform ORDER BY voc_count DESC LIMIT :top_n
    """)
    params: dict = {"top_n": top_n}
    if product_code:
        params["code"] = product_code.upper()
    if period_days:
        params["days"] = period_days
    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
        return [dict(r) for r in rows]


async def get_engagement_leaders_tool(
    product_code: Optional[str] = None, period_days: int = 30, limit: int = 20
) -> List[dict]:
    """engagement_score(좋아요/댓글/공유 기반) 상위 VOC — 바이럴·고영향 게시물 발굴.

    likes/comments/shares 가 있는 행만 의미가 있으므로 engagement_score>0 으로 한정.
    """
    limit = max(1, min(limit, 100))
    pfilt = "AND p.code = :code" if product_code else ""
    dfilt = "AND v.collected_at >= NOW() - make_interval(days => :days)" if period_days else ""
    stmt = text(f"""
        SELECT v.id, v.source_url, v.author_name,
               COALESCE(pl.name, 'unknown') AS platform_name,
               v.country_code, v.sentiment_label,
               v.likes_count, v.comments_count, v.shares_count,
               ROUND(v.engagement_score::numeric, 2) AS engagement_score,
               LEFT(COALESCE(v.content_translated, v.content_original), 200) AS snippet,
               v.published_at
        FROM voc_active v
        LEFT JOIN platforms pl ON pl.id = v.platform_id
        {"JOIN products p ON p.id = v.product_id" if product_code else ""}
        WHERE v.engagement_score > 0 {pfilt} {dfilt}
        ORDER BY v.engagement_score DESC NULLS LAST
        LIMIT :limit
    """)
    params: dict = {"limit": limit}
    if product_code:
        params["code"] = product_code.upper()
    if period_days:
        params["days"] = period_days
    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
        return [dict(r) for r in rows]


async def get_language_breakdown_tool(
    period_days: int = 0, top_n: int = 25
) -> List[dict]:
    """언어(language_detected)별 VOC 분포 + 감성 — 글로벌 커버리지 가시화. 전기간 기본."""
    top_n = max(1, min(top_n, 60))
    dfilt = "AND collected_at >= NOW() - make_interval(days => :days)" if period_days else ""
    stmt = text(f"""
        SELECT language_detected AS language,
               COUNT(*) AS voc_count,
               ROUND(AVG(sentiment_score)::numeric, 3) AS avg_score
        FROM voc_active
        WHERE language_detected IS NOT NULL {dfilt}
        GROUP BY language_detected ORDER BY voc_count DESC LIMIT :top_n
    """)
    params: dict = {"top_n": top_n}
    if period_days:
        params["days"] = period_days
    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
        return [dict(r) for r in rows]
