"""백필 안전장치 실 운영 모니터 (R20 트랙 E → R21 임계 조정).

목적
----
R18 사고 (topic 백필 재실행이 기존 분류 폭락 유발) 예방 정책의 *실 운영 준수*
여부를 자동으로 감시한다.  ``reports/backfill_audit.jsonl`` 을 매일 09:30 KST
스캔하여 다음 위험 패턴을 탐지·요약·보고한다.

탐지 규칙 (4종) — R21 임계 조정 적용
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
1. ``preserve_existing_off`` : 실 적용 (DRY_RUN=False AND DATA_TOUCHED≠False) 중
   PRESERVE_EXISTING 이 False 거나 미설정 — 기존 분류 덮어쓸 위험.
   - dedup_voc 처럼 PRESERVE 개념이 적용 안 되는 스크립트는
     ``AUDIT_PRESERVE_EXEMPT_SCRIPTS`` 환경변수로 면제.
2. ``backup_disabled``       : 실 적용 중 BACKUP_BEFORE 가 False 거나 미설정.
   - R21 조정: PRESERVE_EXISTING=True 면 사고 시 복구 불요 → ``warning`` 으로 격하
     (이전 critical → R20 운영에서 FP 다수 — preserve 켜진 안전 백필도 경보).
3. ``dry_run_off_full``      : DRY_RUN=False AND mode ∈ {full_reclassify, full,
   reclassify_all} — DRY_RUN 검증 없이 전체 적용.
4. ``status_error``          : status == "error" — 백필 실패.
   - R21 조정: dry_run 모드의 일회성 실패는 데이터 영향 없음 → ``info`` 로 격하.

윈도우는 기본 7일.  각 위반은 ``risk`` 수준 (critical/warning/info) 으로 분류.

R21 임계 환경변수 (1주 시뮬레이션 결과 기반) — R22 트랙 E 보강
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
| 변수                              | 기본 | 동작                                       |
|-----------------------------------|------|--------------------------------------------|
| AUDIT_MIN_RUNS_FOR_ALERT          | 0    | 윈도우 내 총 run 수가 이 값 이하면 경보 억제 |
| AUDIT_PRESERVE_EXEMPT_SCRIPTS     | ""   | 콤마 구분 스크립트명 — preserve 규칙 면제   |
| AUDIT_INSERT_ONLY_SCRIPTS         | ""   | 콤마 구분 *수집기* 명 — preserve+backup 면제|
| AUDIT_BACKUP_RISK_WITH_PRESERVE   | warn | preserve=True 시 backup_disabled 등급 격하 |
| AUDIT_DRY_RUN_ERROR_RISK          | info | dry_run + status=error 의 등급             |

INSERT_ONLY vs PRESERVE_EXEMPT (R22)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
- ``preserve_exempt`` — *재분류 가능성이 있는* 스크립트 중 preserve 의미가 모호한
  경우 (예: dedup_voc) preserve 규칙만 면제. backup 규칙은 그대로 적용.
- ``insert_only``     — 절대로 기존 row 의 컬럼을 수정하지 않는 *수집기*
  (예: crisis_platform_direct).  ``ON CONFLICT DO NOTHING`` 으로 신규만 추가.
  preserve + backup 둘 다 면제.  (R18 사고 모델인 reclassify 와 의미가 다름.)

기본값은 R21 1주 시뮬레이션 (2 runs, 0 alerts) + 가상 7일 정상 워크로드
(12 runs/wk) 에서 FP 0, FN 0 을 검증한 값이다.

출력
~~~~
``summarize()`` 가 다음 dict 반환::

    {
      "generated_at": "2026-06-05T...",
      "window_days": 7,
      "total_runs": 12,
      "alerts": [
        {"run_id":"ab12...", "script":"topic_backfill",
         "rule":"preserve_existing_off", "risk":"critical",
         "started_at":"2026-06-05T13:04:26+00:00", "mode":"dry_run",
         "reason":"PRESERVE_EXISTING=False"},
        ...
      ],
      "alert_counts": {"critical":2, "warning":1, "info":0},
      "by_script": {
        "topic_backfill":  {"runs":5, "ok":5, "error":0, "violations":2},
        ...
      }
    }

CLI::

    python -m insight.backfill_audit_monitor              # 7일 윈도우
    python -m insight.backfill_audit_monitor --days 14
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# crawler/ sys.path 보장
_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

from insight.backfill_audit import _audit_path  # noqa: E402

logger = logging.getLogger(__name__)

REPO_ROOT = _CRAWLER_DIR.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "reports"

# ── 위험 규칙 ────────────────────────────────────────────────────────────
# R18 사고 원인 (PRESERVE_EXISTING=False) 은 critical.
# 백업 누락 (BACKUP_BEFORE=False) 은 사고 시 복구 불가 → critical.
# DRY_RUN 우회는 영향 폭이 크지만 PRESERVE_EXISTING 이 켜져 있으면 보호됨 → warning.
# status=error 는 데이터 부정합 가능 → warning.
_RULE_RISKS = {
    "preserve_existing_off": "critical",
    "backup_disabled":       "critical",
    "dry_run_off_full":      "warning",
    "status_error":          "warning",
}

_VALID_RISK = {"critical", "warning", "info"}


def _thresholds() -> Dict[str, Any]:
    """R21 운영 임계 — 환경변수에서 1회 로드.

    값은 모니터 호출 시점에 read.  테스트에서 monkeypatch.setenv 로 주입 가능.
    """
    min_runs_raw = os.getenv("AUDIT_MIN_RUNS_FOR_ALERT", "0").strip()
    try:
        min_runs = int(min_runs_raw)
    except ValueError:
        min_runs = 0

    exempt_raw = os.getenv("AUDIT_PRESERVE_EXEMPT_SCRIPTS", "").strip()
    exempt = {s.strip() for s in exempt_raw.split(",") if s.strip()}

    # R22 — 수집기 (ON CONFLICT DO NOTHING) 면제 리스트.  preserve+backup 둘 다.
    insert_only_raw = os.getenv(
        "AUDIT_INSERT_ONLY_SCRIPTS",
        "crisis_platform_direct",  # R22 트랙 E — 기본 안전망.
    ).strip()
    insert_only = {s.strip() for s in insert_only_raw.split(",") if s.strip()}

    backup_risk = os.getenv("AUDIT_BACKUP_RISK_WITH_PRESERVE", "warning").strip().lower()
    if backup_risk not in _VALID_RISK:
        backup_risk = "warning"

    dryrun_err_risk = os.getenv("AUDIT_DRY_RUN_ERROR_RISK", "info").strip().lower()
    if dryrun_err_risk not in _VALID_RISK:
        dryrun_err_risk = "info"

    return {
        "min_runs_for_alert": min_runs,
        "preserve_exempt_scripts": exempt,
        "insert_only_scripts": insert_only,
        "backup_risk_with_preserve": backup_risk,
        "dry_run_error_risk": dryrun_err_risk,
    }


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    """JSONL 한 파일 전체 로드.  파일 없으면 [].  파싱 실패 줄은 skip."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        logger.warning("[backfill_audit_monitor] read fail: %s", e)
        return []
    out: List[Dict[str, Any]] = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def _parse_started_at(s: Optional[str]) -> Optional[datetime]:
    """started_at ISO8601 문자열 → datetime.  실패 시 None."""
    if not s:
        return None
    try:
        # Python 3.10 의 fromisoformat 은 +00:00 형식 지원
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _within_window(run: Dict[str, Any], cutoff: datetime) -> bool:
    """``started_at`` 가 cutoff 이후면 True. 파싱 실패 시 보수적으로 포함."""
    dt = _parse_started_at(run.get("started_at"))
    if dt is None:
        return True
    return dt >= cutoff


def _check_rules(
    run: Dict[str, Any],
    thresholds: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """단일 run 에 대해 4종 규칙을 적용.  매칭된 위반 리스트 반환.

    같은 run 이 여러 규칙 위반 시 모두 보고.
    ``thresholds`` (R21) 는 ``_thresholds()`` 결과 — 면제·격하 정책을 주입.
    """
    th = thresholds if thresholds is not None else _thresholds()

    env = run.get("env") or {}
    mode = (run.get("mode") or "").lower()
    status = (run.get("status") or "").lower()
    script = run.get("script") or "unknown"
    violations: List[Dict[str, Any]] = []

    # dry_run 모드는 PRESERVE/BACKUP 검사에서 제외 — 실제 DB 변경이 없으므로 사고 위험 0.
    is_dry = (mode == "dry_run") or (env.get("DRY_RUN") is True)
    # DATA_TOUCHED=False 명시 — 메타/임계 변경만이라 데이터 미수정.  검사 면제.
    data_untouched = env.get("DATA_TOUCHED") is False
    # R22 — 수집기 (ON CONFLICT DO NOTHING) 는 기존 row 미수정.  preserve+backup 면제.
    insert_only = script in th["insert_only_scripts"]

    if not is_dry and not data_untouched and not insert_only:
        # R21: preserve 면제 — dedup_voc 처럼 PRESERVE 개념이 적용 안 되는 스크립트.
        preserve_exempt = script in th["preserve_exempt_scripts"]
        preserve = env.get("PRESERVE_EXISTING")
        preserve_ok = preserve is True

        if not preserve_exempt and (preserve is False or preserve is None):
            violations.append({
                "rule": "preserve_existing_off",
                "risk": _RULE_RISKS["preserve_existing_off"],
                "reason": (
                    "PRESERVE_EXISTING=False" if preserve is False
                    else "PRESERVE_EXISTING 미설정 (기본 덮어쓰기 위험)"
                ),
            })
        backup = env.get("BACKUP_BEFORE")
        if backup is False or backup is None:
            # R21 격하 — PRESERVE 가 True 이면 사고 시 복구 불요.  warning 으로.
            risk = (
                th["backup_risk_with_preserve"]
                if preserve_ok else _RULE_RISKS["backup_disabled"]
            )
            violations.append({
                "rule": "backup_disabled",
                "risk": risk,
                "reason": (
                    "BACKUP_BEFORE=False" if backup is False
                    else "BACKUP_BEFORE 미설정 (백업 없이 백필)"
                ),
            })

    # DRY_RUN 우회 전체 적용 — mode 가 명시적으로 "full" 계열일 때만 경고.
    # ("preserve_existing" 은 위에서 PRESERVE 검사로 처리하므로 중복 보고 안 함.)
    full_modes = {"full_reclassify", "full", "reclassify_all"}
    if env.get("DRY_RUN") is False and mode in full_modes:
        violations.append({
            "rule": "dry_run_off_full",
            "risk": _RULE_RISKS["dry_run_off_full"],
            "reason": f"DRY_RUN=False mode={mode} — 전체 적용 위험",
        })

    if status == "error":
        # R21 격하 — dry_run 의 실패는 데이터 영향 없음.  info 로.
        risk = (
            th["dry_run_error_risk"] if is_dry else _RULE_RISKS["status_error"]
        )
        violations.append({
            "rule": "status_error",
            "risk": risk,
            "reason": (run.get("exc_message") or "status=error"),
        })

    return violations


def summarize(
    runs: List[Dict[str, Any]],
    window_days: int = 7,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """위험 백필 alert + 스크립트별 집계 dict 생성.

    Parameters
    ----------
    runs
        ``backfill_audit.jsonl`` 의 전체 row 리스트 (시간 순 무관).
    window_days
        최근 며칠만 평가할지.
    now
        기준 시각 (테스트용 주입).  None 이면 UTC now.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    th = _thresholds()

    in_window = [r for r in runs if _within_window(r, cutoff)]

    alerts: List[Dict[str, Any]] = []
    by_script: Dict[str, Dict[str, Any]] = {}
    # R24 트랙 E — 라운드 별 집계.  env.round 가 없으면 'unlabeled' 로 합산.
    by_round: Dict[str, Dict[str, Any]] = {}

    for run in in_window:
        sc = run.get("script") or "unknown"
        slot = by_script.setdefault(sc, {
            "runs": 0, "ok": 0, "error": 0, "violations": 0,
        })
        slot["runs"] += 1
        st = (run.get("status") or "").lower()
        if st == "ok":
            slot["ok"] += 1
        elif st == "error":
            slot["error"] += 1

        rnd_env = (run.get("env") or {}).get("round") or "unlabeled"
        rnd_slot = by_round.setdefault(str(rnd_env), {
            "runs": 0, "ok": 0, "error": 0, "violations": 0,
        })
        rnd_slot["runs"] += 1
        if st == "ok":
            rnd_slot["ok"] += 1
        elif st == "error":
            rnd_slot["error"] += 1

        for viol in _check_rules(run, thresholds=th):
            alerts.append({
                "run_id": run.get("run_id"),
                "script": sc,
                "mode": run.get("mode"),
                "started_at": run.get("started_at"),
                "round": str(rnd_env),
                "rule": viol["rule"],
                "risk": viol["risk"],
                "reason": viol["reason"],
            })
            slot["violations"] += 1
            rnd_slot["violations"] += 1

    # R21 노이즈 억제 — 윈도우 내 run 수가 floor 이하면 critical 이외는 suppress.
    # critical 은 단 1건이라도 즉시 보고 (R18 사고 직접 원인이므로 floor 무시).
    suppressed = 0
    if len(in_window) <= th["min_runs_for_alert"]:
        kept: List[Dict[str, Any]] = []
        for a in alerts:
            if a["risk"] == "critical":
                kept.append(a)
            else:
                suppressed += 1
        alerts = kept

    counts = {"critical": 0, "warning": 0, "info": 0}
    for a in alerts:
        risk = a["risk"]
        counts[risk] = counts.get(risk, 0) + 1

    payload = {
        "generated_at": now.isoformat(timespec="seconds"),
        "window_days": int(window_days),
        "total_runs": len(in_window),
        "alerts": alerts,
        "alert_counts": counts,
        "by_script": by_script,
        "by_round": by_round,
        "thresholds": {
            "min_runs_for_alert": th["min_runs_for_alert"],
            "preserve_exempt_scripts": sorted(th["preserve_exempt_scripts"]),
            "insert_only_scripts": sorted(th["insert_only_scripts"]),
            "backup_risk_with_preserve": th["backup_risk_with_preserve"],
            "dry_run_error_risk": th["dry_run_error_risk"],
            "suppressed_below_floor": suppressed,
        },
    }
    return payload


def run(
    *,
    window_days: int = 7,
    audit_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """1회 분석 실행.  audit_path 미지정 시 표준 경로 사용."""
    path = audit_path or _audit_path()
    runs = _load_jsonl(path)
    payload = summarize(runs, window_days=window_days, now=now)
    payload["audit_path"] = str(path)
    logger.info(
        "[backfill_audit_monitor] runs=%d alerts=%d critical=%d warning=%d",
        payload["total_runs"], len(payload["alerts"]),
        payload["alert_counts"].get("critical", 0),
        payload["alert_counts"].get("warning", 0),
    )
    return payload


def _parse_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="backfill_audit_monitor")
    p.add_argument("--days", type=int, default=7, help="윈도우 (일, 기본 7)")
    p.add_argument("--audit", type=str, default=None,
                   help="audit JSONL 경로 (기본: reports/backfill_audit.jsonl)")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_cli()
    payload = run(
        window_days=int(args.days),
        audit_path=Path(args.audit) if args.audit else None,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
