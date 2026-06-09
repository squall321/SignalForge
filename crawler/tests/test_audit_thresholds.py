"""R21 트랙 E — backfill audit 임계 조정 단위 테스트.

R20 의 ``test_backfill_audit_monitor.py`` 는 *기본* 규칙 동작을 검증.
본 테스트는 *환경변수로 주입한 임계가 실제 격하/면제/억제로 이어지는지* 확인.

검증 1건 (압축, Discovery 사양):
  - dedup_voc (PRESERVE 미설정 + BACKUP=False, 실 실행) 가
    AUDIT_PRESERVE_EXEMPT_SCRIPTS=dedup_voc 면 critical 1건만 (preserve 면제,
    backup 은 preserve_ok=False 라 여전히 critical).
  - PRESERVE=True + BACKUP=False 인 일반 백필이 BACKUP_RISK_WITH_PRESERVE=warning
    덕분에 critical 0, warning 1 로 격하.
  - dry_run 의 status=error 가 DRY_RUN_ERROR_RISK=info 로 격하.
  - 윈도우 총 run 2건이 MIN_RUNS_FOR_ALERT=3 보다 작으면
    critical 은 유지 / warning·info 는 suppress (suppressed 카운트 반영).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.backfill_audit_monitor import summarize  # noqa: E402


def _row(**kw) -> dict:
    base = {
        "run_id": "x",
        "script": "topic_backfill",
        "mode": "preserve_existing",
        "env": {},
        "started_at": "2026-06-04T12:00:00+00:00",
        "finished_at": "2026-06-04T12:00:01+00:00",
        "status": "ok",
        "exc_message": None,
        "counters": {},
        "backup_path": None,
        "notes": [],
    }
    base.update(kw)
    return base


def test_r21_thresholds_apply_demotions_exemptions_and_floor(monkeypatch):
    """4개 임계가 한 시뮬레이션 안에서 모두 의도대로 동작.

    구성:
      A) dedup_voc 실 실행, PRESERVE 미설정 + BACKUP=False
         → PRESERVE 면제 ON, BACKUP 은 preserve_ok=False 라 critical 유지 (1 critical).
      B) topic_backfill 실 실행, PRESERVE=True + BACKUP=False
         → BACKUP 격하 → warning 1.
      C) topic_backfill dry_run + status=error
         → status_error 가 info 로 격하.

    그리고 윈도우 총 3 run 이 MIN_RUNS_FOR_ALERT=3 (==floor) → critical 만 유지,
    warning/info 는 suppress.
    """
    monkeypatch.setenv("AUDIT_PRESERVE_EXEMPT_SCRIPTS", "dedup_voc")
    monkeypatch.setenv("AUDIT_BACKUP_RISK_WITH_PRESERVE", "warning")
    monkeypatch.setenv("AUDIT_DRY_RUN_ERROR_RISK", "info")
    monkeypatch.setenv("AUDIT_MIN_RUNS_FOR_ALERT", "3")

    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    runs = [
        # A) dedup_voc — PRESERVE 면제 적용, BACKUP critical 만 (preserve_ok=False).
        _row(
            run_id="A_dedup",
            script="dedup_voc",
            mode="execute",
            env={"DRY_RUN": False, "BACKUP_BEFORE": False},
        ),
        # B) PRESERVE=True 안전 백필 + BACKUP 누락 → 격하해서 warning 만.
        _row(
            run_id="B_topic_safe",
            script="topic_backfill",
            mode="preserve_existing",
            env={"DRY_RUN": False, "PRESERVE_EXISTING": True, "BACKUP_BEFORE": False},
        ),
        # C) dry_run + status=error → info 격하.
        _row(
            run_id="C_dry_err",
            script="topic_backfill",
            mode="dry_run",
            env={"DRY_RUN": True, "PRESERVE_EXISTING": True, "BACKUP_BEFORE": False},
            status="error",
            exc_message="RuntimeError: ollama timeout",
        ),
    ]

    payload = summarize(runs, window_days=7, now=now)

    # 임계 echo 확인.
    th = payload["thresholds"]
    assert th["preserve_exempt_scripts"] == ["dedup_voc"]
    assert th["backup_risk_with_preserve"] == "warning"
    assert th["dry_run_error_risk"] == "info"
    assert th["min_runs_for_alert"] == 3

    # 총 run 3 == floor → critical 만 유지, warning/info suppress.
    assert payload["total_runs"] == 3
    counts = payload["alert_counts"]
    assert counts["critical"] == 1, "A_dedup 의 backup_disabled (preserve_ok=False)"
    assert counts["warning"] == 0, "B 의 backup_disabled 는 격하 후 floor 로 suppress"
    assert counts["info"] == 0, "C 의 status_error 는 격하 후 floor 로 suppress"
    assert th["suppressed_below_floor"] == 2, "B warning + C info = 2 suppressed"

    # 유일하게 보고된 alert 는 A_dedup 의 backup_disabled.
    assert len(payload["alerts"]) == 1
    a = payload["alerts"][0]
    assert a["run_id"] == "A_dedup"
    assert a["rule"] == "backup_disabled"
    assert a["risk"] == "critical"
