# 제품×부품×증상 단위 결함 급등 탐지 — 라이프사이클 정규화 + 독립 제보 기반 검정
"""
결함 이상탐지 (defect_anomaly).

기존 알림은 전부 플랫폼/시스템 축이라 제품·결함 축의 통계적 급등 탐지가 없었다.

**2026-09-06 적대적 검증에서 초판의 결함 7종이 확인되어 전면 수정했다.**
1. role 무관 조인 — voc_defects 는 제품 차원이 없어 mentioned/compared 링크에도
   결함이 붙었다. 인도 Galaxy **S26** 폭발 기사가 말미 한 줄 때문에 GS25P 의 fire
   근거 14건 중 6건을 차지했다. → role='primary' 로만 집계.
2. 유효 플랫폼 수(1/HHI) 가드가 정반대로 작동 — 뉴스 1건을 10개 매체가 받아쓰면
   분포가 넓어져 가드를 8배 여유로 통과했다. 막으려던 신디케이션을 오히려 통과시켰다.
   → platforms.kind(0034)로 **독립 제보(community/marketplace/official) 소스 수**를 본다.
3. 같은 글의 페이지 분할 복사본이 각각 1건으로 계수(quasarzone page=1/2/5).
   → 쿼리스트링 제거한 source_url 기준으로 dedup.
4. baseline 최소 표본 하한 부재 — baseline 1건/58문서로 ratio 3.15 가 나왔고
   ±1건이 발화 여부를 뒤집었다. → MIN_BASELINE_TOTAL 하한.
5. z=(cnt-expected)/sqrt(expected) 가 baseline 추정오차를 무시해 유의성을 과대평가.
   → **두 비율 검정**(pooled SE)으로 교체. 작은 baseline 은 SE 가 커져 자동으로 눌린다.
6. floor_share=1/base_total 이 baseline 0 조합을 무조건 통과시켰다(ratio 가 데이터가
   아니라 문서 수로 정해짐). → baseline 0 일 때만 rule of three(3/n) 상한 사용.
7. 최신성 조건 부재로 3주 전 종료된 사건이 28일 내내 재발화. → 최근 구간 활동 요구.
8. **양상 미구분** — "Hinge Concerns (Possible New Owner)" 같은 구매 전 우려와 iFixit
   분해 기사 재게시가 결함 건수를 채웠다. 라벨 표본에서 결함 문서의 firsthand 는 49.3%뿐.
   → voc_defects.modality(0036) 가 'firsthand' 인 것만 센다.

설계 유지
- **점유율(share) 비교** — 세대별 수집 깊이가 달라 절대건수 비교는 무효.
- **신제품은 이전 세대의 같은 라이프사이클 구간**이 baseline.
- **published_at 축** — collected_at 일별은 백필 일정이 만든 인공 스파이크다.

저장은 살아 있는 collection_health 패턴(alert_rules 에서 임계·cooldown 만 읽고
alert_events 직접 INSERT). slack_notifier 가 송출한다.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)

ALERT_RULE_NAME = "defect_anomaly"
DEFAULT_COOLDOWN_SEC = 86400          # 24h — beat(6h)보다 길어야 억제가 실제로 동작한다

# 창 크기는 실측으로 정했다. firsthand·primary·URL dedup 을 모두 적용하면 모집단이
# 크게 줄어 28일 창에서는 평가 대상이 2조합뿐이었다(사실상 탐지 불능). 56일에서 12조합.
# **민감도는 상류 커버리지에 종속된다** — 결함행의 31%만 제품 링크를 갖고 그중 57.5%만
# firsthand 다. 링크 커버리지와 추출 재현율이 오르면 창을 줄일 수 있다.
RECENT_DAYS = 56                      # 관측 창
BASELINE_DAYS = 56                    # 직전 동일 길이 창(성숙 제품 baseline)
NEW_PRODUCT_DAYS = 120                # 이 기간 내 출시면 '신제품' → 세대 비교
TAIL_DAYS = 7                         # 최신성 — 이 구간에 활동이 있어야 한다
MIN_RECENT_COUNT = 10                 # URL dedup·firsthand 후 최소 사건 수
MIN_TAIL_COUNT = 2                    # 최근 TAIL_DAYS 내 최소 건수(종료된 사건 배제)
MIN_BASELINE_TOTAL = 200              # baseline 모수 하한 — 미만이면 비교 불가로 skip
MIN_INDEP_SOURCES = 3                 # 독립 제보(비매체) 플랫폼 수 하한
RATIO_THRESHOLD = 2.0                 # share 배수 하한(운영자가 alert_rules 로 조정)
Z_THRESHOLD = 3.0                     # 두 비율 검정 z 하한
SEVERITY_ESCALATE = {"safety", "non_functional"}
# 독립 제보로 인정하는 플랫폼 종류. media/aggregator 는 한 사건의 복제일 수 있어 제외.
_INDEP_KINDS = ("community", "marketplace", "official")


def _two_prop_z(c1: int, n1: int, c2: int, n2: int) -> float:
    """두 비율 검정 z. baseline(n2)이 작으면 SE 가 커져 자동으로 유의성이 낮아진다."""
    if n1 <= 0 or n2 <= 0:
        return 0.0
    p1, p2 = c1 / n1, c2 / n2
    pooled = (c1 + c2) / (n1 + n2)
    if pooled <= 0 or pooled >= 1:
        return 0.0
    se = (pooled * (1 - pooled) * (1 / n1 + 1 / n2)) ** 0.5
    return (p1 - p2) / se if se > 0 else 0.0


def _dsn() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return url.replace("postgresql+asyncpg://", "postgresql://")
    pw = os.getenv("PGPASSWORD", os.getenv("POSTGRES_PASSWORD", "signalforge_pass"))
    return (f"postgresql://{os.getenv('POSTGRES_USER', 'signalforge')}:{pw}"
            f"@{os.getenv('POSTGRES_HOST', '127.0.0.1')}:{os.getenv('POSTGRES_PORT', '5434')}"
            f"/{os.getenv('POSTGRES_DB', 'signalforge')}")


# 최근 창의 (제품×부품×증상) 집계.
# role='primary' 로만 센다 — mentioned/compared 를 포함하면 남의 제품 사고가 귀속된다.
# 건수는 쿼리스트링 제거한 source_url 기준 distinct — 같은 글의 페이지 분할 복제 배제.
# 독립 제보 소스 수와 최근 TAIL 구간 활동도 함께 산출한다.
_RECENT_SQL = """
WITH recent AS (
    SELECT p.code AS product_code, p.id AS product_id,
           d.component, d.symptom, d.severity,
           split_part(v.source_url, '?', 1) AS url,
           pl.kind AS platform_kind, pl.code AS platform_code,
           v.published_at
    FROM voc_defects d
    JOIN voc_records v       ON v.id = d.voc_id
    JOIN voc_product_links l ON l.voc_id = d.voc_id AND l.role = 'primary'
    JOIN products p          ON p.id = l.product_id
    JOIN platforms pl        ON pl.id = v.platform_id
    WHERE v.archived_at IS NULL
      AND d.modality = 'firsthand'
      AND v.published_at >= now() - ($1::int || ' days')::interval
      AND v.published_at <= now()
)
SELECT product_code, product_id, component, symptom,
       max(severity) AS severity,
       count(DISTINCT url)::int AS cnt,
       count(DISTINCT platform_code) FILTER (WHERE platform_kind = ANY($3))::int
           AS indep_sources,
       count(DISTINCT url) FILTER (
           WHERE published_at >= now() - ($4::int || ' days')::interval)::int AS tail_cnt
FROM recent
GROUP BY 1,2,3,4
HAVING count(DISTINCT url) >= $2
"""

# 제품 단위 전체(분모) — 분자와 동일한 role·dedup 기준이어야 share 가 의미를 갖는다.
_PRODUCT_TOTAL_SQL = """
SELECT p.code AS product_code,
       count(DISTINCT split_part(v.source_url, '?', 1))::int AS total
FROM voc_records v
JOIN voc_product_links l ON l.voc_id = v.id AND l.role = 'primary'
JOIN products p          ON p.id = l.product_id
WHERE v.archived_at IS NULL
  AND v.published_at >= now() - ($1::int || ' days')::interval
  AND v.published_at <= now()
GROUP BY 1
"""

# 자기 과거 baseline (성숙 제품) — 최근 창 직전 BASELINE_DAYS, 동일 기준
_OWN_BASELINE_SQL = """
WITH win AS (
    SELECT split_part(v.source_url, '?', 1) AS url, d.component, d.symptom
    FROM voc_records v
    JOIN voc_product_links l ON l.voc_id = v.id AND l.role = 'primary'
    LEFT JOIN voc_defects d  ON d.voc_id = v.id AND d.modality = 'firsthand'
    WHERE l.product_id = $1
      AND v.archived_at IS NULL
      AND v.published_at >= now() - (($2::int + $3::int) || ' days')::interval
      AND v.published_at <  now() - ($2::int || ' days')::interval
)
SELECT count(DISTINCT url) FILTER (WHERE component = $4 AND symptom = $5)::int AS cnt,
       count(DISTINCT url)::int AS total
FROM win
"""

# 세대 baseline (신제품) — 직전 세대의 같은 라이프사이클 구간, 동일 기준
_GEN_BASELINE_SQL = """
WITH pred AS (
    SELECT pp.id, pp.released_at
    FROM products cur
    JOIN products pp ON pp.code = cur.predecessor_code
    WHERE cur.id = $1 AND pp.released_at IS NOT NULL
),
win AS (
    SELECT split_part(v.source_url, '?', 1) AS url, d.component, d.symptom
    FROM pred
    JOIN voc_product_links l ON l.product_id = pred.id AND l.role = 'primary'
    JOIN voc_records v       ON v.id = l.voc_id
    LEFT JOIN voc_defects d  ON d.voc_id = v.id AND d.modality = 'firsthand'
    WHERE v.archived_at IS NULL
      AND v.published_at >= pred.released_at + ($2::int || ' days')::interval
      AND v.published_at <  pred.released_at + ($3::int || ' days')::interval
)
SELECT count(DISTINCT url) FILTER (WHERE component = $4 AND symptom = $5)::int AS cnt,
       count(DISTINCT url)::int AS total
FROM win
"""


async def _fetch_baseline(conn, row, days_since_release: Optional[int]) -> Dict[str, Any]:
    """baseline share 를 구한다. 신제품이면 세대 비교, 아니면 자기 과거."""
    is_new = days_since_release is not None and days_since_release <= NEW_PRODUCT_DAYS
    if is_new:
        lo = max(0, days_since_release - RECENT_DAYS)
        gen = await conn.fetchrow(_GEN_BASELINE_SQL, int(row["product_id"]),
                                  lo, int(days_since_release),
                                  row["component"], row["symptom"])
        if gen and (gen["total"] or 0) >= MIN_BASELINE_TOTAL:
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

    rows = await conn.fetch(_RECENT_SQL, RECENT_DAYS, MIN_RECENT_COUNT,
                            list(_INDEP_KINDS), TAIL_DAYS)
    out: List[Dict[str, Any]] = []
    for row in rows:
        total_recent = totals.get(row["product_code"], 0)
        if total_recent <= 0:
            continue
        cnt = int(row["cnt"])

        # 독립 제보 소스 하한 — 매체 신디케이션만으로는 알리지 않는다
        if int(row["indep_sources"]) < MIN_INDEP_SOURCES:
            continue
        # 최신성 — 이미 종료된 사건이 창에 남아 반복 발화하는 것 방지
        if int(row["tail_cnt"]) < MIN_TAIL_COUNT:
            continue

        base = await _fetch_baseline(conn, row, rel.get(row["product_id"]))
        if base["total"] < MIN_BASELINE_TOTAL:
            continue

        recent_share = cnt / total_recent
        base_share = base["cnt"] / base["total"]
        # baseline 0 이면 rule of three(3/n) 상한을 쓴다. 1/n floor 는 배수가 데이터가
        # 아니라 baseline 문서 수로 정해지는 인공물이었다.
        eff_base_share = base_share if base["cnt"] > 0 else 3.0 / base["total"]

        ratio = recent_share / eff_base_share if eff_base_share > 0 else 0.0
        z = _two_prop_z(cnt, total_recent, base["cnt"], base["total"])
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
            "recent_total": total_recent,
            "recent_share": round(recent_share, 5),
            "baseline_count": base["cnt"],
            "baseline_total": base["total"],
            "baseline_share": round(base_share, 5),
            "baseline_mode": base["mode"],
            "indep_sources": int(row["indep_sources"]),
            "tail_count": int(row["tail_cnt"]),
            "ratio": round(ratio, 2),
            "z": round(z, 2),
            "value": round(ratio, 2),
            "threshold": thr,
            "reason": (f"{row['product_code']} {row['component']}/{row['symptom']} "
                       f"최근 {RECENT_DAYS}일 {cnt}/{total_recent}건({recent_share:.2%}) "
                       f"vs baseline {base['cnt']}/{base['total']}({base_share:.2%}, "
                       f"{base['mode']}) — {ratio:.1f}배·z={z:.1f}·"
                       f"독립소스 {int(row['indep_sources'])}곳"),
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
                        "defect_severity", "recent_count", "recent_total",
                        "recent_share", "baseline_count", "baseline_total",
                        "baseline_share", "baseline_mode", "indep_sources",
                        "tail_count", "ratio", "z", "reason")
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
