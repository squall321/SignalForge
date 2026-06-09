"""R27 D 트랙 — trust 7일 분포 + 임계 정밀화 단위 테스트.

요구: 1 케이스.  본 테스트는 다음을 한꺼번에 확인한다:
- drift 보고서 (합성) + audit JSONL (합성) 의 trust 표본을 결합한 분포 산출.
- 백분위수 (P25 / P50 / P75) 가 sorted 값과 일치.
- 임계 권고가 P25/P50 기반 데이터-드리븐 룰 (tighten / loosen / keep_current)
  중 하나로 분류.
- audit_path=None 일 때 drift 만으로 분포 산출 (graceful).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from insight.workflow_drift_stats import (  # noqa: E402
    compute_trust_7day_distribution,
    _percentile,
)


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_trust_7day_distribution(tmp_path):
    """drift + audit 합성 표본으로 7일 trust 분포 + 권고 산출."""
    # ── 합성 drift 보고서 (R20 형식, trust 가 warning 영역 ~76 으로 떨어지는
    #    LoC drift 1건 포함) ────────────────────────────────────────────────
    reports = tmp_path / "dashboard"
    reports.mkdir()
    r20 = reports / "R20_TEST_2026-06-05.md"
    _write(r20, (
        "# R20 TEST\n"
        "\n"
        "| 트랙 | 산출 | 검증 |\n"
        "|------|------|------|\n"
        "| **B. Crisis 한국** | (실측 509 lines, 보고 358 lines 차이) | ok |\n"
        "\n"
    ))
    r21 = reports / "R21_TEST_2026-06-05.md"
    _write(r21, (
        "# R21 TEST\n"
        "\n"
        "| 트랙 | 산출 | 검증 |\n"
        "|------|------|------|\n"
        "| A LLM apply | x | 보고 322 vs 실측 446 LoC |\n"
        "\n"
    ))

    # ── 합성 audit JSONL ─────────────────────────────────────────────────
    # backfill_audit_monitor.summarize() 는 round 라벨을 ``env.round`` 에서 읽고
    # 규칙은 _check_rules 가 판정.  DRY_RUN=True + PRESERVE_EXISTING=True 면 위반 0.
    # 위반을 강제하려면 mode=full + DRY_RUN=False 또는 status=error 사용.
    # 현재 시각 기준 N 일 윈도우에 들어오도록 utcnow 사용.
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    audit_path = tmp_path / "audit.jsonl"

    def _row(rnd, status, mode="dry_run", dry_run=True, preserve=True):
        return {
            "run_id": f"{rnd}_{status}",
            "script": "topic_backfill",
            "mode": mode,
            "env": {
                "DRY_RUN": dry_run,
                "PRESERVE_EXISTING": preserve,
                "BACKUP_BEFORE": False,
                "round": rnd,
            },
            "started_at": now_iso,
            "finished_at": now_iso,
            "status": status,
            "exc_message": None,
            "counters": {"target_total": 100, "seen": 100, "matched": 1},
            "backup_path": None,
            "notes": [],
        }

    audit_lines = [
        # R24 — 모두 ok, dry_run, 위반 0 → trust 100 / normal
        _row("R24", "ok"),
        _row("R24", "ok"),
        # R25 — ok 1 / error 1 (dry_run 에서 error 는 info 위반) → trust 흔들림
        _row("R25", "ok"),
        _row("R25", "error"),
        # R26 — full mode + DRY_RUN=False + PRESERVE=False → critical 위반
        _row("R26", "ok", mode="full", dry_run=False, preserve=False),
    ]
    with audit_path.open("w", encoding="utf-8") as fh:
        for row in audit_lines:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ── 실행 ─────────────────────────────────────────────────────────────
    out = compute_trust_7day_distribution(
        reports_dir=reports,
        audit_path=audit_path,
        days=7,
    )

    # 기본 schema
    assert out["available"] is True
    assert "trust_samples" in out and "stats" in out and "recommendation" in out

    drift = out["trust_samples"]["drift"]
    audit = out["trust_samples"]["audit"]
    combined = out["trust_samples"]["combined"]

    # drift 표본 — R20 / R21 각 1건씩
    rounds_drift = {s["round"] for s in drift}
    assert "R20" in rounds_drift and "R21" in rounds_drift

    # audit 표본 — R24/R25/R26 각 1건씩
    rounds_audit = {s["round"] for s in audit}
    assert {"R24", "R25", "R26"}.issubset(rounds_audit)

    # combined 길이 == drift + audit 길이
    assert len(combined) == len(drift) + len(audit)

    # 통계
    stats = out["stats"]
    assert stats["n"] == len(combined)
    assert stats["min"] <= stats["median"] <= stats["max"]
    assert stats["p25"] <= stats["p50"] <= stats["p75"]
    # min/max 가 실제 sample 의 min/max
    assert stats["min"] == pytest.approx(min(combined), abs=0.1)
    assert stats["max"] == pytest.approx(max(combined), abs=0.1)

    # 백분위수 sanity (5개 값 [50, 50, 76.5, 80.1, 100] 예시)
    sorted_v = sorted(combined)
    assert _percentile(sorted_v, 0) == pytest.approx(sorted_v[0], abs=0.1)
    assert _percentile(sorted_v, 100) == pytest.approx(sorted_v[-1], abs=0.1)
    assert _percentile(sorted_v, 50) == pytest.approx(
        out["stats"]["median"], abs=0.5
    )

    # 권고 — n >= 3 보장됨, action 은 tighten/loosen/keep_current 중 하나
    rec = out["recommendation"]
    assert rec["action"] in ("tighten", "loosen", "keep_current")
    assert "suggested_critical_below" in rec
    assert "suggested_warning_below" in rec
    # critical < warning 보장
    assert (
        rec["suggested_critical_below"] < rec["suggested_warning_below"]
    )
    assert "rationale" in rec and rec["rationale"]

    # current_thresholds 가 env (기본 60/80) 와 일치
    cur = out["current_thresholds"]
    assert cur["critical_below"] == pytest.approx(60.0)
    assert cur["warning_below"] == pytest.approx(80.0)

    # ── audit_path=None — drift 만으로 graceful ─────────────────────────
    out2 = compute_trust_7day_distribution(
        reports_dir=reports,
        audit_path=None,
        days=7,
    )
    assert out2["available"] is True
    assert len(out2["trust_samples"]["audit"]) == 0
    assert len(out2["trust_samples"]["drift"]) >= 2

    # ── 둘 다 비면 insufficient_samples ─────────────────────────────────
    empty_dir = tmp_path / "empty_dashboard"
    empty_dir.mkdir()
    out3 = compute_trust_7day_distribution(
        reports_dir=empty_dir,
        audit_path=None,
        days=7,
    )
    assert out3["available"] is False
    assert out3["recommendation"]["action"] == "insufficient_samples"
