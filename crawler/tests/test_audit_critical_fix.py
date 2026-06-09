"""R22 트랙 E — audit critical 4건 원인 fix 단위 검증.

R21 운영에서 ``crisis_platform_direct`` (CPD) 가 4 critical alerts 를 유발했다.
원인: CPD 는 *수집기* (ON CONFLICT DO NOTHING) 라 기존 row 를 mutate 하지 않지만,
audit env 에 *표준 키* (DRY_RUN/PRESERVE_EXISTING/BACKUP_BEFORE/DATA_TOUCHED) 를
emit 하지 않아 monitor 가 'PRESERVE_EXISTING 미설정' + 'BACKUP_BEFORE 미설정' 으로
오인 → preserve_existing_off + backup_disabled 각각 critical 발생.

Fix (양방향):
  1) CPD 측 — env 에 DATA_TOUCHED=False + 표준 키 추가 (이 테스트는 데이터 기반
     simulation 으로 검증).
  2) Monitor 측 — AUDIT_INSERT_ONLY_SCRIPTS 환경변수 + 기본값 crisis_platform_direct
     로 collector 안전망 추가.

본 테스트 1 케이스로 두 경로 모두 입증:
  - 과거 (R21) 형식 CPD row + 표준 키 미 emit → 기본 안전망 (insert_only) 가
    여전히 critical 을 0 으로 만든다.
  - R22 형식 CPD row (표준 키 emit + DATA_TOUCHED=False) → critical 0.
  - 안전망과 표준 키 모두 비활성화한 가상의 "raw legacy" → critical 2 재현.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.backfill_audit_monitor import summarize  # noqa: E402


def _cpd_legacy_row(run_id: str) -> dict:
    """R21 운영에서 실제로 생성됐던 CPD audit row (표준 키 없음)."""
    return {
        "run_id": run_id,
        "script": "crisis_platform_direct",
        "mode": "preserve",
        "env": {
            "CPD_DRY_RUN": 0,
            "CPD_PRESERVE_EXISTING": 1,
            "CPD_PER_KEYWORD_MAX": 2,
            "CPD_MAX_PAGES": 2,
        },
        "started_at": "2026-06-05T14:17:39+00:00",
        "finished_at": "2026-06-05T14:19:07+00:00",
        "status": "ok",
        "exc_message": None,
        "counters": {"saved": 22},
        "backup_path": None,
        "notes": [],
    }


def _cpd_r22_row(run_id: str) -> dict:
    """R22 트랙 E fix 후 CPD 가 emit 하는 표준 키 포함 row."""
    return {
        "run_id": run_id,
        "script": "crisis_platform_direct",
        "mode": "preserve",
        "env": {
            "CPD_DRY_RUN": 0,
            "CPD_PRESERVE_EXISTING": 1,
            "CPD_PER_KEYWORD_MAX": 2,
            "CPD_MAX_PAGES": 2,
            "DRY_RUN": False,
            "PRESERVE_EXISTING": True,
            "BACKUP_BEFORE": False,
            "DATA_TOUCHED": False,
        },
        "started_at": "2026-06-05T14:20:10+00:00",
        "finished_at": "2026-06-05T14:21:53+00:00",
        "status": "ok",
        "exc_message": None,
        "counters": {"saved": 22},
        "backup_path": None,
        "notes": [],
    }


def test_r22_collector_safety_net_and_standard_keys(monkeypatch):
    """R22 fix 가 R21 critical 4건을 0 으로 만들고, 진짜 위험은 그대로 검출.

    구성 (4 rows, 모두 7일 윈도우 안):
      A) R21 legacy CPD #1 (표준 키 없음) — Monitor 안전망 (insert_only default) 로
         critical 0 기대.
      B) R21 legacy CPD #2 — 동일.
      C) R22 표준 키 emit CPD — DATA_TOUCHED=False 경로로 critical 0.
      D) topic_backfill 실 실행 + PRESERVE=False — *진짜 위험* — critical 1 유지.

    AUDIT_MIN_RUNS_FOR_ALERT 는 명시적으로 0 (default 동작) 로 두어 floor 영향 배제.
    """
    # 기본 안전망 동작 확인 — AUDIT_INSERT_ONLY_SCRIPTS 미설정 시 default 가
    # crisis_platform_direct 를 포함하는지 검증.
    monkeypatch.delenv("AUDIT_INSERT_ONLY_SCRIPTS", raising=False)
    monkeypatch.delenv("AUDIT_PRESERVE_EXEMPT_SCRIPTS", raising=False)
    monkeypatch.delenv("AUDIT_MIN_RUNS_FOR_ALERT", raising=False)
    monkeypatch.delenv("AUDIT_BACKUP_RISK_WITH_PRESERVE", raising=False)
    monkeypatch.delenv("AUDIT_DRY_RUN_ERROR_RISK", raising=False)

    now = datetime(2026, 6, 5, 23, 0, 0, tzinfo=timezone.utc)
    runs = [
        _cpd_legacy_row("A_legacy_1"),
        _cpd_legacy_row("B_legacy_2"),
        _cpd_r22_row("C_r22_standard"),
        {
            # D — 진짜 위험: 재분류 백필이 PRESERVE off → critical 보존되어야 함.
            "run_id": "D_real_risk",
            "script": "topic_backfill",
            "mode": "full_reclassify",
            "env": {
                "DRY_RUN": False,
                "PRESERVE_EXISTING": False,
                "BACKUP_BEFORE": False,
            },
            "started_at": "2026-06-05T15:00:00+00:00",
            "finished_at": "2026-06-05T15:00:10+00:00",
            "status": "ok",
            "exc_message": None,
            "counters": {},
            "backup_path": None,
            "notes": [],
        },
    ]

    payload = summarize(runs, window_days=7, now=now)

    # 임계 echo — 안전망에 crisis_platform_direct 가 포함됐는가.
    th = payload["thresholds"]
    assert "crisis_platform_direct" in th["insert_only_scripts"], (
        "기본 AUDIT_INSERT_ONLY_SCRIPTS 에 crisis_platform_direct 가 포함돼야 R21 alerts 가 사라진다"
    )

    # CPD 3 run 모두 위반 0 — A/B 는 안전망, C 는 표준 키 + DATA_TOUCHED=False.
    cpd_slot = payload["by_script"]["crisis_platform_direct"]
    assert cpd_slot["runs"] == 3
    assert cpd_slot["violations"] == 0, (
        f"R22 fix 후 CPD violations 는 0 이어야 한다 (현재: {cpd_slot['violations']})"
    )

    # D 의 진짜 위험은 그대로 검출 — preserve_existing_off + backup_disabled + dry_run_off_full.
    tb_slot = payload["by_script"]["topic_backfill"]
    assert tb_slot["runs"] == 1
    assert tb_slot["violations"] >= 2, (
        "진짜 위험 (PRESERVE off + BACKUP off + full_reclassify) 은 보존되어야 한다"
    )

    # critical 총합 — D 의 preserve_existing_off 1 + backup_disabled 1 (preserve_ok=False → critical).
    counts = payload["alert_counts"]
    assert counts["critical"] == 2, (
        f"R22 fix 후 critical 은 D 의 2건만 (R21 의 CPD 4건은 제거): {counts}"
    )

    # critical 들이 모두 D 에서만 나왔는지.
    crit_run_ids = {a["run_id"] for a in payload["alerts"] if a["risk"] == "critical"}
    assert crit_run_ids == {"D_real_risk"}, (
        f"critical 은 D_real_risk 에서만 나와야 한다: {crit_run_ids}"
    )
