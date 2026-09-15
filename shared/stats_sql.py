"""공유 통계 SQL — 코퍼스 전체 통계·기간 비교·키워드 통계 프로파일.

**backend REST 와 MCP 가 같은 SQL 을 쓰기 위한 공유 모듈이다.**
두 서비스는 별도 컨테이너라 각자 복제하면 반드시 갈라진다 — 실제로
search 키워드 매칭이 그렇게 갈라져 백엔드는 두 컬럼을 보는데 MCP 만
번역본만 봐서 한국어 재현율이 1.1% 였다(2026-09-12).

포털 LLM 검색이 "감질맛나게" 나오던 이유는 도구가 **행을 주고 통계를 안 줬기**
때문이다. search_voc 는 30행을 돌려줄 뿐 "총 몇 건인지, 전월 대비 얼마나 늘었는지,
어느 제품에 몰렸는지"를 말해주지 못한다. 기존 30종도 대부분 제품 단위라
코퍼스 전체를 가로지르는 집계가 없었다.

이 모듈은 네 가지를 준다 —
  corpus_overview  우리가 뭘 갖고 있나 (규모·기간·커버리지·품질) 한 번에
  keyword_stats    키워드의 통계 프로파일 (건수·추세·분포·감성) — 검색의 통계판
  period_compare   기간 대비 증감 (임의 슬라이스)
  voc_breakdown    전체 VOC 를 임의 축으로 분해 (결함 아닌 것 포함)
"""
import re
from typing import Optional

from sqlalchemy import text  # noqa: F401

# 한국어 등 비ASCII 키워드는 FTS 가 안 먹는다(교착어 + 번역본만 색인).
# query.py 와 같은 판단을 쓴다 — 실측 재현율 FTS 1.1% vs 부분일치 100%.
_NON_ASCII_RE = re.compile(r"[^\x00-\x7F]")


def _keyword_cond(keyword: str):
    """(SQL 조건, params) — 키워드 성격에 맞는 매칭."""
    if _NON_ASCII_RE.search(keyword or ""):
        esc = (keyword or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return ("(v.content_original ILIKE :kw_like ESCAPE '\\' "
                " OR v.content_translated ILIKE :kw_like ESCAPE '\\')",
                {"kw_like": f"%{esc}%"}, "substring")
    return ("to_tsvector('english', COALESCE(v.content_translated, '')) "
            "@@ plainto_tsquery('english', :keyword)",
            {"keyword": keyword}, "fts")


# 전체 VOC 슬라이스 축 — 결함 도구(defects._FILTER_SPECS)와 같은 사고방식이다.
_SLICE_SPECS = [
    ("product_code", "p.code = :product_code", lambda v: v.upper()),
    ("brand",        "p.brand = :brand", lambda v: v.lower()),
    ("category",     "p.category = :category", lambda v: v.lower()),
    ("country",      "v.country_code = :country", lambda v: v.upper()),
    ("platform",     "pl.code = :platform", None),
    ("sentiment",    "v.sentiment_label = :sentiment", None),
    ("language",     "v.language_detected = :language", None),
]

_SLICE_JOINS = """
    FROM voc_records v
    LEFT JOIN products p   ON p.id = v.product_id
    LEFT JOIN platforms pl ON pl.id = v.platform_id
"""


def _slice_filters(days: Optional[int] = None, keyword: Optional[str] = None,
                   since: Optional[str] = None, until: Optional[str] = None, **kw):
    """(conditions, params, match_mode)."""
    conds = ["v.archived_at IS NULL"]
    params: dict = {}
    mode = None
    if days:
        conds.append("v.published_at >= NOW() - make_interval(days => :days)")
        params["days"] = days
    if since:
        conds.append("v.published_at >= :since")
        params["since"] = since
    if until:
        conds.append("v.published_at < :until")
        params["until"] = until
    if days or since or until:
        # 미래 발행일 방어 — 파싱 오류가 기간 끝을 왜곡한다(alembic 0042 참조)
        conds.append("v.published_at <= NOW()")
    if keyword:
        c, p, mode = _keyword_cond(keyword)
        conds.append(c)
        params.update(p)
    for name, clause, norm in _SLICE_SPECS:
        val = kw.get(name)
        if val in (None, ""):
            continue
        conds.append(clause)
        params[name] = norm(val) if norm else val
    return conds, params, mode




async def corpus_overview(execute, days: Optional[int] = None) -> dict:
    """코퍼스 전체 현황 — 규모·기간·커버리지·품질을 한 번에.

    "우리가 뭘 갖고 있나"에 답한다. LLM 이 답변 앞에 놓을 전제를 만들어준다.
    """
    dfilt = "AND v.published_at >= NOW() - make_interval(days => :days) AND v.published_at <= NOW()" if days else ""
    params = {"days": days} if days else {}
    stmt = text(f"""
        SELECT
            count(*)::int                                            AS voc_total,
            count(*) FILTER (WHERE v.product_id IS NOT NULL)::int     AS tagged,
            count(DISTINCT v.product_id)::int                         AS products_seen,
            count(DISTINCT v.platform_id)::int                        AS platforms_seen,
            count(DISTINCT v.country_code)::int                       AS countries,
            count(DISTINCT v.language_detected)::int                  AS languages,
            min(v.published_at)::date                                 AS oldest_raw,
            max(v.published_at)::date                                 AS newest,
            -- 단일 이상치(파싱 오류)가 코퍼스 시작을 왜곡한다. 실측 — recalls 1건이
            -- 1976-04-22 라 '1976년부터'로 보였다. 1퍼센타일로 견고하게 잡는다.
            (percentile_disc(0.01) WITHIN GROUP (ORDER BY v.published_at))::date AS oldest,
            count(*) FILTER (WHERE v.sentiment_label='negative')::int AS negative,
            count(*) FILTER (WHERE v.categories IS NOT NULL
                             AND v.categories <> '{{}}')::int         AS categorized
        FROM voc_records v
        WHERE v.archived_at IS NULL {dfilt}
    """)
    defect_stmt = text(f"""
        SELECT count(DISTINCT d.voc_id)::int AS defect_docs,
               count(*)::int                 AS defect_records,
               count(*) FILTER (WHERE d.modality='firsthand')::int AS firsthand
        FROM voc_defects d JOIN voc_records v ON v.id = d.voc_id
        WHERE v.archived_at IS NULL {dfilt}
    """)
    cat_stmt = text("""
        SELECT p.category,
               count(*)::int AS products,
               count(*) FILTER (WHERE p.is_active)::int AS active
        FROM products p GROUP BY 1 ORDER BY 2 DESC
    """)
    base = (await execute(stmt, params)).mappings().first()
    dfx = (await execute(defect_stmt, params)).mappings().first()
    cats = (await execute(cat_stmt)).mappings().all()
    b = dict(base or {})
    total = b.get("voc_total") or 0
    return {
        "period_days": days,
        "scale": {
            "voc_total": total,
            "oldest": b.get("oldest").isoformat() if b.get("oldest") else None,
            "oldest_raw": b.get("oldest_raw").isoformat() if b.get("oldest_raw") else None,
            "newest": b.get("newest").isoformat() if b.get("newest") else None,
            "platforms": b.get("platforms_seen"),
            "countries": b.get("countries"),
            "languages": b.get("languages"),
        },
        "coverage": {
            "tagged": b.get("tagged"),
            "tagged_pct": round(100.0 * (b.get("tagged") or 0) / total, 1) if total else 0.0,
            "products_seen": b.get("products_seen"),
            "catalog_products": sum(int(c["products"]) for c in cats),
            "catalog_active": sum(int(c["active"]) for c in cats),
            "catalog_by_category": {c["category"]: int(c["products"]) for c in cats},
        },
        "quality": {
            "categorized_pct": round(100.0 * (b.get("categorized") or 0) / total, 1) if total else 0.0,
            "negative_pct": round(100.0 * (b.get("negative") or 0) / total, 1) if total else 0.0,
        },
        "defects": {
            "docs": dfx.get("defect_docs") if dfx else 0,
            "records": dfx.get("defect_records") if dfx else 0,
            "firsthand": dfx.get("firsthand") if dfx else 0,
            "defect_rate_pct": round(100.0 * (dfx.get("defect_docs") or 0) / total, 2) if total and dfx else 0.0,
        },
        "note": ("tagged_pct 가 낮은 것은 결함이 아니다 — 코퍼스에 일반 IT 담론이 섞여 있고 "
                 "제품이 특정되지 않는 글이 다수다. categorized_pct 도 토픽이 없으면 비는 것이 정상이다. "
                 "oldest 는 1퍼센타일(파싱 이상치 방어), oldest_raw 는 실제 최솟값이다."),
    }


async def voc_breakdown(
    execute, by: str = "product", days: Optional[int] = 30, limit: int = 20,
    keyword: Optional[str] = None, **filters,
) -> dict:
    """전체 VOC 를 임의 축으로 분해. 결함에 한정되지 않는다.

    by: product | brand | category | country | platform | language |
        sentiment | platform_kind
    """
    axes = {
        "product":  ("COALESCE(p.code,'(untagged)')", "product"),
        "brand":    ("COALESCE(p.brand,'(untagged)')", "brand"),
        "category": ("COALESCE(p.category,'(untagged)')", "category"),
        "country":  ("COALESCE(v.country_code,'??')", "country"),
        "platform": ("COALESCE(pl.code,'unknown')", "platform"),
        "platform_kind": ("COALESCE(pl.kind,'unknown')", "platform_kind"),
        "language": ("COALESCE(v.language_detected,'?')", "language"),
        "sentiment": ("COALESCE(v.sentiment_label,'unknown')", "sentiment"),
    }
    expr, label = axes.get(by, axes["product"])
    conds, params, mode = _slice_filters(days=days, keyword=keyword, **filters)
    params["limit"] = limit
    stmt = text(f"""
        SELECT {expr} AS key,
               count(*)::int AS docs,
               count(*) FILTER (WHERE v.sentiment_label='negative')::int AS negative,
               round(100.0 * count(*) FILTER (WHERE v.sentiment_label='negative')
                     / NULLIF(count(*),0), 1)::float AS negative_pct,
               round(avg(v.sentiment_score)::numeric, 3)::float AS avg_sentiment
        {_SLICE_JOINS}
        WHERE {" AND ".join(conds)}
        GROUP BY 1 ORDER BY docs DESC LIMIT :limit
    """)
    total_stmt = text(f"SELECT count(*)::int AS n {_SLICE_JOINS} WHERE {' AND '.join(conds)}")
    rows = (await execute(stmt, params)).mappings().all()
    total = (await execute(total_stmt, params)).scalar() or 0
    out = [dict(r) for r in rows]
    for r in out:
        r["share_pct"] = round(100.0 * r["docs"] / total, 1) if total else 0.0
    return {"by": label, "days": days, "keyword": keyword, "match": mode,
            "total": total, "rows": out,
            "note": "share_pct 는 이 슬라이스 전체(total) 대비 비중이다."}


async def keyword_stats(
    execute, keyword: str, days: int = 90, interval: str = "week", top_n: int = 8,
    **filters,
) -> dict:
    """키워드의 **통계 프로파일** — 검색의 통계판.

    search_voc 가 행을 준다면 이건 "총 몇 건, 추세, 어디에 몰렸나, 감성은"을 준다.
    LLM 이 '몇 건 나왔다'가 아니라 '총 1,012건이고 전기 대비 +561%, GZF8 이 32%'
    라고 말할 수 있게 하는 것이 목적이다.

    **한 번만 스캔한다.** 한국어 키워드는 부분일치라 스캔당 1초대인데, 집계마다
    따로 돌리면 6회 = 10초가 된다(실측). 매칭 결과를 CTE 로 한 번 잡고 그 위에서
    모든 집계를 낸다.
    """
    bucket = {"day": "day", "week": "week", "month": "month"}.get(interval, "week")
    conds, params, mode = _slice_filters(keyword=keyword, **filters)
    params.update({"d1": days, "d2": days * 2, "top_n": top_n})
    where = " AND ".join(conds)

    stmt = text(f"""
        WITH hit AS (
            SELECT v.id, v.published_at, v.sentiment_label, v.sentiment_score,
                   v.country_code, v.platform_id,
                   split_part(v.source_url,'?','1'::int) AS url,
                   COALESCE(p.code,'(untagged)')  AS product,
                   COALESCE(pl.code,'unknown')    AS platform,
                   (v.published_at >= NOW() - make_interval(days => :d1)) AS in_cur,
                   (v.published_at >= NOW() - make_interval(days => :d2)
                    AND v.published_at < NOW() - make_interval(days => :d1)) AS in_prev
            {_SLICE_JOINS}
            WHERE {where}
              AND v.published_at >= NOW() - make_interval(days => :d2)
              AND v.published_at <= NOW()
        ), cur AS (SELECT * FROM hit WHERE in_cur)
        SELECT
          (SELECT count(*)::int FROM cur)                                  AS docs,
          (SELECT count(DISTINCT url)::int FROM cur)                       AS uniq_urls,
          (SELECT count(DISTINCT platform_id)::int FROM cur)               AS platforms,
          (SELECT count(DISTINCT country_code)::int FROM cur)              AS countries,
          (SELECT count(*) FILTER (WHERE sentiment_label='negative')::int FROM cur) AS negative,
          (SELECT round(avg(sentiment_score)::numeric,3)::float FROM cur)  AS avg_sentiment,
          (SELECT min(published_at)::date FROM cur)                        AS first_seen,
          (SELECT max(published_at)::date FROM cur)                        AS last_seen,
          (SELECT count(*)::int FROM hit WHERE in_prev)                    AS prev_docs,
          (SELECT json_agg(x) FROM (
              SELECT date_trunc('{bucket}', published_at)::date AS bucket,
                     count(*)::int AS docs
              FROM cur GROUP BY 1 ORDER BY 1) x)                           AS series,
          (SELECT json_agg(x) FROM (
              SELECT product AS key, count(*)::int AS docs FROM cur
              GROUP BY 1 ORDER BY 2 DESC LIMIT :top_n) x)                  AS top_product,
          (SELECT json_agg(x) FROM (
              SELECT platform AS key, count(*)::int AS docs FROM cur
              GROUP BY 1 ORDER BY 2 DESC LIMIT :top_n) x)                  AS top_platform,
          (SELECT json_agg(x) FROM (
              SELECT COALESCE(country_code,'??') AS key, count(*)::int AS docs FROM cur
              GROUP BY 1 ORDER BY 2 DESC LIMIT :top_n) x)                  AS top_country
    """)
    row = (await execute(stmt, params)).mappings().first()

    c = dict(row or {})
    docs = c.get("docs") or 0
    prev = c.get("prev_docs") or 0
    tops = {}
    for axis in ("product", "platform", "country"):
        rows = c.get(f"top_{axis}") or []
        for r in rows:
            r["share_pct"] = round(100.0 * r["docs"] / docs, 1) if docs else 0.0
        tops[axis] = rows
    return {
        "keyword": keyword, "days": days, "match": mode, "filters": filters,
        "total": {
            "docs": docs,
            "uniq_urls": c.get("uniq_urls"),
            "platforms": c.get("platforms"),
            "countries": c.get("countries"),
            "negative": c.get("negative"),
            "negative_pct": round(100.0 * (c.get("negative") or 0) / docs, 1) if docs else 0.0,
            "avg_sentiment": c.get("avg_sentiment"),
            "first_seen": c["first_seen"].isoformat() if c.get("first_seen") else None,
            "last_seen": c["last_seen"].isoformat() if c.get("last_seen") else None,
        },
        "vs_previous_period": {
            "prev_docs": prev,
            "delta_pct": round(100.0 * (docs - prev) / prev, 1) if prev else None,
        },
        "series": c.get("series") or [],
        "top": tops,
        "note": ("uniq_urls 가 docs 보다 크게 작으면 같은 글의 페이지 분할 복제다. "
                 "platforms 가 1~2 면 한 커뮤니티의 반향이라 일반화하면 안 된다."),
    }


async def period_compare(
    execute, days: int = 30, by: str = "product", limit: int = 15, keyword: Optional[str] = None,
    **filters,
) -> dict:
    """최근 N일 vs 직전 N일 — 무엇이 늘고 줄었나.

    "의미 있는 기간에 따른 지표"의 핵심. 절대 건수만으로는 변화를 못 본다.
    """
    axes = {
        "product":  "COALESCE(p.code,'(untagged)')",
        "brand":    "COALESCE(p.brand,'(untagged)')",
        "category": "COALESCE(p.category,'(untagged)')",
        "country":  "COALESCE(v.country_code,'??')",
        "platform": "COALESCE(pl.code,'unknown')",
    }
    expr = axes.get(by, axes["product"])
    base_conds, params, mode = _slice_filters(keyword=keyword, **filters)
    params.update({"d1": days, "d2": days * 2, "limit": limit})
    where = " AND ".join(base_conds)
    stmt = text(f"""
        WITH cur AS (
            SELECT {expr} AS key, count(*)::int AS n
            {_SLICE_JOINS}
            WHERE {where}
              AND v.published_at >= NOW() - make_interval(days => :d1)
              AND v.published_at <= NOW()
            GROUP BY 1
        ), prev AS (
            SELECT {expr} AS key, count(*)::int AS n
            {_SLICE_JOINS}
            WHERE {where}
              AND v.published_at >= NOW() - make_interval(days => :d2)
              AND v.published_at <  NOW() - make_interval(days => :d1)
            GROUP BY 1
        )
        SELECT COALESCE(cur.key, prev.key) AS key,
               COALESCE(cur.n,0) AS current, COALESCE(prev.n,0) AS previous,
               COALESCE(cur.n,0) - COALESCE(prev.n,0) AS delta,
               CASE WHEN COALESCE(prev.n,0) = 0 THEN NULL
                    ELSE round(100.0*(COALESCE(cur.n,0)-prev.n)/prev.n, 1)::float
               END AS delta_pct
        FROM cur FULL OUTER JOIN prev ON cur.key = prev.key
        ORDER BY abs(COALESCE(cur.n,0) - COALESCE(prev.n,0)) DESC
        LIMIT :limit
    """)
    rows = (await execute(stmt, params)).mappings().all()
    data = [dict(r) for r in rows]
    return {
        "by": by, "days": days, "keyword": keyword, "match": mode, "filters": filters,
        "rows": data,
        "risers": [r for r in data if r["delta"] > 0][:5],
        "fallers": [r for r in data if r["delta"] < 0][:5],
        "note": ("delta_pct 가 null 이면 직전 기간 0건이라 비율을 낼 수 없다(신규 등장). "
                 "수집 깊이가 기간마다 다를 수 있으므로 절대 증감과 함께 보라."),
    }
