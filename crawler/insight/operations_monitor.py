"""운영 1주 모니터링 자동화 — 임계 위반 시 자동 알림 (R14 트랙 E).

목적
----
``weekly_monitor`` 가 *일일 누적 보고* 를 담당하고, ``data_quality`` 가 *데이터 자체*
지표를 매일 1회 점검한다면, 이 모듈은 *매시 30분* 호출되어 운영 임계 위반을 *실시간*
감지하여 ``alert_events`` 에 INSERT 한다.

점검 6 metric (운영 SLO)
~~~~~~~~~~~~~~~~~~~~~~~
1. ``data_quality_alerts_count``    : 최신 reports/data_quality_*.json 의 alerts 수
                                       (이전 시점 대비 증가 시 critical)
2. ``regression_ok_ratio``           : /_internal/regression-baseline summary.ok / total
                                       (1.0 미만 → warning)
3. ``voc_daily_drop_pct``            : 어제 voc < 그제 voc * 0.5 → warning
4. ``sentiment_null_rate``           : voc_records.sentiment_label NULL 비율 (10%+ → warning)
5. ``topic_classified_rate_drop``    : 이전 일 대비 분류율 20%+ 감소 → warning
6. ``llm_grounding_last``            : grounding 점수 < 0.3 → warning

각 위반은 ``alert_events`` 에 ``rule_id`` (operations_monitor 룰) 와 함께 INSERT.
적합한 룰이 없으면 ``operations_monitor`` 룰 (system.ops_violations) 으로 합산.

CLI::

    python -m insight.operations_monitor              # 1회 점검 + JSON stdout
    python -m insight.operations_monitor --no-insert  # 점검만, INSERT 생략 (dry-run)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# crawler/ 를 sys.path 보장
_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

import asyncpg  # noqa: E402

logger = logging.getLogger(__name__)

REPO_ROOT = _CRAWLER_DIR.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "reports"
DEFAULT_BASE = os.getenv("SIGNALFORGE_API", "http://127.0.0.1:8000")

# ── 임계 (운영 정책 R14) ──────────────────────────────────────────────────
THRESH_VOC_DAILY_DROP_PCT = 50.0           # 어제 vs 그제 50%+ 감소
THRESH_SENTIMENT_NULL_RATE = 0.10          # 10%+ NULL → 파이프 의심
THRESH_TOPIC_DROP_PCT = 20.0               # 이전일 대비 20%+ 감소
THRESH_GROUNDING_MIN = 0.30                # < 0.3 → grounding 저하
THRESH_REGRESSION_OK_MIN = 1.0             # 100% 미만 → 회귀 실패


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


def _http_get_json(url: str, timeout: float = 6.0) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"_error": str(e)}


# ── 신호 수집 ────────────────────────────────────────────────────────────
def _latest_data_quality(report_dir: Path) -> Dict[str, Any]:
    """가장 최근 reports/data_quality_*.json 1개 로드."""
    if not report_dir.is_dir():
        return {}
    files = sorted(
        report_dir.glob("data_quality_*.json"),
        key=lambda p: p.name,
        reverse=True,
    )
    if not files:
        return {}
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except Exception as e:
        return {"_error": str(e), "_file": files[0].name}


def _grounding_last(report_dir: Path) -> Optional[float]:
    """reports/insight_grounding_history.json 의 마지막 score."""
    path = report_dir / "insight_grounding_history.json"
    if not path.exists():
        return None
    try:
        h = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(h, list) and h:
            last = h[-1]
            v = last.get("grounding_score")
            return float(v) if isinstance(v, (int, float)) else None
    except Exception:
        return None
    return None


async def _voc_2day(conn: asyncpg.Connection) -> Dict[str, Any]:
    """어제 / 그제 voc count + sentiment NULL ratio + topic ratio."""
    rows = await conn.fetch(
        """
        SELECT date_trunc('day', collected_at AT TIME ZONE 'UTC')::date AS d,
               count(*) AS n,
               count(*) FILTER (WHERE sentiment_label IS NULL) AS sent_null,
               count(*) FILTER (WHERE array_length(topics,1) > 0) AS topics_filled
        FROM voc_records
        WHERE collected_at >= NOW() - INTERVAL '3 days'
          AND collected_at <  date_trunc('day', NOW())
        GROUP BY 1
        ORDER BY 1 DESC
        LIMIT 2
        """
    )
    out: List[Dict[str, Any]] = []
    for r in rows:
        n = int(r["n"] or 0)
        out.append({
            "day": r["d"].isoformat(),
            "n": n,
            "sentiment_null_rate": (round(int(r["sent_null"] or 0) / n, 4)
                                    if n > 0 else None),
            "topic_rate": (round(int(r["topics_filled"] or 0) / n, 4)
                           if n > 0 else None),
        })
    return {"days": out}


def _collect_regression(base: str) -> Dict[str, Any]:
    data = _http_get_json(f"{base.rstrip('/')}/api/v1/_internal/regression-baseline")
    if "_error" in data:
        return {"error": data["_error"]}
    s = data.get("summary") or {}
    total = int(s.get("total", 0))
    ok = int(s.get("ok", 0))
    return {
        "total": total,
        "ok": ok,
        "failed": int(s.get("failed", 0)),
        "ok_ratio": (round(ok / total, 4) if total > 0 else None),
        "alembic_ok": bool(data.get("alembic_ok", False)),
    }


# ── 룰 평가 ──────────────────────────────────────────────────────────────
def evaluate_violations(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """6 점검 → 위반 시 alerts 목록.

    각 alert: {metric, severity, value, threshold, reason}
    """
    out: List[Dict[str, Any]] = []

    # 1) data_quality 잔여 alerts
    dq = payload.get("data_quality") or {}
    dq_alerts = dq.get("alerts") or []
    if isinstance(dq_alerts, list) and len(dq_alerts) > 0:
        out.append({
            "metric": "data_quality_alerts_count",
            "severity": "warning",
            "value": float(len(dq_alerts)),
            "threshold": 0.0,
            "reason": f"data_quality alerts={len(dq_alerts)} "
                      f"({', '.join(a.get('metric','?') for a in dq_alerts[:3])})",
        })

    # 2) regression ok_ratio < 1.0
    reg = payload.get("regression") or {}
    okr = reg.get("ok_ratio")
    if isinstance(okr, (int, float)) and okr < THRESH_REGRESSION_OK_MIN:
        out.append({
            "metric": "regression_ok_ratio",
            "severity": "critical",
            "value": float(okr),
            "threshold": float(THRESH_REGRESSION_OK_MIN),
            "reason": f"regression ok_ratio={okr:.3f} < 1.0 (failed={reg.get('failed', 0)})",
        })

    # 3) voc 일별 drop %
    voc = (payload.get("voc") or {}).get("days") or []
    if len(voc) >= 2:
        # voc[0] = 어제, voc[1] = 그제 (DESC)
        prev = int(voc[1].get("n") or 0)
        last = int(voc[0].get("n") or 0)
        if prev > 0:
            drop_pct = (prev - last) / prev * 100.0
            if drop_pct >= THRESH_VOC_DAILY_DROP_PCT:
                out.append({
                    "metric": "voc_daily_drop_pct",
                    "severity": "warning",
                    "value": round(drop_pct, 2),
                    "threshold": THRESH_VOC_DAILY_DROP_PCT,
                    "reason": (f"voc {voc[1]['day']}={prev} → "
                               f"{voc[0]['day']}={last} ({drop_pct:.1f}% 감소)"),
                })

    # 4) sentiment NULL 비율 (어제 기준)
    if voc:
        snr = voc[0].get("sentiment_null_rate")
        if isinstance(snr, (int, float)) and snr > THRESH_SENTIMENT_NULL_RATE:
            out.append({
                "metric": "sentiment_null_rate",
                "severity": "warning",
                "value": float(snr),
                "threshold": THRESH_SENTIMENT_NULL_RATE,
                "reason": f"sentiment_label NULL={snr:.3f} > {THRESH_SENTIMENT_NULL_RATE}",
            })

    # 5) topic 분류율 — 이전일 대비 20%+ 감소
    if len(voc) >= 2:
        prev_t = voc[1].get("topic_rate")
        last_t = voc[0].get("topic_rate")
        if (isinstance(prev_t, (int, float)) and isinstance(last_t, (int, float))
                and prev_t > 0):
            drop = (prev_t - last_t) / prev_t * 100.0
            if drop >= THRESH_TOPIC_DROP_PCT:
                out.append({
                    "metric": "topic_classified_rate_drop",
                    "severity": "warning",
                    "value": round(drop, 2),
                    "threshold": THRESH_TOPIC_DROP_PCT,
                    "reason": (f"topic_rate {voc[1]['day']}={prev_t:.3f} → "
                               f"{voc[0]['day']}={last_t:.3f} ({drop:.1f}% 감소)"),
                })

    # 6) grounding < 0.3
    gl = payload.get("grounding_last")
    if isinstance(gl, (int, float)) and gl < THRESH_GROUNDING_MIN:
        out.append({
            "metric": "llm_grounding_last",
            "severity": "warning",
            "value": float(gl),
            "threshold": THRESH_GROUNDING_MIN,
            "reason": f"grounding last={gl:.3f} < {THRESH_GROUNDING_MIN}",
        })

    return out


# ── alert_events INSERT ──────────────────────────────────────────────────
async def insert_alert_events(
    conn: asyncpg.Connection,
    violations: List[Dict[str, Any]],
) -> int:
    """``operations_monitor`` 룰로 위반당 1행 INSERT.

    룰이 없으면 0 반환 (graceful — seed 미적용 환경 보호).
    중복 발화 방지를 위해 직전 1시간(cooldown_sec) 내 발화가 있으면 skip.
    """
    if not violations:
        return 0
    rule = await conn.fetchrow(
        """
        SELECT id, severity, threshold, cooldown_sec
        FROM alert_rules
        WHERE name = 'operations_monitor' AND is_active = TRUE
        """
    )
    if rule is None:
        logger.info("[operations_monitor] alert_rules.operations_monitor 없음 — INSERT skip")
        return 0

    # cooldown 가드 — 마지막 발화 후 cooldown_sec 미만이면 skip
    last_fired = await conn.fetchval(
        "SELECT max(fired_at) FROM alert_events WHERE rule_id = $1",
        rule["id"],
    )
    if last_fired is not None:
        sec_since = (datetime.now(timezone.utc) - last_fired).total_seconds()
        if sec_since < int(rule["cooldown_sec"] or 0):
            logger.info(
                "[operations_monitor] cooldown 활성 (%.0fs < %ss) — skip",
                sec_since, rule["cooldown_sec"],
            )
            return 0

    inserted = 0
    for v in violations:
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
                    "type": "operations_monitor",
                    "metric": v.get("metric"),
                    "reason": v.get("reason"),
                }, ensure_ascii=False),
            )
            inserted += 1
        except Exception as exc:
            logger.warning("[operations_monitor] INSERT 실패 (%s): %s", v.get("metric"), exc)
    return inserted


# ── 실행 ─────────────────────────────────────────────────────────────────
async def collect_status(
    *,
    base: str = DEFAULT_BASE,
    report_dir: Path = DEFAULT_REPORT_DIR,
    dsn: Optional[str] = None,
) -> Dict[str, Any]:
    """6 metric 수집 + 위반 평가. INSERT 는 하지 않음.

    반환 payload — /ops-status 응답 그대로 사용.
    """
    dsn = dsn or _dsn()
    conn = await asyncpg.connect(dsn)
    try:
        voc = await _voc_2day(conn)
    finally:
        await conn.close()

    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "thresholds": {
            "voc_daily_drop_pct": THRESH_VOC_DAILY_DROP_PCT,
            "sentiment_null_rate": THRESH_SENTIMENT_NULL_RATE,
            "topic_drop_pct": THRESH_TOPIC_DROP_PCT,
            "grounding_min": THRESH_GROUNDING_MIN,
            "regression_ok_min": THRESH_REGRESSION_OK_MIN,
        },
        "data_quality": _latest_data_quality(report_dir),
        "regression": _collect_regression(base),
        "voc": voc,
        "grounding_last": _grounding_last(report_dir),
    }
    payload["violations"] = evaluate_violations(payload)
    payload["status"] = _overall_status(payload["violations"])
    return payload


def _overall_status(violations: List[Dict[str, Any]]) -> str:
    if any(v.get("severity") == "critical" for v in violations):
        return "critical"
    if violations:
        return "warning"
    return "ok"


async def run(
    *,
    base: str = DEFAULT_BASE,
    report_dir: Path = DEFAULT_REPORT_DIR,
    insert: bool = True,
    dsn: Optional[str] = None,
) -> Dict[str, Any]:
    """수집 + (선택) alert_events INSERT. 결과 payload 반환."""
    payload = await collect_status(base=base, report_dir=report_dir, dsn=dsn)
    inserted = 0
    if insert and payload["violations"]:
        conn = await asyncpg.connect(dsn or _dsn())
        try:
            inserted = await insert_alert_events(conn, payload["violations"])
        finally:
            await conn.close()
    payload["alert_events_inserted"] = inserted
    return payload


def _parse_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="operations_monitor")
    p.add_argument("--base", default=DEFAULT_BASE)
    p.add_argument("--no-insert", action="store_true",
                   help="alert_events INSERT 생략 (dry-run)")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_cli()
    payload = asyncio.run(run(base=args.base, insert=not args.no_insert))
    # 사람이 보기 좋은 한 줄 요약 + JSON
    print(f"[ops] status={payload['status']} "
          f"violations={len(payload['violations'])} "
          f"inserted={payload.get('alert_events_inserted', 0)}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
