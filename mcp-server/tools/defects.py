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


async def defect_profile_tool(
    product_code: Optional[str] = None,
    component: Optional[str] = None,
    severity: Optional[str] = None,
    period_days: int = 90,
    limit: int = 20,
) -> dict:
    """제품·부품·증상별 결함 프로파일. 소스 다양성(유효 플랫폼 수)까지 함께 준다.

    유효 플랫폼 수 = 1/HHI. 낮으면 한 커뮤니티의 반향이라 신뢰도가 떨어진다.
    """
    conds = ["v.archived_at IS NULL",
             "v.published_at >= NOW() - make_interval(days => :days)"]
    params = {"days": period_days, "limit": limit}
    if product_code:
        conds.append("p.code = :code")
        params["code"] = product_code.upper()
    if component:
        conds.append("d.component = :component")
        params["component"] = component
    if severity:
        conds.append("d.severity = :severity")
        params["severity"] = severity
    where = " AND ".join(conds)

    stmt = text(f"""
        WITH base AS (
            SELECT p.code AS product_code, d.component, d.symptom, d.severity,
                   v.platform_id, count(DISTINCT v.id)::float AS pc
            FROM voc_defects d
            JOIN voc_records v       ON v.id = d.voc_id
            JOIN voc_product_links l ON l.voc_id = d.voc_id
            JOIN products p          ON p.id = l.product_id
            WHERE {where}
            GROUP BY 1,2,3,4,5
        )
        SELECT product_code, component, symptom, severity,
               sum(pc)::int AS count,
               count(*)::int AS platforms,
               round((NULLIF(power(sum(pc),2),0) / NULLIF(sum(pc*pc),0))::numeric, 2)
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
                    "severity": severity},
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
