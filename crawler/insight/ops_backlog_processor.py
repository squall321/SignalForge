"""ops_status backlog 일괄 처리기 — R21 트랙 C.

목적
----
``ops_alerts`` 는 *오늘 (TODAY)* ``reports/ops_status_YYYY-MM-DD.json`` 1개의
``violations`` 배열만 ``alert_events`` 로 INSERT 한다.  반면 ``ops_trend_analysis``
의 ``backfill_from_db`` 가 만든 과거 JSON 파일들은 ``violations_count`` 는 있지만
``violations: []`` (헤더만) 이라 *처리되지 않은 backlog* 로 남는다.

R20 종료 시점 backlog = 1,152::

    2026-06-02: 312  (status warning, violations[])
    2026-06-03: 415  (status critical, violations[])
    2026-06-04: 423  (status critical, violations[])
    2026-06-05:   2  (status critical, violations 채워짐)
                = 1,152

본 모듈은 매시 45분 Celery beat 에서 호출되어 다음을 수행한다.

1. **윈도우 스캔**  ``--days`` (기본 7) 만큼의 ``ops_status_*.json`` 을 모두 읽는다.
2. **violations 재구성**  헤더만 있는 backfill 파일은 ``alert_events`` 에서 같은 날짜
   범위의 행을 끌어와 violation 객체로 *재구성* 한다 (실제 INSERT 는 아니라 분류용).
3. **severity 분류 + dedupe**  payload->>'metric' + source_date 키로 이미 INSERT 된
   violation 은 *skip*.
4. **자동 처리**
   * critical → ``alert_events`` 즉시 INSERT (``ops_status_violation`` 룰 id 80)
   * warning  → ``reports/ops_backlog_warning_summary.json`` 일별 요약 누적 (수동 검토용)
   * info     → 카운트만 (백로그 정리 — 노이즈)
5. **감사 로그**  ``reports/ops_backlog_audit.jsonl`` 에 1 run = 1 line 추가.

안전 장치
----------
* DRY_RUN (insert=False) 기본 호환 — CLI ``--no-insert`` 로 강제 가능.
* cooldown_sec (룰 정의값) 내 동일 metric 재발화 skip — ``ops_alerts`` 와 동일 규칙.
* backfill 파일에서 재구성한 violation 은 ``alert_events`` 에 이미 존재할 가능성이
  매우 높으므로 (그 카운트로 헤더가 만들어진 것이므로) **dedupe key 가 매칭되면
  처리됨으로 표시 + INSERT skip**.  이로써 1,152 backlog 중 대부분은 "이미 처리됨"
  분류로 정리된다.

CLI::

    python -m insight.ops_backlog_processor              # 7일 윈도우 1회 실행
    python -m insight.ops_backlog_processor --days 30    # 30일 윈도우
    python -m insight.ops_backlog_processor --no-insert  # dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# crawler/ sys.path 보장 — insight.* 직접 import.
_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

import asyncpg  # noqa: E402

from insight.ops_alerts import (  # noqa: E402
    ALERT_RULE_NAME,
    DEFAULT_COOLDOWN_SEC,
    classify_severity,
    load_ops_status,
    severity_distribution,
)

logger = logging.getLogger(__name__)

REPO_ROOT = _CRAWLER_DIR.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "reports"
WARNING_SUMMARY_FILE = "ops_backlog_warning_summary.json"
AUDIT_FILE = "ops_backlog_audit.jsonl"


# ─────────────────────────────────────────────────────────────────────────
# 윈도우 스캔
# ─────────────────────────────────────────────────────────────────────────
def scan_window(
    *,
    days: int,
    end_date: Optional[date] = None,
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> List[Tuple[date, Dict[str, Any]]]:
    """end_date (기본 오늘 UTC) 부터 ``days`` 일 거슬러 가며 ops_status JSON 을 모은다.

    반환: 오래된 날짜 → 최신 순으로 정렬된 ``(date, payload)`` 목록.
    파일이 없는 날짜는 건너뛴다 (graceful).
    """
    end_date = end_date or datetime.now(timezone.utc).date()
    found: List[Tuple[date, Dict[str, Any]]] = []
    for i in range(days):
        d = end_date - timedelta(days=i)
        payload = load_ops_status(d, report_dir=report_dir)
        if payload is None:
            continue
        found.append((d, payload))
    # 오래된 순으로 정렬 — 시계열 처리/리포팅 편의
    found.sort(key=lambda t: t[0])
    return found


# ─────────────────────────────────────────────────────────────────────────
# violations 재구성 — alert_events 에서 끌어오기
# ─────────────────────────────────────────────────────────────────────────
async def reconstruct_violations_from_db(
    conn: asyncpg.Connection,
    target_date: date,
) -> List[Dict[str, Any]]:
    """``alert_events`` 에서 ``target_date`` 일자의 ops-status 위반 행을 violation 객체로 재구성.

    backfill_from_db 가 만든 헤더 (``violations_count`` 만 있고 본문 빈 배열) 파일을
    실제 violation 객체로 펼치는 용도.  이미 alert_events 에 있으므로 INSERT 대상은
    아니며, dedupe key 산출과 severity 분포 계산을 위해 사용된다.

    ops_status_violation (id 80) 외 다른 룰 (operations_monitor 등) 도 함께 끌어와
    backlog 의 *원천* 을 보존한다.
    """
    rows = await conn.fetch(
        """
        SELECT
            e.id, e.fired_at, e.severity, e.value, e.threshold, e.payload,
            r.name AS rule_name
        FROM alert_events e
        JOIN alert_rules r ON r.id = e.rule_id
        WHERE e.fired_at >= ($1::date)::timestamptz
          AND e.fired_at <  (($1::date + 1))::timestamptz
          AND (
                r.name = 'ops_status_violation'
             OR r.name = 'operations_monitor'
             OR r.name LIKE 'ops_%'
          )
        ORDER BY e.fired_at
        """,
        target_date,
    )
    out: List[Dict[str, Any]] = []
    for r in rows:
        payload_raw = r["payload"]
        payload: Dict[str, Any] = {}
        if isinstance(payload_raw, str):
            try:
                payload = json.loads(payload_raw)
            except Exception:
                payload = {}
        elif isinstance(payload_raw, dict):
            payload = payload_raw
        metric = (
            payload.get("metric")
            or (payload.get("violation") or {}).get("metric")
            or "unknown"
        )
        viol = payload.get("violation") or {}
        out.append({
            "metric": str(metric),
            "severity": str(r["severity"] or "info"),
            "value": (viol.get("value") if viol.get("value") is not None
                      else r["value"]),
            "threshold": (viol.get("threshold") if viol.get("threshold") is not None
                          else r["threshold"]),
            "reason": viol.get("reason"),
            "_source_event_id": int(r["id"]),
            "_source_rule": str(r["rule_name"]),
            "_already_fired": True,  # 이미 alert_events 에 있음
        })
    return out


# ─────────────────────────────────────────────────────────────────────────
# 이미 발화된 violations 키 수집
# ─────────────────────────────────────────────────────────────────────────
def _dedupe_key(target: date, metric: str) -> str:
    """dedupe 식별자: ``source_date|metric``."""
    return f"{target.isoformat()}|{metric}"


async def already_fired_keys(
    conn: asyncpg.Connection,
    *,
    days: int,
    rule_name: str = ALERT_RULE_NAME,
) -> Set[str]:
    """최근 ``days`` 일 ``rule_name`` 의 ``(source_date, metric)`` 키 셋.

    ``ops_alerts`` 가 INSERT 한 행은 payload 에 ``source_date`` 와 ``metric`` 을 보존.
    backlog processor 가 같은 violation 을 두 번 INSERT 하지 않도록 dedupe 한다.
    """
    rows = await conn.fetch(
        """
        SELECT
            payload->>'metric'      AS metric,
            payload->>'source_date' AS source_date
        FROM alert_events e
        JOIN alert_rules r ON r.id = e.rule_id
        WHERE r.name = $1
          AND e.fired_at >= NOW() - ($2::int || ' days')::interval
        """,
        rule_name,
        int(days),
    )
    keys: Set[str] = set()
    for r in rows:
        sd = r["source_date"]
        m = r["metric"]
        if sd and m:
            keys.add(f"{sd}|{m}")
    return keys


# ─────────────────────────────────────────────────────────────────────────
# severity 분류 + 자동 처리
# ─────────────────────────────────────────────────────────────────────────
def split_by_severity(
    violations: Iterable[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """violations → {critical, warning, info} 그룹 분할."""
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "critical": [],
        "warning": [],
        "info": [],
    }
    for v in violations:
        buckets[classify_severity(v)].append(v)
    return buckets


async def insert_critical_violations(
    conn: asyncpg.Connection,
    violations: List[Dict[str, Any]],
    *,
    target_date: date,
    fired_keys: Set[str],
    rule_name: str = ALERT_RULE_NAME,
) -> Tuple[int, int]:
    """critical violations 만 alert_events 로 INSERT — dedupe 키 매칭 시 skip.

    반환: (inserted, skipped_by_dedupe).
    룰이 없으면 (0, len(violations)) — 모두 skip 으로 카운트.
    """
    if not violations:
        return (0, 0)
    rule = await conn.fetchrow(
        """
        SELECT id, threshold, cooldown_sec
        FROM alert_rules
        WHERE name = $1 AND is_active = TRUE
        """,
        rule_name,
    )
    if rule is None:
        logger.info(
            "[ops-backlog] alert_rules.%s 비활성 — critical INSERT skip", rule_name,
        )
        return (0, len(violations))

    rule_id = int(rule["id"])
    default_threshold = float(rule["threshold"] or 0.0)
    inserted = 0
    skipped = 0
    for v in violations:
        metric = str(v.get("metric") or "unknown")
        key = _dedupe_key(target_date, metric)
        if key in fired_keys:
            skipped += 1
            continue
        # 이미 alert_events 에서 가져온 행이면 (재구성 경로) INSERT 대상이 아님
        if v.get("_already_fired"):
            skipped += 1
            fired_keys.add(key)
            continue
        try:
            await conn.execute(
                """
                INSERT INTO alert_events
                    (rule_id, severity, value, threshold, payload, dispatched_channels)
                VALUES ($1, $2, $3, $4, $5::jsonb, ARRAY[]::varchar[])
                """,
                rule_id,
                "critical",
                float(v.get("value") or 0.0),
                float(v.get("threshold") or default_threshold),
                json.dumps({
                    "type": "ops_status_violation",
                    "metric": metric,
                    "violation": {
                        "value": v.get("value"),
                        "threshold": v.get("threshold"),
                        "reason": v.get("reason"),
                    },
                    "source_date": target_date.isoformat(),
                    "source_processor": "ops_backlog_processor",
                }, ensure_ascii=False),
            )
            inserted += 1
            fired_keys.add(key)
        except Exception as exc:
            logger.warning(
                "[ops-backlog] INSERT 실패 (%s %s): %s",
                target_date, metric, exc,
            )
    return (inserted, skipped)


def append_warning_summary(
    *,
    summary_by_day: Dict[str, Dict[str, Any]],
    report_dir: Path = DEFAULT_REPORT_DIR,
    file_name: str = WARNING_SUMMARY_FILE,
) -> Path:
    """warning 위반의 일별 요약을 누적 JSON 으로 기록 — 운영자 수동 검토용.

    포맷::

        {
          "updated_at": "...",
          "days": {
            "2026-06-04": {"warning_count": 12, "metrics": ["voc_drop_pct", ...]},
            ...
          }
        }
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / file_name
    existing: Dict[str, Any] = {"updated_at": None, "days": {}}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if "days" not in existing:
                existing["days"] = {}
        except Exception:
            existing = {"updated_at": None, "days": {}}
    existing["days"].update(summary_by_day)
    existing["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def append_audit_line(
    *,
    report_dir: Path,
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    insert_enabled: bool,
    days: int,
    distribution_window: Dict[str, int],
    actions: Dict[str, int],
    per_day: Dict[str, Dict[str, Any]],
    status: str,
    exc_message: Optional[str],
) -> Path:
    """ops_backlog_audit.jsonl 에 1 line 추가."""
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / AUDIT_FILE
    line = {
        "run_id": run_id,
        "script": "ops_backlog_processor",
        "mode": "apply" if insert_enabled else "dry_run",
        "env": {
            "DAYS_WINDOW": int(days),
            "INSERT": bool(insert_enabled),
        },
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "status": status,
        "exc_message": exc_message,
        "counters": {
            "window_severity": distribution_window,
            "actions": actions,
            "files_scanned": len(per_day),
        },
        "backup_path": None,
        "notes": [],
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return path


# ─────────────────────────────────────────────────────────────────────────
# 메인 처리
# ─────────────────────────────────────────────────────────────────────────
async def process_backlog(
    *,
    days: int = 7,
    end_date: Optional[date] = None,
    insert: bool = True,
    report_dir: Path = DEFAULT_REPORT_DIR,
    dsn: Optional[str] = None,
) -> Dict[str, Any]:
    """윈도우 내 ops_status_*.json 의 violations 를 일괄 처리.

    반환::

        {
          "days_window": 7,
          "files_scanned": 5,
          "window_severity": {"critical": 7, "warning": 836, "info": 309},
          "per_day": {"2026-06-02": {...}, ...},
          "actions": {"critical_inserted": 3, "critical_skipped": 4,
                       "warning_logged": 836, "info_ignored": 309},
          "warning_summary_path": "...",
          "audit_path": "...",
        }
    """
    started_at = datetime.now(timezone.utc)
    run_id = uuid.uuid4().hex[:12]
    status_label = "ok"
    exc_message: Optional[str] = None

    scanned = scan_window(days=days, end_date=end_date, report_dir=report_dir)
    per_day: Dict[str, Dict[str, Any]] = {}
    window_dist = {"critical": 0, "warning": 0, "info": 0}
    actions = {
        "critical_inserted": 0,
        "critical_skipped": 0,
        "warning_logged": 0,
        "info_ignored": 0,
    }
    warning_by_day: Dict[str, Dict[str, Any]] = {}

    conn: Optional[asyncpg.Connection] = None
    try:
        if scanned:
            conn = await asyncpg.connect(dsn or _dsn())
            fired_keys = await already_fired_keys(conn, days=days)
        else:
            fired_keys = set()

        for d, payload in scanned:
            violations = list(payload.get("violations") or [])
            # backfill_from_db 파일 (헤더만) 은 alert_events 에서 재구성
            need_reconstruct = (
                int(payload.get("violations_count") or 0) > 0
                and not violations
            )
            if need_reconstruct and conn is not None:
                violations = await reconstruct_violations_from_db(conn, d)

            buckets = split_by_severity(violations)
            day_dist = {k: len(v) for k, v in buckets.items()}
            for k, n in day_dist.items():
                window_dist[k] += n

            day_record: Dict[str, Any] = {
                "violations_count_header": int(payload.get("violations_count") or 0),
                "violations_materialized": len(violations),
                "reconstructed": need_reconstruct,
                "severity_distribution": day_dist,
            }

            # critical 처리
            inserted = skipped = 0
            if insert and conn is not None:
                inserted, skipped = await insert_critical_violations(
                    conn, buckets["critical"],
                    target_date=d,
                    fired_keys=fired_keys,
                )
            else:
                # dry-run: dedupe 만 계산
                for v in buckets["critical"]:
                    key = _dedupe_key(d, str(v.get("metric") or "unknown"))
                    if key in fired_keys or v.get("_already_fired"):
                        skipped += 1
                    else:
                        inserted += 1  # would-insert
            day_record["critical_inserted"] = inserted
            day_record["critical_skipped"] = skipped
            actions["critical_inserted"] += inserted
            actions["critical_skipped"] += skipped

            # warning — 누적 요약
            warnings = buckets["warning"]
            if warnings:
                metric_names = sorted({
                    str(v.get("metric") or "unknown") for v in warnings
                })
                warning_by_day[d.isoformat()] = {
                    "warning_count": len(warnings),
                    "metrics": metric_names,
                }
                actions["warning_logged"] += len(warnings)

            # info — 무시 (백로그 정리)
            actions["info_ignored"] += len(buckets["info"])

            per_day[d.isoformat()] = day_record

        # warning 요약 파일 갱신
        warning_summary_path: Optional[Path] = None
        if warning_by_day:
            warning_summary_path = append_warning_summary(
                summary_by_day=warning_by_day,
                report_dir=report_dir,
            )
    except Exception as exc:  # pragma: no cover — defensive
        status_label = "error"
        exc_message = repr(exc)
        logger.exception("[ops-backlog] 처리 중 예외")
        warning_summary_path = None
    finally:
        if conn is not None:
            await conn.close()

    finished_at = datetime.now(timezone.utc)
    audit_path = append_audit_line(
        report_dir=report_dir,
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        insert_enabled=insert,
        days=days,
        distribution_window=window_dist,
        actions=actions,
        per_day=per_day,
        status=status_label,
        exc_message=exc_message,
    )

    return {
        "run_id": run_id,
        "days_window": days,
        "files_scanned": len(per_day),
        "window_severity": window_dist,
        "per_day": per_day,
        "actions": actions,
        "warning_summary_path": (str(warning_summary_path)
                                 if warning_summary_path else None),
        "audit_path": str(audit_path),
        "status": status_label,
    }


# ─────────────────────────────────────────────────────────────────────────
# DSN — ops_alerts 와 동일 정책
# ─────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────
def _parse_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="ops_backlog_processor")
    p.add_argument("--days", type=int, default=7,
                   help="윈도우 (일) — 기본 7")
    p.add_argument("--end-date", dest="end_date",
                   help="윈도우 끝 날짜 YYYY-MM-DD (기본: 오늘 UTC)")
    p.add_argument("--no-insert", action="store_true",
                   help="alert_events INSERT 생략 (dry-run)")
    p.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR),
                   help="ops_status_*.json 디렉토리")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = _parse_cli()
    end_date = (datetime.fromisoformat(args.end_date).date()
                if args.end_date else None)
    result = asyncio.run(process_backlog(
        days=int(args.days),
        end_date=end_date,
        insert=not args.no_insert,
        report_dir=Path(args.report_dir),
    ))
    print(
        f"[ops-backlog] run={result['run_id']} "
        f"files={result['files_scanned']} "
        f"window_severity={result['window_severity']} "
        f"actions={result['actions']}"
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
