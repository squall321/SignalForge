# 제품×부품×증상 단위 결함 급등 탐지 — 라이프사이클 정규화 + 소스 집중도 가중
"""
결함 이상탐지 (defect_anomaly).

기존 알림은 전부 플랫폼/시스템 축이었고 **제품·결함 축의 통계적 급등 탐지가 없었다**
(제품 축은 고정 +30%p 같은 하드 임계값뿐). 이 모듈이 그 공백을 메운다.

설계 근거 (2026-09-06 실측 조사)
- **점유율(share) 비교, 절대건수 아님.** 폴드7 의 주차별 표본이 24~57건인데 폴드8 은
  수천건이다. 코퍼스 수집 깊이가 세대마다 달라 절대건수 비교는 무효다.
  share = 해당 결함 건수 / 같은 창의 그 제품 전체 VOC.
- **신제품은 이전 세대의 같은 라이프사이클 구간을 baseline 으로.** 출시 직후엔 자기
  과거가 없고, 신제품은 원래 급등한다("폴드8 +195%" 의 대부분이 출시 효과였다).
  products.predecessor_code + released_at(0027/0031)으로 동일 주차 구간을 잡는다.
- **소스 집중도 가드.** 진짜 증폭은 매체 복제가 아니라 단일 커뮤니티 쏠림이었다
  (voc_defects 의 33%가 hackernews 단독, GS26U burn_in 의 79%가 dcinside).
  유효 플랫폼 수 = 1/HHI 가 낮으면 한 커뮤니티의 잡담이므로 알리지 않는다.
- **published_at 축.** collected_at 일별은 백필 일정이 만든 인공 스파이크다.

저장은 살아 있는 collection_health 패턴을 따른다 — alert_rules 에서 임계·cooldown 만
읽고 alert_events 에 직접 INSERT. slack_notifier(5분 주기)가 자동으로 송출한다.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)

ALERT_RULE_NAME = "defect_anomaly"
DEFAULT_COOLDOWN_SEC = 21600          # 6h — 같은 결함으로 반복 알림 방지

# 창 크기는 실측으로 정했다. 7일/14일 창에서는 (제품×부품×증상) 조합이 min_count 를
# 넘는 경우가 9~34개뿐이라 사실상 탐지가 불가능하다(결함행의 31%만 제품 링크를 가짐).
# 28일 창에서 평가대상 72개가 되어 유의미해진다.
RECENT_DAYS = 28                      # 관측 창
BASELINE_DAYS = 28                    # 직전 동일 길이 창(성숙 제품 baseline)
NEW_PRODUCT_DAYS = 120                # 이 기간 내 출시면 '신제품' → 세대 비교
MIN_RECENT_COUNT = 12                 # Poisson z 가 의미를 갖는 최소 사건 수
MIN_EFF_PLATFORMS = 2.0               # 유효 플랫폼 수(1/HHI) 하한 — 단일 커뮤니티 배제
RATIO_THRESHOLD = 2.0                 # share 배수 하한
Z_THRESHOLD = 3.0                     # Poisson 근사 z 하한
SEVERITY_ESCALATE = {"safety", "non_functional"}   # 이 심각도면 알림 등급 상향


def _dsn() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return url.replace("postgresql+asyncpg://", "postgresql://")
    pw = os.getenv("PGPASSWORD", os.getenv("POSTGRES_PASSWORD", "signalforge_pass"))
    return (f"postgresql://{os.getenv('POSTGRES_USER', 'signalforge')}:{pw}"
            f"@{os.getenv('POSTGRES_HOST', '127.0.0.1')}:{os.getenv('POSTGRES_PORT', '5434')}"
            f"/{os.getenv('POSTGRES_DB', 'signalforge')}")


# 최근 창의 (제품×부품×증상) 집계 + 플랫폼 분포로 유효 플랫폼 수까지 한 번에.
# voc_product_links 기반이라 비교글 언급도 포함된다(Phase 1).
_RECENT_SQL = """
WITH recent AS (
    SELECT p.code AS product_code, p.id AS product_id,
           d.component, d.symptom, d.severity,
           v.platform_id, v.id AS voc_id
    FROM voc_defects d
    JOIN voc_records v       ON v.id = d.voc_id
    JOIN voc_product_links l ON l.voc_id = d.voc_id
    JOIN products p          ON p.id = l.product_id
    WHERE v.archived_at IS NULL
      AND v.published_at >= now() - ($1::int || ' days')::interval
      AND v.published_at <= now()
),
combo AS (
    -- pc 는 플랫폼별 distinct voc 수. voc 는 플랫폼 하나에만 속하므로 sum(pc)=총건수.
    -- max(severity): 사전순이 cosmetic<degraded<non_functional<safety 라 가장 심각한
    -- 등급이 선택된다(등급 이름을 바꾸면 이 성질이 깨지니 주의).
    SELECT product_code, product_id, component, symptom,
           max(severity) AS severity,
           sum(pc)::int AS cnt,
           sum(pc * pc)::float / NULLIF(power(sum(pc), 2), 0) AS hhi
    FROM (
        SELECT product_code, product_id, component, symptom, severity,
               platform_id, count(DISTINCT voc_id)::float AS pc
        FROM recent
        GROUP BY 1,2,3,4,5,6
    ) t
    GROUP BY 1,2,3,4
)
SELECT * FROM combo WHERE cnt >= $2
"""

# 제품 단위 전체 VOC(분모) — 같은 창
_PRODUCT_TOTAL_SQL = """
SELECT p.code AS product_code, count(DISTINCT v.id) AS total
FROM voc_records v
JOIN voc_product_links l ON l.voc_id = v.id
JOIN products p          ON p.id = l.product_id
WHERE v.archived_at IS NULL
  AND v.published_at >= now() - ($1::int || ' days')::interval
  AND v.published_at <= now()
GROUP BY 1
"""

# 자기 과거 baseline (성숙 제품) — 최근 창 직전 BASELINE_DAYS
_OWN_BASELINE_SQL = """
WITH win AS (
    SELECT v.id AS voc_id, d.component, d.symptom
    FROM voc_defects d
    JOIN voc_records v       ON v.id = d.voc_id
    JOIN voc_product_links l ON l.voc_id = d.voc_id
    WHERE l.product_id = $1
      AND v.archived_at IS NULL
      AND v.published_at >= now() - (($2::int + $3::int) || ' days')::interval
      AND v.published_at <  now() - ($2::int || ' days')::interval
),
tot AS (
    SELECT count(DISTINCT v.id) AS total
    FROM voc_records v
    JOIN voc_product_links l ON l.voc_id = v.id
    WHERE l.product_id = $1
      AND v.archived_at IS NULL
      AND v.published_at >= now() - (($2::int + $3::int) || ' days')::interval
      AND v.published_at <  now() - ($2::int || ' days')::interval
)
SELECT (SELECT count(*) FROM win WHERE component = $4 AND symptom = $5) AS cnt,
       (SELECT total FROM tot) AS total
"""

# 세대 baseline (신제품) — 직전 세대의 **같은 라이프사이클 주차 구간**
_GEN_BASELINE_SQL = """
WITH pred AS (
    SELECT pp.id, pp.released_at
    FROM products cur
    JOIN products pp ON pp.code = cur.predecessor_code
    WHERE cur.id = $1 AND pp.released_at IS NOT NULL
),
win AS (
    SELECT v.id AS voc_id, d.component, d.symptom
    FROM pred
    JOIN voc_product_links l ON l.product_id = pred.id
    JOIN voc_records v       ON v.id = l.voc_id
    LEFT JOIN voc_defects d  ON d.voc_id = v.id
    WHERE v.archived_at IS NULL
      AND v.published_at >= pred.released_at + ($2::int || ' days')::interval
      AND v.published_at <  pred.released_at + ($3::int || ' days')::interval
)
SELECT count(*) FILTER (WHERE component = $4 AND symptom = $5) AS cnt,
       count(DISTINCT voc_id) AS total
FROM win
"""


async def _fetch_baseline(conn, row, days_since_release: Optional[int]) -> Dict[str, Any]:
    """이 조합의 baseline share 를 구한다. 신제품이면 세대 비교, 아니면 자기 과거."""
    is_new = days_since_release is not None and days_since_release <= NEW_PRODUCT_DAYS
    if is_new:
        # 신제품의 최근 창을 출시 후 [d-RECENT, d) 구간으로 보고 이전 세대의 동일 구간과 비교
        lo = max(0, days_since_release - RECENT_DAYS)
        gen = await conn.fetchrow(_GEN_BASELINE_SQL, int(row["product_id"]),
                                  lo, int(days_since_release),
                                  row["component"], row["symptom"])
        if gen and (gen["total"] or 0) > 0:
            return {"mode": "lifecycle", "cnt": int(gen["cnt"] or 0),
                    "total": int(gen["total"] or 0)}
    own = await conn.fetchrow(_OWN_BASELINE_SQL, int(row["product_id"]),
                              RECENT_DAYS, BASELINE_DAYS,
                              row["component"], row["symptom"])
    return {"mode": "history", "cnt": int((own and own["cnt"]) or 0),
            "total": int((own and own["total"]) or 0)}


async def evaluate(conn, ratio_threshold: Optional[float] = None) -> List[Dict[str, Any]]:
    """급등 조합 목록을 반환. DB 쓰기는 하지 않는다(테스트 용이).

    ratio_threshold 를 주면 그 값을 쓴다 — 운영자가 alert_rules.threshold 를
    UI(PATCH /alerts/rules)에서 조정하면 코드 배포 없이 민감도를 바꿀 수 있다."""
    thr = float(ratio_threshold if ratio_threshold else RATIO_THRESHOLD)
    totals = {r["product_code"]: int(r["total"])
              for r in await conn.fetch(_PRODUCT_TOTAL_SQL, RECENT_DAYS)}
    rel = {r["id"]: r["days"] for r in await conn.fetch("""
        SELECT id, (now()::date - released_at)::int AS days
        FROM products WHERE released_at IS NOT NULL
    """)}

    rows = await conn.fetch(_RECENT_SQL, RECENT_DAYS, MIN_RECENT_COUNT)
    out: List[Dict[str, Any]] = []
    for row in rows:
        total_recent = totals.get(row["product_code"], 0)
        if total_recent <= 0:
            continue
        cnt = int(row["cnt"])
        recent_share = cnt / total_recent

        # 단일 커뮤니티 쏠림 배제 — 유효 플랫폼 수 = 1/HHI
        hhi = float(row["hhi"] or 1.0)
        eff_platforms = (1.0 / hhi) if hhi > 0 else 1.0
        if eff_platforms < MIN_EFF_PLATFORMS:
            continue

        base = await _fetch_baseline(conn, row, rel.get(row["product_id"]))
        if base["total"] <= 0:
            continue
        base_share = base["cnt"] / base["total"]
        # baseline 이 0 이면 '한 건도 없던 것이 나타남' — 바닥값으로 대체해 배수를 유한하게
        floor_share = 1.0 / max(base["total"], 1)
        eff_base_share = max(base_share, floor_share)

        ratio = recent_share / eff_base_share
        expected = eff_base_share * total_recent
        z = (cnt - expected) / (expected ** 0.5) if expected > 0 else 0.0
        if ratio < thr or z < Z_THRESHOLD:
            continue

        severity = ("critical" if row["severity"] in SEVERITY_ESCALATE else "warning")
        out.append({
            "metric": f"defect:{row['product_code']}:{row['component']}:{row['symptom']}",
            "product_code": row["product_code"],
            "component": row["component"],
            "symptom": row["symptom"],
            "defect_severity": row["severity"],
            "severity": severity,
            "recent_count": cnt,
            "recent_share": round(recent_share, 5),
            "baseline_share": round(base_share, 5),
            "baseline_mode": base["mode"],
            "baseline_total": base["total"],
            "ratio": round(ratio, 2),
            "z": round(z, 2),
            "eff_platforms": round(eff_platforms, 2),
            "value": round(ratio, 2),
            "threshold": thr,
            "reason": (f"{row['product_code']} {row['component']}/{row['symptom']} "
                       f"최근 {RECENT_DAYS}일 점유율 {recent_share:.2%} "
                       f"(baseline {base_share:.2%}, {base['mode']}) "
                       f"{ratio:.1f}배·z={z:.1f}·유효플랫폼 {eff_platforms:.1f}"),
        })
    out.sort(key=lambda v: v["ratio"], reverse=True)
    return out


async def insert_alert_events(conn, violations: List[Dict[str, Any]]) -> Dict[str, int]:
    """위반당 1행 INSERT. metric 단위 cooldown (collection_health 와 동일 패턴)."""
    if not violations:
        return {"inserted": 0, "skipped_cooldown": 0, "rule_missing": 0}

    rule = await conn.fetchrow(
        "SELECT id, severity, threshold, cooldown_sec FROM alert_rules "
        "WHERE name = $1 AND is_active = TRUE", ALERT_RULE_NAME)
    if rule is None:
        logger.info("[defect_anomaly] alert_rules.%s 없음 — INSERT skip (graceful)",
                    ALERT_RULE_NAME)
        return {"inserted": 0, "skipped_cooldown": 0, "rule_missing": len(violations)}

    cooldown_sec = int(rule["cooldown_sec"] or DEFAULT_COOLDOWN_SEC)
    now = datetime.now(timezone.utc)
    inserted = skipped = 0

    for v in violations:
        last_fired = await conn.fetchval(
            "SELECT max(fired_at) FROM alert_events "
            "WHERE rule_id = $1 AND payload->>'metric' = $2",
            int(rule["id"]), v["metric"])
        if last_fired is not None and (now - last_fired).total_seconds() < cooldown_sec:
            skipped += 1
            continue
        try:
            await conn.execute(
                """
                INSERT INTO alert_events
                    (rule_id, severity, value, threshold, payload, dispatched_channels)
                VALUES ($1, $2, $3, $4, $5::jsonb, ARRAY[]::varchar[])
                """,
                int(rule["id"]), v["severity"], float(v["value"]),
                float(v["threshold"]),
                json.dumps({"type": "defect_anomaly", **{
                    k: v[k] for k in (
                        "metric", "product_code", "component", "symptom",
                        "defect_severity", "recent_count", "recent_share",
                        "baseline_share", "baseline_mode", "ratio", "z",
                        "eff_platforms", "reason")
                }}, ensure_ascii=False),
            )
            inserted += 1
        except Exception as exc:
            logger.warning("[defect_anomaly] INSERT 실패 (%s): %s", v["metric"], exc)

    return {"inserted": inserted, "skipped_cooldown": skipped, "rule_missing": 0}


async def run(dsn: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    conn = await asyncpg.connect(dsn or _dsn())
    try:
        rule_thr = await conn.fetchval(
            "SELECT threshold FROM alert_rules WHERE name = $1 AND is_active = TRUE",
            ALERT_RULE_NAME)
        violations = await evaluate(conn, rule_thr)
        if dry_run:
            return {"status": "ok", "dry_run": True, "violations": violations}
        res = await insert_alert_events(conn, violations)
    finally:
        await conn.close()
    logger.info("[defect_anomaly] 위반 %d건 → %s", len(violations), res)
    return {"status": "ok", "violations": len(violations),
            "top": violations[:5], **res}
