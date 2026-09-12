"""MCP Query Tools — VOC 조회"""
import re

from typing import Optional, List
from datetime import datetime
from db import get_db_session
from sqlalchemy import text


def _parse_dt(s: str):
    """'YYYY-MM-DD' 또는 ISO 타임스탬프 → datetime. asyncpg 는 str 를 안 받으므로 객체로 변환."""
    return datetime.fromisoformat(s)


async def query_voc_tool(
    product_code: Optional[str] = None,
    country: Optional[str] = None,
    category: Optional[str] = None,
    sentiment: Optional[str] = None,
    platform: Optional[str] = None,
    keyword: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 20,
) -> List[dict]:
    # 모든 필터가 선택 — product_code 없이도 전체 VOC 를 자유 조회(제품 태깅 ~18% 라
    # LEFT JOIN 으로 미태깅 VOC 도 포함). 날짜구간(published_at)·플랫폼·키워드까지 조합 가능.
    conditions: List[str] = ["TRUE"]
    params: dict = {"limit": limit}

    if product_code:
        conditions.append("p.code = :product_code")
        params["product_code"] = product_code.upper()
    if country:
        conditions.append("v.country_code = :country")
        params["country"] = country.upper()
    if sentiment:
        conditions.append("v.sentiment_label = :sentiment")
        params["sentiment"] = sentiment
    if category:
        conditions.append(":category = ANY(v.categories)")
        params["category"] = category
    if platform:
        conditions.append("pl.code = :platform")
        params["platform"] = platform
    if keyword:
        conditions.append(
            "to_tsvector('english', COALESCE(v.content_translated, '')) "
            "@@ plainto_tsquery('english', :keyword)")
        params["keyword"] = keyword
    if start_date:
        conditions.append("v.published_at >= :start_date")
        params["start_date"] = _parse_dt(start_date)
    if end_date:
        conditions.append("v.published_at < :end_date")
        params["end_date"] = _parse_dt(end_date)

    where = " AND ".join(conditions)
    stmt = text(f"""
        SELECT
            v.id, v.external_id, v.source_url, v.author_name,
            v.content_original, v.content_translated,
            v.language_detected, v.country_code,
            v.sentiment_score, v.sentiment_label, v.categories,
            v.likes_count, v.comments_count, v.engagement_score,
            v.published_at, pl.name AS platform_name,
            p.code AS product_code, p.name_ko AS product_name
        FROM voc_active v
        LEFT JOIN products p ON p.id = v.product_id
        LEFT JOIN platforms pl ON pl.id = v.platform_id
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
        FROM voc_active v
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



# ── 키워드 매칭 전략 ─────────────────────────────────────────────────────
# FTS 인덱스는 to_tsvector('english', content_translated) 하나뿐이라 **한국어
# 키워드가 사실상 안 먹는다.** 실측(2026-09-12) — '발열' FTS 15건 vs
# content_original 실제 포함 1,346건 = 재현율 1.1%.
# 원인은 두 겹이다 — (1) 한국어 행의 96.1%(118,513/123,322)가 영어로 번역돼
# content_translated 에 '발열'이 없고, (2) 검색이 content_original 을 아예 안 본다.
#
# 'simple' 설정으로 content_original 을 색인해도 한국어는 교착어라 조사 결합형
# ('발열이')을 못 잡아 재현율이 49.8%(670/1,346)에 그친다.
#
# 그래서 언어에 따라 경로를 나눈다 —
#   · ASCII 키워드  → FTS (인덱스 사용, 어간 처리로 overheating↔overheat 매칭)
#   · 비ASCII 키워드 → 두 컬럼 부분일치 (재현율 100%, 실측 1.0~1.4초)
# pg_trgm 인덱스를 깔면 부분일치도 빨라지지만 본문 203MB 에 수백 MB 가 더 붙는다.
# 1.2초가 대화형 호출에 견딜 만하므로 스키마는 건드리지 않는다.
_NON_ASCII_RE = re.compile(r"[^\x00-\x7F]")

_FTS_CLAUSE = (
    "to_tsvector('english', COALESCE(v.content_translated, '')) "
    "@@ plainto_tsquery('english', :keyword)"
)
# ILIKE 특수문자를 이스케이프해 '100%' 같은 키워드가 와일드카드로 해석되지 않게 한다
_SUBSTR_CLAUSE = (
    "(v.content_original ILIKE :kw_like ESCAPE '\\' "
    " OR v.content_translated ILIKE :kw_like ESCAPE '\\')"
)


def _keyword_clause(keyword: str, match: str = "auto"):
    """(clause, params) — 키워드 성격에 맞는 매칭 조건."""
    mode = (match or "auto").lower()
    if mode == "auto":
        mode = "substring" if _NON_ASCII_RE.search(keyword or "") else "fts"
    if mode == "substring":
        esc = (keyword or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return _SUBSTR_CLAUSE, {"kw_like": f"%{esc}%"}, "substring"
    return _FTS_CLAUSE, {"keyword": keyword}, "fts"


# 정렬 옵션 — 기본은 최신순이다.
# 이전에는 날짜 필터 없이 `ORDER BY engagement_score DESC` 뿐이었다. 그러면 "최근
# 이슈"를 물어도 **역대 최고 참여도 글**이 나온다. 오래된 글일수록 수년간 좋아요·
# 댓글이 쌓여 상위를 독식하기 때문이다. 실측(2026-09-12) — search_voc('발열') 의
# 1위가 2019년 뽐뿌 갤럭시 노트10+ 리뷰였고, engagement 상위 30의 제품이 GN9·GN4·
# GN5·GS10·GW4 같은 구형으로 채워졌다(21건은 아예 미태깅).
# 관련성 점수가 따로 없는 FTS 검색에서 "가장 바이럴했던 글"보다 "가장 최근 글"이
# 기본값으로 훨씬 유용하다. 바이럴 발굴은 order='engagement' 또는 전용 도구
# get_engagement_leaders(period_days 필터 내장)를 쓴다.
_SEARCH_ORDERS = {
    "recent": "v.published_at DESC NULLS LAST",
    "engagement": "v.engagement_score DESC NULLS LAST",
}


async def search_voc_tool(
    keyword: str, product_code: Optional[str] = None, limit: int = 30,
    days: Optional[int] = None, order: str = "recent", match: str = "auto",
) -> List[dict]:
    # products 는 LEFT JOIN — 제품 태깅율이 ~18% 라 INNER JOIN 시 미태깅 VOC 82% 가
    # 조용히 누락된다. 검색은 전체 voc_active 를 대상으로 해야 한다(product_code 지정 시만 좁힘).
    kw_clause, kw_params, used_match = _keyword_clause(keyword, match)
    conditions = [kw_clause]
    params: dict = {"limit": limit, **kw_params}

    if product_code:
        conditions.append("p.code = :product_code")
        params["product_code"] = product_code.upper()

    order_key = (order or "recent").lower()
    order_sql_is_recent = order_key not in _SEARCH_ORDERS or order_key == "recent"
    order_sql = _SEARCH_ORDERS.get(order_key, _SEARCH_ORDERS["recent"])

    if days:
        # published_at 기준 — collected_at 은 백필 때문에 옛 글도 최근값이라 못 쓴다
        conditions.append("v.published_at >= NOW() - make_interval(days => :days)")
        params["days"] = int(days)

    # 미래 발행일 방어. 한국 커뮤니티의 연도 없는 'MM-DD' 표기를 올해로 가정하면
    # 연말 글이 미래가 된다. 최신순 정렬은 그런 행을 **맨 위로** 끌어올리므로
    # (실측: 오늘이 09-12 인데 12-27·12-22 가 1위) 정렬 기준에서 배제한다.
    # 기존 32건은 alembic 0042 로 보정했고, 이건 재발 방어다.
    if order_sql_is_recent:
        conditions.append("(v.published_at IS NULL OR v.published_at <= NOW())")

    where = " AND ".join(conditions)
    stmt = text(f"""
        SELECT
            v.id, v.source_url, v.author_name,
            v.content_translated, v.sentiment_label,
            v.categories, v.published_at,
            pl.name AS platform_name,
            p.name_en AS product_name
        FROM voc_active v
        LEFT JOIN products p ON p.id = v.product_id
        LEFT JOIN platforms pl ON pl.id = v.platform_id
        WHERE {where}
        ORDER BY {order_sql}
        LIMIT :limit
    """)

    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
        return [dict(r) for r in rows]
