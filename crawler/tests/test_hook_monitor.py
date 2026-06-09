"""hook_monitor 단위 테스트 (R27 트랙 C).

요구: ≥ 1 케이스.

본 테스트는 1주 운영 시나리오 ─ 가짜 validator_hook_state.json + 가짜
workflow_validate_*.md + 일부 archive 디렉토리 실재 ─ 를 임시 디렉토리에
구성하고, ``hook_monitor.compute()`` 가
  1. 후크 상태 메타 (hook_active, scan_count)
  2. 1주 alerts/archive_drift 누계
  3. archive_drift_unresolved (R25 부재 / R26 실재 분리)
  4. false positive 분류 (persistent / resolved / clean)
  5. 운영 권고 문장
를 정확히 합성하는지 검증한다.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from insight.hook_monitor import compute  # noqa: E402


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_hook_monitor_aggregates_1week(tmp_path):
    """가짜 상태 파일 + workflow_validate 보고서 + 일부 archive 실재 케이스를
    구성하고 1주 통계가 정확한지 검증한다.

    시나리오:
      - state.history 4건:
          R25 alerts=2 archive_drift=["reports/archive/R25/ (missing)"]
          R26 alerts=0 archive_drift=[]
          R26 alerts=1 archive_drift=[]   (R26 두 번째 — last_alert=1 → persistent)
          R27 alerts=0 archive_drift=[]
      - archive 디렉토리: R26 만 실재 (R25 부재 → drift_unresolved)
      - workflow_validate 보고서 2편 — R25, R27 — 둘 다 windows 안 mtime
    """
    state_path = tmp_path / "reports" / "validator_hook_state.json"
    archive_dir = tmp_path / "reports" / "archive"
    reports_dir = tmp_path / "reports"

    history = [
        {
            "round": "R25",
            "report_path": "docs/dashboard/R25_X_2026-06-05.md",
            "scanned_at_utc": "2026-06-05T10:00:00+00:00",
            "report_mtime_utc": "2026-06-05T09:00:00+00:00",
            "claims_total": 5,
            "alerts": 2,
            "mean_abs_drift_pct": 18.5,
            "max_abs_drift_pct": 35.0,
            "archive_claims": ["reports/archive/R25/"],
            "archive_drift": ["reports/archive/R25/ (missing)"],
        },
        {
            "round": "R26",
            "report_path": "docs/dashboard/R26_Y_2026-06-05.md",
            "scanned_at_utc": "2026-06-05T11:00:00+00:00",
            "report_mtime_utc": "2026-06-05T10:30:00+00:00",
            "claims_total": 6,
            "alerts": 0,
            "mean_abs_drift_pct": 4.2,
            "max_abs_drift_pct": 9.5,
            "archive_claims": [],
            "archive_drift": [],
        },
        {
            "round": "R26",
            "report_path": "docs/dashboard/R26_Y_2026-06-05.md",
            "scanned_at_utc": "2026-06-05T12:00:00+00:00",
            "report_mtime_utc": "2026-06-05T11:55:00+00:00",
            "claims_total": 6,
            "alerts": 1,
            "mean_abs_drift_pct": 12.0,
            "max_abs_drift_pct": 12.0,
            "archive_claims": [],
            "archive_drift": [],
        },
        {
            "round": "R27",
            "report_path": "docs/dashboard/R27_Z_2026-06-06.md",
            "scanned_at_utc": "2026-06-06T01:00:00+00:00",
            "report_mtime_utc": "2026-06-06T00:55:00+00:00",
            "claims_total": 4,
            "alerts": 0,
            "mean_abs_drift_pct": 2.0,
            "max_abs_drift_pct": 3.0,
            "archive_claims": [],
            "archive_drift": [],
        },
    ]
    state = {
        "hook_active": True,
        "scan_count": 1200,
        "last_scan_utc": "2026-06-06T01:00:00+00:00",
        "last_alerts_total": 0,
        "last_archive_drift_total": 0,
        "history": history,
    }
    _write(state_path, json.dumps(state, ensure_ascii=False, indent=2))

    # archive 디렉토리: R26 만 생성 (R25 부재).
    (archive_dir / "R26").mkdir(parents=True)

    # workflow_validate 보고서 2편 — 윈도우 안 mtime 자동 (방금 생성).
    _write(reports_dir / "workflow_validate_R25.md",
           "# validate R25\n\nalerts: 2\nmean |Δ|%: 18.5\n")
    _write(reports_dir / "workflow_validate_R27.md",
           "# validate R27\n\nalerts: 0\nmean |Δ|%: 2.0\n")
    # 오래된 R20 — 30일 이전 mtime 강제 (윈도우 밖, 집계 미포함 검증).
    old_p = _write(reports_dir / "workflow_validate_R20.md",
                   "# validate R20\n\nalerts: 1\nmean |Δ|%: 5.0\n")
    old_ts = time.time() - 40 * 86400
    os.utime(old_p, (old_ts, old_ts))

    result = compute(
        days=7,
        state_path=state_path,
        reports_dir=reports_dir,
        archive_dir=archive_dir,
    )

    # 1) 기본 가용성 + 상태 메타.
    assert result["available"] is True
    assert result["days"] == 7
    assert result["state"]["hook_active"] is True
    assert result["state"]["scan_count"] == 1200

    # 2) summary 누계.
    sm = result["summary"]
    assert sm["history_entries"] == 4
    assert sm["alerts_total"] == 3  # 2 + 0 + 1 + 0
    assert sm["archive_drift_total"] == 1
    # 윈도우 7일 안에 R25/R27 2편 — R20 은 40일 전이라 제외.
    assert sm["validate_reports"] == 2
    # mean drift: (18.5 + 4.2 + 12.0 + 2.0) / 4 = 9.175.
    assert 9.0 < sm["mean_drift_pct"] < 9.5
    # max drift: 35.0.
    assert sm["max_drift_pct"] == 35.0

    # 3) archive 교차 확인.
    assert result["archive_existing"] == ["R26"]
    # R25 는 부재 → unresolved 에 포함.
    assert "R25" in result["archive_drift_unresolved"]

    # 4) false positive 분류.
    fp = result["false_positive_analysis"]
    # R26 은 2회 등장 — 마지막 alerts=1 → persistent.
    assert "R26" in fp["persistent_unresolved"]
    # R27 은 1회 alerts=0 → clean.
    assert "R27" in fp["clean"]
    # R25 는 1회 alerts=2 → one_shot_with_alerts.
    assert "R25" in fp["one_shot_with_alerts"]

    # 5) 권고 — archive_drift_unresolved 가 있으니 관련 권고 1건 이상.
    recs_joined = " ".join(result["recommendations"])
    assert "archive_drift" in recs_joined or "R25" in recs_joined or "persistent" in recs_joined

    # 6) validate_reports 구조 — 라운드/사이즈/mtime 채워짐.
    rounds_in = {r["round"] for r in result["validate_reports"]}
    assert "R25" in rounds_in
    assert "R27" in rounds_in
    assert "R20" not in rounds_in  # 윈도우 밖
    for r in result["validate_reports"]:
        assert r["size_bytes"] > 0
        assert r["mtime_utc"] is not None


def test_hook_monitor_no_state_graceful(tmp_path):
    """상태 파일 부재 시 available=False + reason 명시.  운영 차단 금지 (graceful)."""
    state_path = tmp_path / "reports" / "validator_hook_state.json"  # 미생성
    result = compute(
        days=7,
        state_path=state_path,
        reports_dir=tmp_path / "reports",
        archive_dir=tmp_path / "reports" / "archive",
    )
    assert result["available"] is False
    assert "reason" in result
    assert "state file 부재" in result["reason"]
