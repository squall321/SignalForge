"""workflow_validator_hook 단위 테스트 (R26 트랙).

요구: ≥ 2 케이스.

1. ``test_hook_detects_drift_and_persists_state`` — 가짜 보고서 + 가짜 live 측정
   주입, 후크 1회 실행 시 drift > 10% 가 alert 으로 캡처되고 상태 파일이
   영구화되는지.
2. ``test_hook_captures_archive_path_drift`` — 보고서가 ``archive/R25/`` 경로를
   주장하지만 실제 파일시스템에 부재할 때 ``archive_drift`` 필드로 자동 캡처
   되는지 (R25 회고 사고 모델).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from insight.workflow_validator_hook import run, status  # noqa: E402


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# ── 케이스 1 ──────────────────────────────────────────────────────────────
def test_hook_detects_drift_and_persists_state(tmp_path):
    """보고서 본문 sentiment_pct=100% vs 실측 88.5 → drift -11.5% (>10% threshold)
    → alert=1, 상태 파일에 history 1건 영구화.

    재실행 ``status()`` 가 hook_active=True + history 보존.
    """
    # 가짜 docs/dashboard 디렉토리 + 보고서 1편.
    dash = tmp_path / "docs" / "dashboard"
    report = dash / "R99_TESTHOOK_2026-06-05.md"
    body = (
        "# R99 TESTHOOK\n"
        "\n"
        "| 지표 | 값 |\n"
        "|---|---|\n"
        "| voc_total | 118,430 |\n"
        "| sentiment % | 100.00% |\n"
    )
    _write(report, body)

    state_path = tmp_path / "reports" / "validator_hook_state.json"
    # sentiment source 가 "approx" 면 alert 억제 → 명시적으로 *비*-approx 로
    # 설정해서 drift 가 alert 으로 캡처되는지 검증.
    fake_live = {
        "available": {"regression": True, "coverage": True, "topic_eval": False},
        "metrics": {
            "voc_total": 119981,   # drift +1.3% (alert 미발생)
            "sentiment_pct": 88.5,  # drift -11.5% (alert 발생)
        },
        "sources": {
            "voc_total": "regression-baseline",
            "sentiment_pct": "regression-baseline",  # approx 아님 → alert 살아남음.
        },
        "backend": "http://test",
        "generated_at_utc": "2026-06-05T22:13:00+00:00",
    }

    out = run(
        state_path=state_path,
        scan_dirs=[dash],
        force_all=True,
        live_override=fake_live,
        threshold=0.10,
    )

    assert out["hook_active"] is True
    assert out["scanned_count"] == 1
    # sentiment_pct drift -11.5% → alert 1건 이상.
    assert out["alerts_total"] >= 1
    # results 내 round 식별 + alerts 필드 확인.
    r = out["results"][0]
    assert r["round"] == "R99"
    assert r["alerts"] >= 1
    # 상태 파일 영구화 확인.
    assert state_path.is_file()
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["hook_active"] is True
    assert saved["scan_count"] == 1
    assert len(saved["history"]) == 1
    assert saved["history"][0]["round"] == "R99"

    # status() 가 history 를 재현.
    st = status(state_path=state_path)
    assert st["hook_active"] is True
    assert st["scan_count"] == 1
    assert len(st["history"]) == 1
    assert st["history"][0]["round"] == "R99"


# ── 케이스 2 ──────────────────────────────────────────────────────────────
def test_hook_captures_archive_path_drift(tmp_path):
    """R25 회고 사고 — 보고서가 ``reports/archive/R25/`` 경로 영구 기록을
    주장하지만 실제 디렉토리는 부재한 경우, ``archive_drift`` 가 자동 캡처되어야.

    또한 *실재* 디렉토리 (tmp_path 내 생성) 는 drift 미캡처.
    """
    # 가짜 보고서 본문에 archive 경로 2개 — R25 (부재), R26 (생성).
    dash = tmp_path / "docs" / "dashboard"
    report = dash / "R26_ARCHIVE_2026-06-05.md"
    body = (
        "# R26 ARCHIVE\n"
        "\n"
        "audit JSONL 영구 기록: `reports/archive/R25/audit.jsonl` 와 "
        "`reports/archive/R26/` 에 저장. R25 는 회고로 *부재* 확인됨.\n"
    )
    _write(report, body)

    # R26 archive 디렉토리는 실재 — drift 없어야.  REPO_ROOT 기준 절대 경로
    # 검증이므로 module 의 REPO_ROOT 를 tmp_path 로 monkeypatch.
    import insight.workflow_validator_hook as hook_mod

    state_path = tmp_path / "reports" / "validator_hook_state.json"
    # tmp_path 안에 실재할 archive 디렉토리.
    (tmp_path / "reports" / "archive" / "R26").mkdir(parents=True)
    # R25 는 일부러 만들지 않음 (회고 사고 모델).

    # REPO_ROOT 를 tmp_path 로 임시 교체 — _check_archive_drift 의 경로 해석
    # 대상이 tmp_path 기준이 되도록.
    orig_root = hook_mod.REPO_ROOT
    hook_mod.REPO_ROOT = tmp_path
    try:
        fake_live = {
            "available": {"regression": True, "coverage": True, "topic_eval": False},
            "metrics": {"voc_total": 119981},
            "sources": {},
            "backend": "http://test",
            "generated_at_utc": "2026-06-05T22:13:00+00:00",
        }
        out = run(
            state_path=state_path,
            scan_dirs=[dash],
            force_all=True,
            live_override=fake_live,
            threshold=0.10,
        )
    finally:
        hook_mod.REPO_ROOT = orig_root

    assert out["scanned_count"] == 1
    r = out["results"][0]
    # archive_claims 에 R25, R26 둘 다 포함.
    assert any("R25" in s for s in r["archive_claims"])
    assert any("R26" in s for s in r["archive_claims"])
    # archive_drift 에는 R25 (부재) 만 — R26 은 디렉토리 실재.
    assert any("R25" in s and "missing" in s for s in r["archive_drift"])
    assert not any("R26" in s and "missing" in s for s in r["archive_drift"])
    assert out["archive_drift_total"] >= 1
