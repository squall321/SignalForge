"""ops_status_*.json 파일 기반 자동 알림 — 파일 기반 위반 전파 (R20 트랙 C).

목적
----
``operations_monitor`` (매시 30분) 는 *DB·HTTP* 를 직접 조회해 SLO 위반을
``alert_events`` 에 INSERT 한다.  그 결과는 ``ops_history`` 가 매일 09:30 KST
``reports/ops_status_YYYY-MM-DD.json`` 로 적재해 ``/ops-trend`` endpoint 가 시계열로
소비한다.

이 모듈은 **파일 기반 보강 경로** 다.  매시 호출되어 *TODAY* ops_status JSON 의
violations 배열을 읽어 ``ops_status_violation`` 룰 (id 80) 로 alert_events 에
INSERT 한다.  목적은 세 가지::

1. **이중 안전망** — operations_monitor 가 어떤 이유로 INSERT 실패해도 파일 입력 경로로
   복구.  파일이 있으면 알림은 발화한다.
2. **수동 감사·재발화** — 운영자가 ops_status JSON 을 수정/생성하면 그 즉시 알림 발화.
3. **severity 분류 단순화** — 입력 파일의 ``severity`` 값을 그대로 신뢰
   (operations_monitor 가 이미 critical/warning 으로 라벨링).

severity 분류
~~~~~~~~~~~~~
- ``critical`` : violation.severity == "critical"
- ``warning``  : violation.severity == "warning"
- ``info``     : violation.severity 가 위 둘 외 (정책 변경 시)

cooldown 가드
~~~~~~~~~~~~~
같은 metric 의 직전 발화가 ``cooldown_sec`` (기본 3600) 내면 skip.
rule 전체 cooldown 이 아니라 *metric 단위* cooldown — 다른 metric 위반은 즉시 발화.

CLI::

    python -m insight.ops_alerts                   # 1회 실행 (오늘 UTC)
    python -m insight.ops_alerts --date 2026-06-05 # 특정 일자
    python -m insight.ops_alerts --no-insert       # dry-run
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
from typing import Any, Dict, List, Optional, Tuple

# crawler/ sys.path 보장
_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

import asyncpg  # noqa: E402

logger = logging.getLogger(__name__)

REPO_ROOT = _CRAWLER_DIR.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "reports"

ALERT_RULE_NAME = "ops_status_violation"
DEFAULT_COOLDOWN_SEC = 3600


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


# ── 입력 로드 ───────────────────────────────────────────────────────────
def load_ops_status(
    target: date,
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> Optional[Dict[str, Any]]:
    """``reports/ops_status_YYYY-MM-DD.json`` 1개 로드. 없으면 None."""
    path = report_dir / f"ops_status_{target.isoformat()}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[ops-alerts] %s 파싱 실패: %s", path.name, exc)
        return None


# ── severity 분류 ───────────────────────────────────────────────────────
def classify_severity(violation: Dict[str, Any]) -> str:
    """위반 1개의 severity 라벨 분류.

    입력 ``violation.severity`` 가 ``critical`` 이면 critical,
    ``warning`` 이면 warning, 기타 (또는 없음) info 로 분류.
    """
    sev = str(violation.get("severity") or "").lower()
    if sev == "critical":
        return "critical"
    if sev == "warning":
        return "warning"
    return "info"


def severity_distribution(
    violations: List[Dict[str, Any]],
) -> Dict[str, int]:
    """전체 violations 의 severity 분포 ({critical, warning, info} → count)."""
    counts = {"critical": 0, "warning": 0, "info": 0}
    for v in violations:
        counts[classify_severity(v)] += 1
    return counts


# ── alert_events INSERT ────────────────────────────────────────────────
async def _resolve_rule(conn: asyncpg.Connection) -> Optional[asyncpg.Record]:
    """``ops_status_violation`` 활성 룰 조회. 없으면 None."""
    return await conn.fetchrow(
        """
        SELECT id, severity, threshold, cooldown_sec
        FROM alert_rules
        WHERE name = $1 AND is_active = TRUE
        """,
        ALERT_RULE_NAME,
    )


async def _last_fired_per_metric(
    conn: asyncpg.Connection,
    rule_id: int,
    cooldown_sec: int,
) -> Dict[str, datetime]:
    """이 룰의 cooldown 윈도우 내 metric → 마지막 발화 시각 매핑."""
    rows = await conn.fetch(
        """
        SELECT payload->>'metric' AS metric,
               max(fired_at) AS last
        FROM alert_events
        WHERE rule_id = $1
          AND fired_at >= NOW() - make_interval(secs => $2)
        GROUP BY 1
        """,
        rule_id,
        int(cooldown_sec),
    )
    out: Dict[str, datetime] = {}
    for r in rows:
        m = r["metric"]
        if m:
            out[str(m)] = r["last"]
    return out


async def insert_alert_events(
    conn: asyncpg.Connection,
    violations: List[Dict[str, Any]],
    *,
    target_date: Optional[date] = None,
) -> Tuple[int, int]:
    """violations → alert_events INSERT.

    반환: (inserted, skipped_by_cooldown)

    룰이 없으면 (0, 0). 룰이 있으면 metric 단위 cooldown 확인 후 INSERT.
    """
    if not violations:
        return (0, 0)
    rule = await _resolve_rule(conn)
    if rule is None:
        logger.info("[ops-alerts] alert_rules.%s 비활성 — INSERT skip", ALERT_RULE_NAME)
        return (0, 0)

    cooldown_sec = int(rule["cooldown_sec"] or DEFAULT_COOLDOWN_SEC)
    last_fired = await _last_fired_per_metric(conn, int(rule["id"]), cooldown_sec)

    inserted = 0
    skipped = 0
    now = datetime.now(timezone.utc)
    for v in violations:
        metric = str(v.get("metric") or "unknown")
        # metric 단위 cooldown 가드
        prev = last_fired.get(metric)
        if prev is not None:
            sec_since = (now - prev).total_seconds()
            if sec_since < cooldown_sec:
                skipped += 1
                logger.info(
                    "[ops-alerts] cooldown skip metric=%s (%.0fs < %ss)",
                    metric, sec_since, cooldown_sec,
                )
                continue
        severity = classify_severity(v)
        try:
            await conn.execute(
                """
                INSERT INTO alert_events
                    (rule_id, severity, value, threshold, payload, dispatched_channels)
                VALUES ($1, $2, $3, $4, $5::jsonb, ARRAY[]::varchar[])
                """,
                int(rule["id"]),
                severity,
                float(v.get("value") or 0.0),
                float(v.get("threshold") or rule["threshold"] or 0.0),
                json.dumps({
                    "type": "ops_status_violation",
                    "metric": metric,
                    "violation": {
                        "value": v.get("value"),
                        "threshold": v.get("threshold"),
                        "reason": v.get("reason"),
                    },
                    "source_date": (target_date.isoformat() if target_date else None),
                }, ensure_ascii=False),
            )
            inserted += 1
        except Exception as exc:
            logger.warning("[ops-alerts] INSERT 실패 (%s): %s", metric, exc)
    return (inserted, skipped)


# ── 실행 ───────────────────────────────────────────────────────────────
async def run(
    *,
    target: Optional[date] = None,
    report_dir: Path = DEFAULT_REPORT_DIR,
    insert: bool = True,
    dsn: Optional[str] = None,
) -> Dict[str, Any]:
    """ops_status_TODAY.json 1회 검사 → alert_events INSERT.

    반환 payload::

        {
          "target_date": "2026-06-05",
          "found": True,
          "status": "critical",
          "violations_count": 2,
          "severity_distribution": {"critical": 1, "warning": 1, "info": 0},
          "inserted": 2,
          "skipped_by_cooldown": 0,
        }
    """
    target = target or datetime.now(timezone.utc).date()
    status = load_ops_status(target, report_dir=report_dir)
    if status is None:
        logger.info("[ops-alerts] ops_status_%s.json 없음 — skip", target.isoformat())
        return {
            "target_date": target.isoformat(),
            "found": False,
            "status": None,
            "violations_count": 0,
            "severity_distribution": {"critical": 0, "warning": 0, "info": 0},
            "inserted": 0,
            "skipped_by_cooldown": 0,
        }

    violations = list(status.get("violations") or [])
    dist = severity_distribution(violations)

    inserted = 0
    skipped = 0
    if insert and violations:
        conn = await asyncpg.connect(dsn or _dsn())
        try:
            inserted, skipped = await insert_alert_events(
                conn, violations, target_date=target,
            )
        finally:
            await conn.close()

    return {
        "target_date": target.isoformat(),
        "found": True,
        "status": status.get("status"),
        "violations_count": len(violations),
        "severity_distribution": dist,
        "inserted": inserted,
        "skipped_by_cooldown": skipped,
    }


def _parse_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="ops_alerts")
    p.add_argument("--date", dest="target_date",
                   help="YYYY-MM-DD (기본: 오늘 UTC)")
    p.add_argument("--no-insert", action="store_true",
                   help="alert_events INSERT 생략 (dry-run)")
    p.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR),
                   help="ops_status_*.json 디렉토리")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_cli()
    target = (datetime.fromisoformat(args.target_date).date()
              if args.target_date else datetime.now(timezone.utc).date())
    result = asyncio.run(run(
        target=target,
        report_dir=Path(args.report_dir),
        insert=not args.no_insert,
    ))
    print(f"[ops-alerts] target={result['target_date']} "
          f"found={result['found']} "
          f"violations={result['violations_count']} "
          f"inserted={result['inserted']} "
          f"skipped={result['skipped_by_cooldown']} "
          f"dist={result['severity_distribution']}")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
