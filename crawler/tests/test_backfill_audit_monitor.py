"""insight.backfill_audit_monitor 단위 테스트 (R20 트랙 E).

DB / HTTP / 파일 의존성 없음 — 순수 summarize() 로직 검증.

검증:
  - dry_run 실행은 위반 없음 (PRESERVE/BACKUP 검사 제외).
  - 실 실행 중 PRESERVE_EXISTING=False → critical 'preserve_existing_off'.
  - 실 실행 중 BACKUP_BEFORE=False → critical 'backup_disabled'.
  - DRY_RUN=False + mode=full → warning 'dry_run_off_full'.
  - status=error → warning 'status_error'.
  - by_script 누적 카운트 (runs/ok/error/violations) 정확.
  - 윈도우 밖 row 는 제외.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.backfill_audit_monitor import summarize  # noqa: E402


def _run(
    *,
    run_id: str,
    script: str,
    mode: str,
    env: dict,
    started_at: str,
    status: str = "ok",
    exc_message: str | None = None,
) -> dict:
    """헬퍼: backfill_audit.jsonl 한 줄 형태 dict."""
    return {
        "run_id": run_id,
        "script": script,
        "mode": mode,
        "env": env,
        "started_at": started_at,
        "finished_at": started_at,
        "status": status,
        "exc_message": exc_message,
        "counters": {},
        "backup_path": None,
        "notes": [],
    }


def test_summarize_detects_unsafe_full_backfill_and_safe_dry_run():
    """dry_run 은 위반 0, 실 백필 중 PRESERVE 미설정 + BACKUP 비활성 = critical 2종."""
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    base = "2026-06-04T12:00:00+00:00"  # 윈도우 안 (1일 전)

    runs = [
        # 1) 안전: dry_run 모드 — 어떤 env 든 위반 없어야 함.
        _run(
            run_id="dry01", script="topic_backfill", mode="dry_run",
            env={"DRY_RUN": True, "PRESERVE_EXISTING": False, "BACKUP_BEFORE": False},
            started_at=base,
        ),
        # 2) 위험: 실 적용 + PRESERVE_EXISTING=False + BACKUP_BEFORE=False.
        _run(
            run_id="bad01", script="topic_backfill", mode="preserve_existing",
            env={"DRY_RUN": False, "PRESERVE_EXISTING": False, "BACKUP_BEFORE": False},
            started_at=base,
        ),
        # 3) 실패: status=error.
        _run(
            run_id="err01", script="sentiment_backfill", mode="preserve_existing",
            env={"DRY_RUN": False, "PRESERVE_EXISTING": True, "BACKUP_BEFORE": True},
            started_at=base,
            status="error",
            exc_message="RuntimeError: pg connection lost",
        ),
        # 4) DRY_RUN 우회 + 전체 적용.
        _run(
            run_id="full01", script="dedup_voc", mode="full_reclassify",
            env={"DRY_RUN": False, "PRESERVE_EXISTING": True, "BACKUP_BEFORE": True},
            started_at=base,
        ),
        # 5) 윈도우 밖 (10일 전) — 결과에서 제외되어야 함.
        _run(
            run_id="old01", script="topic_backfill", mode="full_reclassify",
            env={"DRY_RUN": False, "PRESERVE_EXISTING": False, "BACKUP_BEFORE": False},
            started_at="2026-05-25T12:00:00+00:00",
        ),
    ]

    payload = summarize(runs, window_days=7, now=now)

    # 윈도우 통과 4건 (5번은 제외)
    assert payload["total_runs"] == 4
    assert payload["window_days"] == 7

    # alert 매핑 — run_id 기준으로 어떤 rule 이 잡혔는지 확인.
    by_run: dict[str, list[str]] = {}
    for a in payload["alerts"]:
        by_run.setdefault(a["run_id"], []).append(a["rule"])

    # dry_run 은 위반 없음 (안전 검증)
    assert "dry01" not in by_run, "dry_run mode 는 위반 보고에서 제외되어야 함"
    # 윈도우 밖 row 도 위반 없음
    assert "old01" not in by_run

    # 실 백필 + PRESERVE off + BACKUP off → 두 critical
    assert "preserve_existing_off" in by_run["bad01"]
    assert "backup_disabled" in by_run["bad01"]

    # status=error 만 (env 는 안전)
    assert by_run["err01"] == ["status_error"]

    # DRY_RUN 우회 — preserve/backup 은 True 라 통과, dry_run_off_full 만.
    assert by_run["full01"] == ["dry_run_off_full"]

    # 위험도 카운트
    counts = payload["alert_counts"]
    assert counts["critical"] == 2     # bad01 의 preserve + backup
    assert counts["warning"] == 2      # err01 status_error + full01 dry_run_off_full
    assert counts.get("info", 0) == 0

    # by_script 집계
    by_script = payload["by_script"]
    assert by_script["topic_backfill"]["runs"] == 2     # dry01 + bad01
    assert by_script["topic_backfill"]["ok"] == 2
    assert by_script["topic_backfill"]["error"] == 0
    assert by_script["topic_backfill"]["violations"] == 2  # bad01 의 2건
    assert by_script["sentiment_backfill"]["runs"] == 1
    assert by_script["sentiment_backfill"]["error"] == 1
    assert by_script["sentiment_backfill"]["violations"] == 1
    assert by_script["dedup_voc"]["runs"] == 1
    assert by_script["dedup_voc"]["violations"] == 1
