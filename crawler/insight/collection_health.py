"""수집 자동 모니터링 — 사이트 24h 수집 0건 시 자동 alert (R29 트랙 D).

목적
----
``operations_monitor`` 가 *서비스 SLO 6 metric* 의 임계 위반을 *실시간* 감지한다면,
이 모듈은 *수집 파이프라인 자체* — 각 활성 사이트의 24h 수집량을 점검한다.

매시 1회 (Celery beat) 호출되어 다음을 수행한다::

  1. is_active=TRUE 모든 platform 의 24h voc 카운트 + 직전 7일 일평균 베이스라인 산출
  2. critical : recent_24h == 0 AND prior_7d_avg_24h > 0   → 사이트 중단 의심
     warning  : recent_24h < prior_7d_avg_24h * 0.1 (단, recent_24h > 0)
                                                            → 평소의 10% 미만 수집
  3. 위반당 ``alert_events`` 1행 INSERT (rule ``collection_health``)
     metric 단위 cooldown 1h 적용 — 같은 사이트는 1h 내 중복 발화 없음
  4. reports/collection_health_YYYY-MM-DD.json 스냅샷 1개 적재 → ``/collection-monitor-history``
     endpoint 가 최근 N일 트렌드로 소비

베이스라인이 0 (= 평소 수집 자체가 없음) 인 사이트는 차단·미운영 사이트로 간주,
warning/critical 둘 다 발화하지 않는다 (이미 알고 있는 정보).

CLI::

    python -m insight.collection_health              # 1회 점검 + JSON stdout + 스냅샷 적재
    python -m insight.collection_health --no-insert  # alert_events INSERT 생략 (dry-run)
    python -m insight.collection_health --no-save    # 스냅샷 파일 저장 생략
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# crawler/ 를 sys.path 보장
_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

import asyncpg  # noqa: E402

logger = logging.getLogger(__name__)

REPO_ROOT = _CRAWLER_DIR.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "reports"
SNAPSHOT_PREFIX = "collection_health_"

# ── 임계 ───────────────────────────────────────────────────────────────────
THRESH_WARNING_RATIO = 0.10        # recent_24h < baseline_24h * 0.10 → warning
DEFAULT_COOLDOWN_SEC = 3600        # metric 단위 cooldown 1h
ALERT_RULE_NAME = "collection_health"


def _dsn() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        if url.startswith("postgresql+asyncpg://"):
            url = "postgresql://" + url[len("postgresql+asyncpg://"):]
        elif url.startswith("postgres+asyncpg://"):
            url = "postgres://" + url[len("postgres+asyncpg://"):]
        return url
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5434")
    user = os.getenv("POSTGRES_USER", "signalforge")
    pwd = os.getenv("POSTGRES_PASSWORD", "signalforge_pass")
    db = os.getenv("POSTGRES_DB", "signalforge")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"


# ── 사이트 통계 수집 ─────────────────────────────────────────────────────
async def collect_site_stats(conn: asyncpg.Connection) -> List[Dict[str, Any]]:
    """활성 사이트별 24h 수집 + 직전 7일 일평균 + 최근 collected_at.

    Returns
    -------
    list of dict ``{code, n_24h, baseline_24h_avg, last_collected, hours_since}``
    """
    rows = await conn.fetch(
        """
        SELECT p.code,
               count(v.id) FILTER (
                 WHERE v.collected_at >= NOW() - INTERVAL '24 hours'
               ) AS n_24h,
               count(v.id) FILTER (
                 WHERE v.collected_at >= NOW() - INTERVAL '8 days'
                   AND v.collected_at <  NOW() - INTERVAL '24 hours'
               ) AS n_prior7d,
               max(v.collected_at) AS last_collected
        FROM platforms p
        LEFT JOIN voc_records v ON v.platform_id = p.id
        WHERE p.is_active = TRUE
        GROUP BY p.code
        ORDER BY p.code
        """
    )
    now = datetime.now(timezone.utc)
    out: List[Dict[str, Any]] = []
    for r in rows:
        last = r["last_collected"]
        hours_since: Optional[float]
        if last is None:
            hours_since = None
        else:
            hours_since = round((now - last).total_seconds() / 3600.0, 2)
        n_24h = int(r["n_24h"] or 0)
        n_prior7d = int(r["n_prior7d"] or 0)
        # baseline = 직전 7일 일평균
        baseline_avg = round(n_prior7d / 7.0, 3)
        out.append({
            "code": r["code"],
            "n_24h": n_24h,
            "baseline_24h_avg": baseline_avg,
            "last_collected": last.isoformat() if last else None,
            "hours_since": hours_since,
        })
    return out


# ── 위반 평가 ─────────────────────────────────────────────────────────────
def evaluate_violations(stats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """사이트별 24h vs baseline 비교 → 위반 목록.

    Rules
    -----
    - baseline_24h_avg == 0  → skip (이미 운영 중단/차단된 사이트, 노이즈)
    - recent == 0 AND baseline > 0           → critical
    - 0 < recent < baseline * THRESH_WARNING_RATIO → warning
    """
    out: List[Dict[str, Any]] = []
    for s in stats:
        baseline = float(s.get("baseline_24h_avg") or 0.0)
        n_24h = int(s.get("n_24h") or 0)
        if baseline <= 0.0:
            continue
        if n_24h == 0:
            out.append({
                "code": s["code"],
                "metric": f"collection.{s['code']}",
                "severity": "critical",
                "value": 0.0,
                "threshold": baseline,
                "reason": (f"{s['code']}: 24h 수집 0건 (직전 7일 일평균 {baseline:.1f}건) "
                           f"— 마지막 수집 {s.get('hours_since')}h 전"),
            })
            continue
        if n_24h < baseline * THRESH_WARNING_RATIO:
            ratio_pct = (n_24h / baseline) * 100.0 if baseline > 0 else 0.0
            out.append({
                "code": s["code"],
                "metric": f"collection.{s['code']}",
                "severity": "warning",
                "value": float(n_24h),
                "threshold": round(baseline * THRESH_WARNING_RATIO, 3),
                "reason": (f"{s['code']}: 24h {n_24h}건 (직전 7일 일평균 {baseline:.1f}건) "
                           f"— 평소의 {ratio_pct:.1f}%"),
            })
    return out


# ── 무산출 실행 감지 (baseline 0 사각지대) ────────────────────────────────
#
# evaluate_violations 는 baseline_24h_avg <= 0 이면 "이미 운영 중단된 사이트"로 보고
# 건너뛴다. 그래서 소스가 죽으면 **7일간만** 알리고, 8일째부터는 직전 7일도 0이라
# baseline 이 0 이 되어 영구히 침묵한다. 실측(2026-09-09) — googlenews·appstore·recalls
# 가 12일, wpnews 18일, waybacknews 11일 무유입이었는데 알림이 하나도 없었다.
#
# 이제 crawl_jobs 에 실행 기록이 남으므로 "은퇴한 소스"와 "고장난 소스"를 구별할 수 있다.
# **beat 가 계속 실행하는데 산출이 0** 이면 은퇴가 아니라 고장이다.
ZERO_YIELD_MIN_RUNS = 3        # 이만큼 돌고도 0건이면 고장으로 본다
ZERO_YIELD_WINDOW_H = 48


async def collect_zero_yield(conn: asyncpg.Connection) -> List[Dict[str, Any]]:
    """활성 사이트 중 최근 실행은 있는데 산출이 0인 것 — 고장난 소스."""
    rows = await conn.fetch(
        f"""
        SELECT p.code,
               count(j.id) AS runs,
               count(j.id) FILTER (WHERE j.status = 'failed') AS failed,
               count(j.id) FILTER (WHERE j.error_message LIKE 'blocked:%') AS blocked,
               coalesce(sum(j.items_fetched), 0) AS fetched,
               count(j.id) FILTER (WHERE j.items_fetched IS NOT NULL) AS fetch_known,
               max(j.error_message) FILTER (WHERE j.error_message LIKE 'blocked:%')
                 AS blocked_detail,
               coalesce(sum(j.items_collected), 0) AS items
        FROM platforms p
        JOIN crawl_jobs j ON j.platform_id = p.id
        WHERE p.is_active = TRUE
          AND j.started_at >= NOW() - INTERVAL '{ZERO_YIELD_WINDOW_H} hours'
        GROUP BY p.code
        HAVING count(j.id) >= {ZERO_YIELD_MIN_RUNS}
           AND coalesce(sum(j.items_collected), 0) = 0
        ORDER BY p.code
        """
    )
    return [{"code": r["code"], "runs": int(r["runs"]),
             "failed": int(r["failed"]), "items": int(r["items"]),
             "blocked": int(r["blocked"] or 0),
             "blocked_detail": r["blocked_detail"],
             "fetched": int(r["fetched"] or 0),
             "fetch_known": int(r["fetch_known"] or 0)} for r in rows]


def evaluate_zero_yield(zy: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for z in zy:
        # **원인을 이름으로 부른다.** "N회 실패"로만 적으면 대응이 안 나온다 —
        # androidcentral 은 그렇게 35일간 리포트에 떠 있었는데 아무도 움직이지
        # 않았다. 실제 원인은 stile 챌린지 벽이었고 그건 재시도로 안 뚫린다.
        # **긁었는데 신규가 없는 것은 고장이 아니다.** 이 둘을 섞으면 오경보가
        # 진짜 장애를 묻는다 — ifixit 는 700건을 긁고 신규 0건인데 고장으로
        # 떴고, 정말로 막혀 있던 androidcentral 은 같은 문구에 섞여 35일간
        # 아무도 손대지 않았다. items_fetched 가 NULL 이면 "모름"이라 판단하지
        # 않는다(옛 행).
        if z.get("fetch_known") and z.get("fetched", 0) > 0 and not z.get("blocked"):
            continue
        if z.get("blocked"):
            detail = (z.get("blocked_detail") or "blocked").removeprefix("blocked:").strip()
            how = f"차단됨 — {detail}"
        elif z["failed"]:
            how = f"{z['failed']}회 실패"
        else:
            how = "전부 0건 반환"
        out.append({
            "code": z["code"],
            # metric 을 분리해야 collection.* 쿨다운과 섞이지 않는다
            "metric": f"collection.zero_yield.{z['code']}",
            "severity": "critical",
            "value": 0.0,
            "threshold": float(ZERO_YIELD_MIN_RUNS),
            "reason": (f"{z['code']}: 최근 {ZERO_YIELD_WINDOW_H}h 동안 "
                       f"{z['runs']}회 실행했으나 수집 0건 ({how}) "
                       + ("— 접근 방식을 바꿔야 한다(재시도로 안 뚫린다)"
                          if z.get("blocked") else "— 은퇴가 아니라 고장이다")),
        })
    return out


# ── alert_events INSERT (metric 단위 cooldown) ───────────────────────────
# ── 발행일 결측 감시 ──────────────────────────────────────────────────────
# 셀렉터 노후화는 **조용히** 진행된다. 사이트가 클래스명을 바꾸면 크롤러는
# 예외도 내지 않고 그 필드만 비운 채 계속 저장한다. 실측(2026-09-15) —
# dogdrip 은 `.comment-bar-author` → `.comment-bar` 변경을 못 따라가 넉 달 가까이
# 댓글의 **88%** 를 작성자 '익명' + 발행일 NULL 로 쌓았고, 실패 로그도 0건이었다.
# 수집량 지표로는 절대 안 보인다(건수는 정상이었다).
#
# 발행일이 없으면 그 글은 시계열 분석에서 통째로 빠지므로, 결측률 자체를 본다.
NULL_DATE_WARN = 0.25          # 최근 창의 25% 이상이 무날짜면 경고
NULL_DATE_CRIT = 0.60          # 60% 이상이면 심각 — 필드가 통째로 깨진 것이다
NULL_DATE_MIN_ROWS = 40        # 표본이 적으면 판단하지 않는다
NULL_DATE_WINDOW_H = 72


async def collect_null_dates(conn: asyncpg.Connection) -> List[Dict[str, Any]]:
    """최근 창에서 published_at 이 비어 저장된 비율."""
    rows = await conn.fetch(
        f"""
        SELECT p.code,
               count(*) AS rows_total,
               count(*) FILTER (WHERE v.published_at IS NULL) AS rows_null
        FROM voc_records v
        JOIN platforms p ON p.id = v.platform_id
        WHERE p.is_active = TRUE
          AND v.collected_at >= NOW() - INTERVAL '{NULL_DATE_WINDOW_H} hours'
        GROUP BY p.code
        HAVING count(*) >= {NULL_DATE_MIN_ROWS}
        ORDER BY p.code
        """
    )
    return [{"code": r["code"], "rows": int(r["rows_total"]),
             "null_rows": int(r["rows_null"]),
             "null_ratio": (int(r["rows_null"]) / int(r["rows_total"])
                            if r["rows_total"] else 0.0)} for r in rows]


def evaluate_null_dates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in items:
        ratio = it["null_ratio"]
        if ratio < NULL_DATE_WARN:
            continue
        sev = "critical" if ratio >= NULL_DATE_CRIT else "warning"
        out.append({
            "code": it["code"],
            "metric": f"collection.null_date.{it['code']}",
            "severity": sev,
            "value": round(ratio, 3),
            "threshold": NULL_DATE_CRIT if sev == "critical" else NULL_DATE_WARN,
            "reason": (f"{it['code']}: 최근 {NULL_DATE_WINDOW_H}h 저장 "
                       f"{it['rows']}건 중 {it['null_rows']}건({ratio:.0%})이 "
                       f"발행일 없음 — 목록/상세 셀렉터가 낡았는지 확인하라"
                       " (날짜 없는 글은 시계열에서 빠진다)"),
        })
    return out


async def insert_alert_events(
    conn: asyncpg.Connection,
    violations: List[Dict[str, Any]],
) -> Dict[str, int]:
    """위반당 1행 INSERT. metric 단위 cooldown 적용.

    rule(collection_health) 이 없으면 inserted=0 으로 graceful 반환.
    """
    if not violations:
        return {"inserted": 0, "skipped_cooldown": 0, "rule_missing": 0}

    rule = await conn.fetchrow(
        """
        SELECT id, severity, threshold, cooldown_sec
        FROM alert_rules
        WHERE name = $1 AND is_active = TRUE
        """,
        ALERT_RULE_NAME,
    )
    if rule is None:
        logger.info("[collection_health] alert_rules.%s 없음 — INSERT skip (graceful)",
                    ALERT_RULE_NAME)
        return {"inserted": 0, "skipped_cooldown": 0, "rule_missing": len(violations)}

    cooldown_sec = int(rule["cooldown_sec"] or DEFAULT_COOLDOWN_SEC)
    inserted = 0
    skipped = 0
    now = datetime.now(timezone.utc)

    for v in violations:
        metric_key = v.get("metric")
        # metric 단위 cooldown — payload->>'metric' 으로 직전 발화 검색
        last_fired = await conn.fetchval(
            """
            SELECT max(fired_at)
            FROM alert_events
            WHERE rule_id = $1 AND payload->>'metric' = $2
            """,
            int(rule["id"]),
            metric_key,
        )
        if last_fired is not None:
            sec_since = (now - last_fired).total_seconds()
            if sec_since < cooldown_sec:
                skipped += 1
                continue
        try:
            await conn.execute(
                """
                INSERT INTO alert_events
                    (rule_id, severity, value, threshold, payload, dispatched_channels)
                VALUES ($1, $2, $3, $4, $5::jsonb, ARRAY[]::varchar[])
                """,
                int(rule["id"]),
                str(v.get("severity") or rule["severity"]),
                float(v.get("value") or 0.0),
                float(v.get("threshold") or rule["threshold"] or 0.0),
                json.dumps({
                    "type": "collection_health",
                    "metric": metric_key,
                    "code": v.get("code"),
                    "reason": v.get("reason"),
                }, ensure_ascii=False),
            )
            inserted += 1
        except Exception as exc:
            logger.warning("[collection_health] INSERT 실패 (%s): %s", metric_key, exc)

    return {"inserted": inserted, "skipped_cooldown": skipped, "rule_missing": 0}


# ── 스냅샷 적재 ─────────────────────────────────────────────────────────
def save_snapshot(
    payload: Dict[str, Any],
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> Path:
    """``reports/collection_health_YYYY-MM-DD.json`` 한 개 (당일 덮어쓰기)."""
    report_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = report_dir / f"{SNAPSHOT_PREFIX}{today}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _overall_status(violations: List[Dict[str, Any]]) -> str:
    if any(v.get("severity") == "critical" for v in violations):
        return "critical"
    if violations:
        return "warning"
    return "ok"


# ── 실행 ─────────────────────────────────────────────────────────────────
async def collect_payload(
    *,
    dsn: Optional[str] = None,
) -> Dict[str, Any]:
    """사이트 통계 + 위반 평가 (INSERT 안 함). payload 반환."""
    conn = await asyncpg.connect(dsn or _dsn())
    try:
        stats = await collect_site_stats(conn)
        zero_yield = await collect_zero_yield(conn)
        null_dates = await collect_null_dates(conn)
    finally:
        await conn.close()
    violations = evaluate_violations(stats)
    # baseline 0 사각지대 보완 — 이미 collection.* 로 잡힌 사이트는 중복 보고하지 않는다
    seen = {v["code"] for v in violations}
    violations += [v for v in evaluate_zero_yield(zero_yield) if v["code"] not in seen]
    # 결측률은 수집량과 **독립된 신호**다 — 건수가 정상이어도 필드가 비어 있을 수
    # 있으므로 이미 보고된 사이트라도 따로 낸다(metric namespace 가 다르다).
    violations += evaluate_null_dates(null_dates)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "thresholds": {
            "warning_ratio": THRESH_WARNING_RATIO,
            "cooldown_sec": DEFAULT_COOLDOWN_SEC,
        },
        "active_sites": len(stats),
        "stats": stats,
        "zero_yield": zero_yield,
        "null_dates": null_dates,
        "violations": violations,
        "status": _overall_status(violations),
        "violation_counts": {
            "critical": sum(1 for v in violations if v["severity"] == "critical"),
            "warning":  sum(1 for v in violations if v["severity"] == "warning"),
        },
    }


async def run(
    *,
    insert: bool = True,
    save: bool = True,
    dsn: Optional[str] = None,
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> Dict[str, Any]:
    """수집 + (선택) alert_events INSERT + (선택) 스냅샷 적재."""
    payload = await collect_payload(dsn=dsn)
    if insert and payload["violations"]:
        conn = await asyncpg.connect(dsn or _dsn())
        try:
            ins = await insert_alert_events(conn, payload["violations"])
        finally:
            await conn.close()
        payload["alert_events"] = ins
    else:
        payload["alert_events"] = {"inserted": 0, "skipped_cooldown": 0, "rule_missing": 0}
    if save:
        path = save_snapshot(payload, report_dir=report_dir)
        payload["snapshot_path"] = str(path)
    return payload


def _parse_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="collection_health")
    p.add_argument("--no-insert", action="store_true", help="alert_events INSERT 생략")
    p.add_argument("--no-save", action="store_true", help="스냅샷 파일 저장 생략")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_cli()
    payload = asyncio.run(run(insert=not args.no_insert, save=not args.no_save))
    print(f"[collection_health] status={payload['status']} "
          f"sites={payload['active_sites']} "
          f"critical={payload['violation_counts']['critical']} "
          f"warning={payload['violation_counts']['warning']} "
          f"inserted={payload['alert_events']['inserted']} "
          f"skipped_cooldown={payload['alert_events']['skipped_cooldown']}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
