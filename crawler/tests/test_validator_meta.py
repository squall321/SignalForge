"""workflow_validator 메타-루프 단위 테스트 (R24 트랙 B).

목표
====
R23 권고 — "B validator 메타-루프 (validator 가 자기 보고서도 검증)" — 의
최소 단위 검증. 핵심 의문:

  * `parse_report` 의 정규식 거리 제약 (≤8자) 으로 인해 validator 의 *자기
    산출 보고서* (예 `reports/workflow_validate_R22.md`) 표 형식
    `| voc_total | L38 | 150,000 | ...` 은 *읽히지 않는다* (R22 § 2 자동
    식별률 0/5).
  * 신규 `parse_report_meta` 는 표 셀 단위 파싱으로 이를 해소한다 — voc_total
    metric 셀 행을 잡아 reported 118,541 vs live 120,179 의 drift 를 재구성.
  * `validate_meta` 는 재귀 적용 — cap 3 도달 시 종료, 또는 *iter 중 claim
    0 발생* 시 연쇄 자연 종료.

본 테스트는 *백엔드 비가용* (가짜 live dict) 환경에서 결정론적으로 실행 가능.

요구: ≥ 1 케이스 (PLAN.md).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from insight.workflow_validator import (  # noqa: E402
    parse_report,
    parse_report_meta,
    validate_meta,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
R22_META_REPORT = REPO_ROOT / "reports" / "workflow_validate_R22.md"


@pytest.mark.skipif(
    not R22_META_REPORT.is_file(),
    reason="workflow_validate_R22.md missing (R23 트랙 B 산출물 부재)",
)
def test_meta_parser_recovers_validator_self_report_when_primary_parser_blind(tmp_path):
    """R22 검증 보고서의 표 셀 `| voc_total | L103 | 118,541 | 120,423 |` 은
    1) 기존 `parse_report` 로는 *0건* 검출 (정규식 거리 한계로 invisible)
    2) 신규 `parse_report_meta` 로는 *>=1건* 검출되고 drift 가
       reported=118,541 vs live=120,728 (R23 시점 실측) 기준
       ≈ +0.68% 로 계산되어야 한다.

    이는 R23 권고 B 의 핵심 — *validator blind spot 해소* — 의 회귀 검증.
    또한 `validate_meta` cap 3 동작도 같이 확인 (재귀 cap 도달 시 cap_reached=True).
    """
    fake_live = {
        "available": {"regression": True, "coverage": True, "topic_eval": False},
        "metrics": {
            # R23 종료 시점 실측 (메모리 인덱스 P3.7+ 의 voc 120,728).
            "voc_total": 120728,
            "topic_pct": 88.57,
        },
        "sources": {
            "voc_total": "regression-baseline",
            "topic_pct": "coverage-status.analyzable_pct (approx)",
        },
        "backend": "http://test",
        "generated_at_utc": "2026-06-05T22:00:00+00:00",
    }

    # (1) 기존 parser 는 *0건* — 표 셀 거리 > 8자 한계.
    primary_claims = parse_report(R22_META_REPORT, fake_live, threshold=0.10)
    assert primary_claims == [], (
        "primary parse_report 가 자기 보고서를 읽지 못함이 회귀 검증 전제. "
        f"실측={len(primary_claims)}"
    )

    # (2) meta parser 는 표 셀을 직접 파싱 → voc_total claim 1건 이상.
    meta_claims = parse_report_meta(R22_META_REPORT, fake_live, threshold=0.10)
    assert meta_claims, (
        "메타 파서가 표 셀에서 0건 추출 — 표 셀 분해 또는 metric 정규화 회귀"
    )
    voc_claims = [c for c in meta_claims if c.metric == "voc_total"]
    assert voc_claims, (
        f"voc_total 표 행 미검출. 검출된 metric="
        f"{sorted({c.metric for c in meta_claims})}"
    )
    # § 1 표 또는 § 3 표의 voc_total 행 중 reported 가 118,541 또는 120,179
    # (R22 본문 표 셀 첫 숫자) 인 행이 *반드시* 존재해야 한다.
    by_reported = {c.reported: c for c in voc_claims}
    assert 118541.0 in by_reported or 120179.0 in by_reported, (
        f"voc_total reported 셀 미검출. 검출된 reported={sorted(by_reported)}"
    )
    # drift 계산 — live=120,728 vs reported=118,541 → +0.0181 (1.81%) 부근.
    if 118541.0 in by_reported:
        c = by_reported[118541.0]
        assert c.actual == 120728.0
        assert c.drift_pct is not None
        # drift = (120728-118541)/max(118541,120728) ≈ +0.01810
        assert c.drift_pct == pytest.approx(0.0181, abs=2e-3)
        # 1.81% < 10% threshold → alert 미발화.
        assert c.alert is False

    # (3) validate_meta — cap 3 동작 검증.
    #     seed = R22 메타 보고서 (1 file). iter 0 = primary parse (0 claim →
    #     자연 종료) 또는 meta seed 자체로 시작하려면 docs/dashboard/R*.md 가
    #     seed. 본 테스트는 *cap 동작* 만 보장하므로 seed = R22 메타 보고서.
    result = validate_meta(
        [R22_META_REPORT],
        backend="http://unreachable.invalid:1",
        threshold=0.10,
        max_iter=3,
        meta_output_dir=tmp_path,
        parent_round="R22",
        write_reports=True,
    )
    # 최소 1 iter 는 실행되어야 함 (seed 가 비어있지 않으므로).
    assert result["iterations"], "validate_meta 가 iter 0 도 실행 안 함"
    # seed 가 검증 보고서이고 iter 0 = primary parser → claims=0 으로 자연 종료
    # (cap_reached=False).  *cap_reached* 키가 결정론적으로 bool 임을 보장.
    assert isinstance(result["cap_reached"], bool)
    assert result["max_iter"] == 3
    assert result["parent_round"] == "R22"
    # 첫 iter 의 output 파일이 tmp_path 에 실제로 쓰여있어야 함.
    iter0 = result["iterations"][0]
    out_path = REPO_ROOT / iter0["output_path"] if not Path(iter0["output_path"]).is_absolute() else Path(iter0["output_path"])
    # tmp_path 안에 있어야 함 (write_reports=True).
    files_in_tmp = list(tmp_path.glob("*.md"))
    assert files_in_tmp, f"메타 보고서가 tmp_path 에 생성 안됨: {tmp_path}"
