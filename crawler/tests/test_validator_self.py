"""workflow_validator 자기 적용 단위 테스트 (R23 트랙 B).

목표
====
R22 보고서 (`docs/dashboard/R22_RELIABILITY_2026-06-05.md`) 를 *그 자체* 가
입력이 되도록 validator 를 자기 적용한다. 이는 R22 권고 4번 — "B hook 을
R22 보고서 자체에 자기 적용 (재귀 검증)" — 의 최소 단위 검증.

가짜 live 측정 dict 를 주입하여 backend 비가용 환경에서도 결정론적으로
실행 가능. R22 가 보고서에 적은 *최소 1개* claim (voc_total=118,541 in
`## 8. 측정 수치 종합` 표) 이 자동 추출되고, drift 값이 가짜 실측 대비
정확히 계산되는지 확인.

요구: ≥ 1 케이스 (PLAN.md).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from insight.workflow_validator import parse_report  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
R22_REPORT = REPO_ROOT / "docs" / "dashboard" / "R22_RELIABILITY_2026-06-05.md"


@pytest.mark.skipif(not R22_REPORT.is_file(), reason="R22 report missing")
def test_validator_self_applied_to_r22_recovers_voc_total_claim():
    """R22 보고서 본문에서 `voc_total | 118,541 | 120,179` 표 셀의 *첫 숫자*
    (118,541) 가 정상 추출되어 가짜 실측 120,179 와 비교될 때 drift 가
    ≈ +1.56% 로 계산되어야 한다.

    이는 R22 자기 보고 drift 4번 ("B hook 자기 적용") 의 최소 회귀 검증 —
    validator 가 자기 자신의 산출 보고서를 *읽고 분석 가능* 함을 보장.
    """
    fake_live = {
        "available": {"regression": True, "coverage": True, "topic_eval": False},
        "metrics": {
            # R22 시점 실측 voc_total 120,179. 본 테스트는 *값 자체* 가 아니라
            # validator 가 보고서에서 동일 metric 을 *읽어내는가* 를 본다.
            "voc_total": 120179,
        },
        "sources": {"voc_total": "regression-baseline"},
        "backend": "http://test",
        "generated_at_utc": "2026-06-05T21:34:00+00:00",
    }

    claims = parse_report(R22_REPORT, fake_live, threshold=0.10)
    assert claims, "R22 보고서에서 0건 claim — 정규식이 본문을 한 줄도 못 잡음"

    voc_claims = [c for c in claims if c.metric == "voc_total"]
    assert voc_claims, "voc_total claim 미검출 (보고서 § 8 표 누락 가능)"

    # § 8 표의 R21 reported 값 118,541 이 후보 중 하나여야 한다.
    by_reported = {c.reported: c for c in voc_claims}
    assert 118541.0 in by_reported, (
        f"voc_total=118,541 claim 미검출. 검출된 reported={list(by_reported)}"
    )

    c = by_reported[118541.0]
    assert c.actual == 120179.0
    # drift = +1638 / max(118541, 120179) = +0.01362
    assert c.drift_pct == pytest.approx(0.0136, abs=2e-3)
    # 1.36% < 10% threshold → alert 미발화.
    assert c.alert is False
    # 본 라인은 보고서 § 8 표 (line 103) 근처 — 정확한 라인 번호는 보고서
    # 재편집 시 변동 가능하므로 *> 50* 으로 약하게 검증.
    assert c.source_line > 50
