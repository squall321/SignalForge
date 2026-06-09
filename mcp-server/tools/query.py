"""MCP Query Tools — VOC 조회"""
from typing import Optional, List
from db import get_db_session
from sqlalchemy import text


async def query_voc_tool(
    product_code: str,
    country: Optional[str] = None,
    category: Optional[str] = None,
    sentiment: Optional[str] = None,
    limit: int = 20,
) -> List[dict]:
    conditions = ["p.code = :product_code"]
    params: dict = {"product_code": product_code.upper(), "limit": limit}

    if country:
        conditions.append("v.country_code = :country")
        params["country"] = country.upper()
    if sentiment:
        conditions.append("v.sentiment_label = :sentiment")
        params["sentiment"] = sentiment
    if category:
        conditions.append(":category = ANY(v.categories)")
        params["category"] = category

    where = " AND ".join(conditions)
    stmt = text(f"""
        SELECT
            v.id, v.external_id, v.source_url, v.author_name,
            v.content_original, v.content_translated,
            v.language_detected, v.country_code,
            v.sentiment_score, v.sentiment_label, v.categories,
            v.likes_count, v.comments_count, v.engagement_score,
            v.published_at, pl.name AS platform_name
        FROM voc_records v
        JOIN products p ON p.id = v.product_id
        JOIN platforms pl ON pl.id = v.platform_id
        WHERE {where}
        ORDER BY v.published_at DESC NULLS LAST
        LIMIT :limit
    """)

    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
        return [dict(r) for r in rows]


async def get_top_issues_tool(
    product_code: str, period_days: int = 30, top_n: int = 10
) -> List[dict]:
    stmt = text("""
        SELECT
            cat AS category,
            COUNT(*) AS total_count,
            ROUND(
                SUM(CASE WHEN v.sentiment_label = 'negative' THEN 1 ELSE 0 END)::numeric
                / NULLIF(COUNT(*), 0) * 100, 1
            ) AS negative_rate
        FROM voc_records v
        JOIN products p ON p.id = v.product_id,
             unnest(v.categories) AS cat
        WHERE p.code = :product_code
          AND v.collected_at >= NOW() - make_interval(days => :period_days)
          AND v.categories IS NOT NULL
        GROUP BY cat
        ORDER BY total_count DESC
        LIMIT :top_n
    """)

    async with get_db_session() as db:
        rows = (await db.execute(stmt, {
            "product_code": product_code.upper(),
            "period_days": period_days,
            "top_n": top_n,
        })).mappings().all()
        return [dict(r) for r in rows]


async def search_voc_tool(
    keyword: str, product_code: Optional[str] = None, limit: int = 30
) -> List[dict]:
    conditions = ["to_tsvector('english', COALESCE(v.content_translated, '')) @@ plainto_tsquery('english', :keyword)"]
    params: dict = {"keyword": keyword, "limit": limit}

    if product_code:
        conditions.append("p.code = :product_code")
        params["product_code"] = product_code.upper()

    where = " AND ".join(conditions)
    stmt = text(f"""
        SELECT
            v.id, v.source_url, v.author_name,
            v.content_translated, v.sentiment_label,
            v.categories, v.published_at,
            pl.name AS platform_name,
            p.name_en AS product_name
        FROM voc_records v
        JOIN products p ON p.id = v.product_id
        JOIN platforms pl ON pl.id = v.platform_id
        WHERE {where}
        ORDER BY v.engagement_score DESC NULLS LAST
        LIMIT :limit
    """)

    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
        return [dict(r) for r in rows]
