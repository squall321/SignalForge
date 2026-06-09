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


# ── alert_events INSERT (metric 단위 cooldown) ───────────────────────────
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
    finally:
        await conn.close()
    violations = evaluate_violations(stats)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "thresholds": {
            "warning_ratio": THRESH_WARNING_RATIO,
            "cooldown_sec": DEFAULT_COOLDOWN_SEC,
        },
        "active_sites": len(stats),
        "stats": stats,
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
