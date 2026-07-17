"""MCP Analytics Tools"""
from typing import Optional, List
from datetime import datetime
from db import get_db_session
from sqlalchemy import text


async def analyze_sentiment_trend_tool(
    product_code: str, period_days: int = 90, granularity: str = "week",
    start_date: Optional[str] = None, end_date: Optional[str] = None,
) -> dict:
    trunc = granularity if granularity in ("day", "week", "month") else "week"
    # 기간 지정 방식 2가지: start_date/end_date(임의 구간) 가 있으면 그것을 쓰고,
    # 없으면 기존처럼 "최근 period_days 일". 둘 중 하나만 줘도 됨(반열림 구간).
    conditions = ["p.code = :product_code", "v.published_at IS NOT NULL"]
    params: dict = {"trunc": trunc, "product_code": product_code.upper()}
    if start_date or end_date:
        # asyncpg 는 str 를 안 받으므로 datetime 객체로 변환해 바인딩.
        if start_date:
            conditions.append("v.published_at >= :start_date")
            params["start_date"] = datetime.fromisoformat(start_date)
        if end_date:
            conditions.append("v.published_at < :end_date")
            params["end_date"] = datetime.fromisoformat(end_date)
    else:
        conditions.append("v.published_at >= NOW() - make_interval(days => :period_days)")
        params["period_days"] = period_days
    where = " AND ".join(conditions)
    stmt = text(f"""
        SELECT
            date_trunc(:trunc, v.published_at)::date AS period,
            SUM(CASE WHEN v.sentiment_label = 'positive' THEN 1 ELSE 0 END) AS positive,
            SUM(CASE WHEN v.sentiment_label = 'negative' THEN 1 ELSE 0 END) AS negative,
            SUM(CASE WHEN v.sentiment_label = 'neutral'  THEN 1 ELSE 0 END) AS neutral,
            ROUND(AVG(v.sentiment_score)::numeric, 3)                        AS avg_score
        FROM voc_active v
        JOIN products p ON p.id = v.product_id
        WHERE {where}
        GROUP BY period
        ORDER BY period
    """)

    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
        return {
            "product_code": product_code,
            "granularity": granularity,
            "range": {"start_date": start_date, "end_date": end_date, "period_days": None if (start_date or end_date) else period_days},
            "data": [dict(r) for r in rows],
        }


async def compare_products_tool(
    product_codes: List[str], category: Optional[str] = None
) -> dict:
    results = {}
    for code in product_codes:
        cat_filter = "AND :category = ANY(v.categories)" if category else ""
        stmt = text(f"""
            SELECT
                unnest(v.categories) AS cat,
                ROUND(AVG(v.sentiment_score)::numeric, 3) AS avg_score
            FROM voc_active v
            JOIN products p ON p.id = v.product_id
            WHERE p.code = :product_code
              AND v.collected_at >= NOW() - INTERVAL '30 days'
              {cat_filter}
            GROUP BY cat
            ORDER BY avg_score DESC
        """)
        params: dict = {"product_code": code.upper()}
        if category:
            params["category"] = category

        async with get_db_session() as db:
            rows = (await db.execute(stmt, params)).mappings().all()
            results[code] = {r["cat"]: float(r["avg_score"]) for r in rows}

    return results


async def get_country_breakdown_tool(
    product_code: str, period_days: int = 30
) -> List[dict]:
    stmt = text("""
        SELECT
            v.country_code,
            COUNT(*) AS voc_count,
            ROUND(AVG(v.sentiment_score)::numeric, 3) AS avg_score,
            ROUND(
                SUM(CASE WHEN v.sentiment_label = 'positive' THEN 1 ELSE 0 END)::numeric
                / NULLIF(COUNT(*), 0) * 100, 1
            ) AS positive_rate
        FROM voc_active v
        JOIN products p ON p.id = v.product_id
        WHERE p.code = :product_code
          AND v.collected_at >= NOW() - make_interval(days => :period_days)
          AND v.country_code IS NOT NULL
        GROUP BY v.country_code
        ORDER BY voc_count DESC
        LIMIT 30
    """)

    async with get_db_session() as db:
        rows = (await db.execute(stmt, {
            "product_code": product_code.upper(), "period_days": period_days
        })).mappings().all()
        return [dict(r) for r in rows]


async def get_voc_summary_tool(product_code: str, period_days: int = 7) -> str:
    """주요 지표를 텍스트 요약으로 반환 (Claude가 이를 기반으로 분석)"""
    # 기본 통계
    stats_stmt = text("""
        SELECT
            COUNT(*) AS total,
            ROUND(AVG(v.sentiment_score)::numeric, 3) AS avg_score,
            SUM(CASE WHEN v.sentiment_label = 'positive' THEN 1 ELSE 0 END) AS positive,
            SUM(CASE WHEN v.sentiment_label = 'negative' THEN 1 ELSE 0 END) AS negative
        FROM voc_active v
        JOIN products p ON p.id = v.product_id
        WHERE p.code = :product_code
          AND v.collected_at >= NOW() - make_interval(days => :period_days)
    """)

    # 상위 카테고리
    cat_stmt = text("""
        SELECT unnest(v.categories) AS cat, COUNT(*) AS cnt
        FROM voc_active v
        JOIN products p ON p.id = v.product_id
        WHERE p.code = :product_code
          AND v.collected_at >= NOW() - make_interval(days => :period_days)
          AND v.categories IS NOT NULL
        GROUP BY cat ORDER BY cnt DESC LIMIT 5
    """)

    params = {"product_code": product_code.upper(), "period_days": period_days}

    async with get_db_session() as db:
        stats = (await db.execute(stats_stmt, params)).mappings().one_or_none()
        cats = (await db.execute(cat_stmt, params)).mappings().all()

    if not stats or stats["total"] == 0:
        return f"{product_code}: 최근 {period_days}일간 VOC 데이터 없음"

    total = stats["total"]
    pos_rate = round(stats["positive"] / total * 100, 1) if total else 0
    neg_rate = round(stats["negative"] / total * 100, 1) if total else 0
    top_cats = ", ".join(f"{r['cat']}({r['cnt']}건)" for r in cats)

    return (
        f"[{product_code}] 최근 {period_days}일 VOC 요약\n"
        f"총 {total}건 | 긍정 {pos_rate}% | 부정 {neg_rate}% | 평균감성 {stats['avg_score']}\n"
        f"주요 이슈: {top_cats}"
    )
