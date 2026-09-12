"""MCP Defect Tools — 구조화 결함(부품·증상·심각도)·급등 탐지·세대 비교 노출.

voc_defects / voc_product_links / v_voc_lifecycle 은 적재만 돼 있고 API·MCP 표면이
전혀 없었다(참조 0건). 이 모듈이 에이전트 소비 경로를 연다.

- 제품 링크는 voc_product_links 기반이라 "S26U vs Fold8" 비교글도 양쪽 신호로 잡힌다.
- 세대 비교는 released_at + predecessor_code 로 **동일 라이프사이클 주차**를 맞춘다.
  신제품은 원래 출시 직후 급등하므로 자기 과거가 아니라 이전 세대와 비교해야 한다.
"""
from typing import Optional

from db import get_db_session
from sqlalchemy import text



# ── 공통 필터 ────────────────────────────────────────────────────────────
# 시나리오 도출은 "어느 축으로든 잘라볼 수 있어야" 성립한다. 기존에는 부품·심각도
# 두 축뿐이라 '증상별로', '한국에서만', '1인칭 제보만' 같은 질문에 답할 수 없었다.
# 아래 축을 모든 결함 도구가 공유한다.
_FILTER_SPECS = [
    ("product_code", "p.code = :product_code", lambda v: v.upper()),
    ("component",    "d.component = :component", None),
    ("symptom",      "d.symptom = :symptom", None),
    ("severity",     "d.severity = :severity", None),
    ("modality",     "d.modality = :modality", None),
    ("brand",        "p.brand = :brand", lambda v: v.lower()),
    ("category",     "p.category = :category", lambda v: v.lower()),
    ("country",      "v.country_code = :country", lambda v: v.upper()),
    ("platform",     "pl.code = :platform", None),
]


def _build_filters(period_days: Optional[int] = None, **kw):
    """(conditions, params) — None 인 필터는 건너뛴다."""
    conds = ["v.archived_at IS NULL"]
    params: dict = {}
    if period_days:
        conds.append("v.published_at >= NOW() - make_interval(days => :days)")
        conds.append("v.published_at <= NOW()")   # 미래 발행일 방어(0042 참조)
        params["days"] = period_days
    for name, clause, norm in _FILTER_SPECS:
        val = kw.get(name)
        if val is None or val == "":
            continue
        conds.append(clause)
        params[name] = norm(val) if norm else val
    return conds, params


_BASE_JOINS = """
        FROM voc_defects d
        JOIN voc_records v       ON v.id = d.voc_id
        JOIN voc_product_links l ON l.voc_id = d.voc_id
        JOIN products p          ON p.id = l.product_id
        LEFT JOIN platforms pl   ON pl.id = v.platform_id
"""

async def defect_profile_tool(
    product_code: Optional[str] = None,
    component: Optional[str] = None,
    severity: Optional[str] = None,
    period_days: int = 90,
    limit: int = 20,
    symptom: Optional[str] = None,
    modality: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    country: Optional[str] = None,
    platform: Optional[str] = None,
) -> dict:
    """제품·부품·증상별 결함 프로파일. 소스 다양성(유효 플랫폼 수)까지 함께 준다.

    유효 플랫폼 수 = 1/HHI. 낮으면 한 커뮤니티의 반향이라 신뢰도가 떨어진다.
    필터 축은 다른 결함 도구와 동일하다(조합해서 같은 슬라이스를 여러 관점으로 본다).
    """
    conds, params = _build_filters(
        period_days, product_code=product_code, component=component,
        severity=severity, symptom=symptom, modality=modality, brand=brand,
        category=category, country=country, platform=platform)
    params["limit"] = limit
    where = " AND ".join(conds)

    stmt = text(f"""
        WITH base AS (
            SELECT p.code AS product_code, d.component, d.symptom, d.severity,
                   v.platform_id, count(DISTINCT v.id)::float AS pc
            {_BASE_JOINS}
            WHERE {where}
            GROUP BY 1,2,3,4,5
        )
        SELECT product_code, component, symptom, severity,
               sum(pc)::int AS count,
               count(*)::int AS platforms,
               round((NULLIF(power(sum(pc),2),0) / NULLIF(sum(pc*pc),0))::numeric, 2)::float
                   AS effective_platforms
        FROM base
        GROUP BY 1,2,3,4
        ORDER BY count DESC
        LIMIT :limit
    """)
    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
    return {
        "period_days": period_days,
        "filters": {"product_code": product_code, "component": component,
                    "severity": severity, "symptom": symptom, "modality": modality,
                    "brand": brand, "category": category, "country": country,
                    "platform": platform},
        "defects": [dict(r) for r in rows],
        "note": "effective_platforms 가 2 미만이면 단일 커뮤니티 쏠림 — 신뢰도 낮음",
    }


async def defect_anomalies_tool(limit: int = 20, severity: Optional[str] = None) -> dict:
    """탐지기(defect_anomaly)가 최근 발화한 결함 급등 목록."""
    conds = ["payload->>'type' = 'defect_anomaly'"]
    params = {"limit": limit}
    if severity:
        conds.append("severity = :severity")
        params["severity"] = severity
    stmt = text(f"""
        SELECT fired_at, severity,
               payload->>'product_code'  AS product_code,
               payload->>'component'     AS component,
               payload->>'symptom'       AS symptom,
               payload->>'defect_severity' AS defect_severity,
               (payload->>'recent_count')::int    AS recent_count,
               (payload->>'ratio')::float         AS ratio,
               (payload->>'z')::float             AS z,
               (payload->>'eff_platforms')::float AS effective_platforms,
               payload->>'baseline_mode' AS baseline_mode,
               payload->>'reason'        AS reason
        FROM alert_events
        WHERE {" AND ".join(conds)}
        ORDER BY fired_at DESC
        LIMIT :limit
    """)
    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
    return {
        "anomalies": [dict(r) for r in rows],
        "note": ("baseline_mode=lifecycle 은 이전 세대의 동일 출시후 구간과 비교한 것 "
                 "— 출시 효과를 제거한 값이다"),
    }


async def lifecycle_compare_tool(
    product_code: str, max_week: int = 12, defect_only: bool = False,
) -> dict:
    """제품과 **직전 세대**를 동일 라이프사이클 주차로 비교.

    신제품 급등이 진짜 이상인지, 원래 그런 것(출시 효과)인지 가르는 용도다.
    """
    stmt = text("""
        WITH cur AS (SELECT id, code, predecessor_code FROM products WHERE code = :code),
        pair AS (
            SELECT cur.code AS cur_code, pp.code AS prev_code
            FROM cur LEFT JOIN products pp ON pp.code = cur.predecessor_code
        ),
        agg AS (
            SELECT lc.product_code, lc.lifecycle_week AS week,
                   count(*)::int AS total,
                   count(*) FILTER (WHERE lc.sentiment_label = 'negative')::int AS negative,
                   count(*) FILTER (WHERE d.voc_id IS NOT NULL)::int AS with_defect
            FROM v_voc_lifecycle lc
            LEFT JOIN voc_defects d ON d.voc_id = lc.voc_id
            WHERE lc.product_code IN (SELECT cur_code FROM pair
                                      UNION SELECT prev_code FROM pair)
              AND lc.lifecycle_week BETWEEN 0 AND :max_week
            GROUP BY 1,2
        )
        SELECT (SELECT cur_code FROM pair) AS product_code,
               (SELECT prev_code FROM pair) AS predecessor_code,
               a.product_code AS series, a.week, a.total, a.negative, a.with_defect,
               round(100.0 * a.negative / NULLIF(a.total,0), 1) AS negative_pct,
               round(100.0 * a.with_defect / NULLIF(a.total,0), 1) AS defect_pct
        FROM agg a ORDER BY a.week, a.product_code
    """)
    async with get_db_session() as db:
        rows = (await db.execute(stmt, {"code": product_code.upper(),
                                        "max_week": max_week})).mappings().all()
    data = [dict(r) for r in rows]
    if defect_only:
        data = [r for r in data if (r.get("with_defect") or 0) > 0]
    pred = data[0]["predecessor_code"] if data else None
    return {
        "product_code": product_code.upper(),
        "predecessor_code": pred,
        "weeks": data,
        "note": ("표본이 작은 주차는 신뢰도가 낮다 — 과거 세대일수록 수집 깊이가 얕아 "
                 "주차별보다 누적 비교가 안정적이다"),
    }


# ── 시나리오 도출 도구 ───────────────────────────────────────────────────
# 결함 "시나리오"는 숫자 하나로 서지 않는다. 다음이 모여야 이야기가 된다 —
#   무엇이(제품×부품×증상) · 언제부터(시계열) · 누가(출처·양상·국가) ·
#   무엇과 함께(동시발생) · 출시 후 얼마나 일찍(경과일) · 실제로 뭐라 하는지(원문)
# 아래 도구는 모두 같은 필터 축을 받아 **조합 가능**하도록 설계했다.

async def defect_timeline_tool(
    interval: str = "week", period_days: int = 180, **filters,
) -> dict:
    """결함 슬라이스의 시계열 — 언제 시작됐고 가속 중인지.

    단일 숫자로는 '원래 그런 것'과 '새로 터진 것'을 가를 수 없다.
    """
    bucket = {"day": "day", "week": "week", "month": "month"}.get(interval, "week")
    conds, params = _build_filters(period_days, **filters)
    params["bucket_unused"] = None
    params.pop("bucket_unused")
    stmt = text(f"""
        SELECT date_trunc('{bucket}', v.published_at)::date AS bucket,
               count(DISTINCT d.voc_id)::int AS docs,
               count(DISTINCT split_part(v.source_url, '?', 1))::int AS uniq_urls,
               count(DISTINCT pl.code)::int AS platforms,
               count(*) FILTER (WHERE d.modality = 'firsthand')::int AS firsthand
        {_BASE_JOINS}
        WHERE {" AND ".join(conds)}
        GROUP BY 1 ORDER BY 1
    """)
    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
    series = [dict(r) for r in rows]
    first_seen = series[0]["bucket"].isoformat() if series else None
    return {
        "interval": bucket, "period_days": period_days, "filters": filters,
        "first_seen": first_seen,
        "series": series,
        "note": ("uniq_urls 가 docs 보다 크게 작으면 같은 글의 페이지 분할 복제다. "
                 "firsthand 비중이 낮으면 전언·기사 반향이라 실제 고장률이 아니다."),
    }


async def defect_breakdown_tool(
    by: str = "platform", period_days: int = 90, limit: int = 20, **filters,
) -> dict:
    """결함 슬라이스를 임의 축으로 분해 — 누가 어디서 말하는가.

    by: platform | country | modality | severity | component | symptom |
        product | brand | category
    """
    axes = {
        "platform": ("COALESCE(pl.code,'unknown')", "platform"),
        "country":  ("COALESCE(v.country_code,'??')", "country"),
        "modality": ("COALESCE(d.modality,'unknown')", "modality"),
        "severity": ("d.severity", "severity"),
        "component": ("d.component", "component"),
        "symptom":  ("d.symptom", "symptom"),
        "product":  ("p.code", "product"),
        "brand":    ("p.brand", "brand"),
        "category": ("p.category", "category"),
    }
    expr, label = axes.get(by, axes["platform"])
    conds, params = _build_filters(period_days, **filters)
    params["limit"] = limit
    stmt = text(f"""
        SELECT {expr} AS key,
               count(DISTINCT d.voc_id)::int AS docs,
               count(*) FILTER (WHERE d.modality = 'firsthand')::int AS firsthand,
               round(100.0 * count(*) FILTER (WHERE d.modality='firsthand')
                     / NULLIF(count(*),0), 1)::float AS firsthand_pct
        {_BASE_JOINS}
        WHERE {" AND ".join(conds)}
        GROUP BY 1 ORDER BY docs DESC LIMIT :limit
    """)
    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
    return {"by": label, "period_days": period_days, "filters": filters,
            "rows": [dict(r) for r in rows]}


async def defect_cooccurrence_tool(
    period_days: int = 180, limit: int = 20, min_docs: int = 3, **filters,
) -> dict:
    """같은 글에서 함께 나타나는 결함 쌍 — 복합 고장 모드 발굴.

    'hinge/dust_ingress 와 display/line 이 같이 나온다'처럼 단일 증상 집계로는
    보이지 않는 연쇄를 드러낸다.
    """
    conds, params = _build_filters(period_days, **filters)
    params.update({"limit": limit, "min_docs": min_docs})
    stmt = text(f"""
        WITH scoped AS (
            SELECT DISTINCT d.voc_id, d.component, d.symptom
            {_BASE_JOINS}
            WHERE {" AND ".join(conds)}
        )
        SELECT a.component || '/' || a.symptom AS defect_a,
               b.component || '/' || b.symptom AS defect_b,
               count(DISTINCT a.voc_id)::int AS co_docs
        FROM scoped a JOIN scoped b
          ON a.voc_id = b.voc_id
         AND (a.component, a.symptom) < (b.component, b.symptom)
        GROUP BY 1,2
        HAVING count(DISTINCT a.voc_id) >= :min_docs
        ORDER BY co_docs DESC LIMIT :limit
    """)
    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
    return {"period_days": period_days, "filters": filters,
            "pairs": [dict(r) for r in rows],
            "note": "동시 등장이지 인과가 아니다. 한 글이 여러 불만을 나열한 것일 수 있다."}


async def defect_onset_tool(period_days: int = 730, **filters) -> dict:
    """출시 후 며칠에 보고되는가 — 초기불량과 마모고장을 가른다.

    released_at 이 있는 제품만 대상이다.
    """
    conds, params = _build_filters(period_days, **filters)
    conds.append("p.released_at IS NOT NULL")
    conds.append("v.published_at >= p.released_at")
    stmt = text(f"""
        WITH d0 AS (
            SELECT p.code AS product_code,
                   (v.published_at::date - p.released_at) AS days_since,
                   d.voc_id
            {_BASE_JOINS}
            WHERE {" AND ".join(conds)}
        )
        SELECT
            count(DISTINCT voc_id)::int AS docs,
            round(avg(days_since)::numeric, 1)::float AS avg_days,
            percentile_disc(0.25) WITHIN GROUP (ORDER BY days_since) AS p25_days,
            percentile_disc(0.50) WITHIN GROUP (ORDER BY days_since) AS median_days,
            percentile_disc(0.75) WITHIN GROUP (ORDER BY days_since) AS p75_days,
            count(*) FILTER (WHERE days_since <= 30)::int AS within_30d,
            count(*) FILTER (WHERE days_since > 365)::int AS after_1y
        FROM d0
    """)
    async with get_db_session() as db:
        row = (await db.execute(stmt, params)).mappings().first()
    out = dict(row) if row else {}
    out.update({"period_days": period_days, "filters": filters,
                "note": ("median 이 작고 within_30d 비중이 높으면 초기불량, "
                         "after_1y 가 크면 마모·열화 패턴이다. "
                         "다만 수집 시작(2026-05) 이전 출시 제품은 초기 구간이 "
                         "비어 있어 median 이 실제보다 크게 나온다.")})
    return out


async def defect_evidence_tool(
    period_days: int = 180, limit: int = 10, modality: Optional[str] = "firsthand",
    **filters,
) -> dict:
    """결함 슬라이스의 실제 사용자 발언 — 시나리오를 구체화하는 원문 근거.

    기본은 firsthand(자기 기기 실현 증상)만 — 전언·기사는 고장 서술이 아니다.
    """
    if modality:
        filters["modality"] = modality
    conds, params = _build_filters(period_days, **filters)
    params["limit"] = limit
    stmt = text(f"""
        SELECT DISTINCT ON (split_part(v.source_url, '?', 1))
               v.id, v.source_url, v.published_at, v.country_code,
               COALESCE(pl.code, 'unknown') AS platform,
               p.code AS product_code, d.component, d.symptom, d.severity, d.modality,
               v.sentiment_label,
               LEFT(COALESCE(v.content_translated, v.content_original), 400) AS snippet
        {_BASE_JOINS}
        WHERE {" AND ".join(conds)}
        ORDER BY split_part(v.source_url, '?', 1), v.published_at DESC
        LIMIT :limit
    """)
    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()
    return {"period_days": period_days, "filters": filters,
            "samples": [dict(r) for r in rows],
            "note": "source_url 기준 중복 제거 — 같은 글의 페이지 분할을 한 건으로 센다."}
