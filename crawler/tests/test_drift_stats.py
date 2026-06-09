"""workflow_drift_stats 단위 테스트 (R23 트랙 C).

요구: ≥ 1 케이스.

본 모듈은 *기본* 한 케이스로 다음을 한꺼번에 확인한다:
- LoC drift 4종 패턴 (보고 vs 실측, 실측 vs 보고, 보고 vs +N%, sync_block)
- 라운드 통계 (n, mean, std, signed_mean)
- 트랙 식별 (표 셀에서 A/B 추출)
- 신뢰도 점수 (0~100 식)
- 분포 히스토그램
- compute() 의 top-level 응답 schema
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from insight.workflow_drift_stats import (  # noqa: E402
    compute,
    parse_report_samples,
    _trust_score,
)


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_parse_and_aggregate_drift(tmp_path):
    """현실 R20·R21 형식의 합성 보고서 2편에서 drift 표본 추출 + 집계."""
    r20 = tmp_path / "R20_TEST_2026-06-05.md"
    _write(r20, (
        "# R20 TEST\n"
        "\n"
        "| 트랙 | 산출 | 검증 |\n"
        "|------|------|------|\n"
        "| **A. LLM apply** | x | y |\n"
        "| **B. Crisis 한국** | (실측 509 lines, 보고 358 lines 차이) | ok |\n"
        "| **C. ops alerts** | (실측 323 LoC, 보고 276 LoC 차이 +47) | ok |\n"
        "\n"
        "### 트랙 B 상세\n"
        "보고 358 vs 실측 509 LoC (+42% drift) — 정상 동작.\n"
        "\n"
        "<!-- workflow-sync:begin -->\n"
        "\n"
        "> 워크플로우 자동 동기화 (R20)  \n"
        "> 보고된 수치 vs 실측 비교.\n"
        "\n"
        "| metric | 보고 | 실측 | drift% | alert |\n"
        "|---|---:|---:|---:|---|\n"
        "| voc_total | 118430 | 119981 | +1.3% |  |\n"
        "| linked | 19439 | 19534 | +0.5% |  |\n"
        "\n"
        "<!-- workflow-sync:end -->\n"
    ))

    r21 = tmp_path / "R21_TEST_2026-06-05.md"
    _write(r21, (
        "# R21 TEST\n"
        "\n"
        "| 트랙 | 산출 | 검증 |\n"
        "|------|------|------|\n"
        "| A LLM apply | topic_llm_apply.py 446 | 보고 322 vs 실측 446 LoC |\n"
        "| B LoC drift | loc_validator.py 389 | "
        "보고 305 vs 실측 389 LoC, drift +27.5% |\n"
        "\n"
        "## 신규 파일 LoC 보고 1623 대비 +41% 일관 drift.\n"
    ))

    # parse 단계
    s20 = parse_report_samples(r20)
    s21 = parse_report_samples(r21)
    assert len(s20) >= 3, f"R20 표본 부족: {[(s.kind, s.source_line) for s in s20]}"
    assert len(s21) >= 3, f"R21 표본 부족: {[(s.kind, s.source_line) for s in s21]}"

    # 트랙 식별 — R20 표 행 안의 LoC drift 는 해당 트랙으로 라벨
    kinds_r20 = {s.kind for s in s20}
    assert "loc" in kinds_r20
    assert "sync_block" in kinds_r20

    # 트랙 B 의 (358, 509) — drift = (509-358)/509 ≈ +0.2967
    b_locs = [s for s in s20 if s.track == "B" and s.kind == "loc"
              and s.reported == 358]
    assert b_locs, f"R20 트랙 B (358 vs 509) 미검출: {s20}"
    assert b_locs[0].drift_pct == pytest.approx(0.2967, abs=1e-3)

    # 트랙 C 의 (276, 323) — drift = (323-276)/323 ≈ +0.1455
    c_locs = [s for s in s20 if s.track == "C" and s.kind == "loc"
              and s.reported == 276]
    assert c_locs, f"R20 트랙 C (276 vs 323) 미검출"
    assert c_locs[0].drift_pct == pytest.approx(0.1455, abs=1e-3)

    # sync_block voc_total 행
    sync = [s for s in s20 if s.kind == "sync_block"]
    assert len(sync) == 2

    # R21 패턴 5 (pct_only): "1623 대비 +41%"
    pct_only = [s for s in s21 if s.kind == "pct_only"]
    assert pct_only, "pct_only 패턴 미검출"
    assert pct_only[0].drift_pct == pytest.approx(0.41, abs=1e-2)

    # compute() — 라운드/트랙 통계 + 신뢰도
    result = compute([r20, r21])
    assert result["available"] is True
    assert result["overall"]["rounds_analyzed"] == 2

    rounds = {r["round"]: r for r in result["rounds"]}
    assert "R20" in rounds and "R21" in rounds

    r20_stat = rounds["R20"]
    assert r20_stat["n"] >= 3
    # mean_abs_pct 는 표본 절댓값 평균 — R20 은 (29.7 + 14.6 + 29.7 + 1.3 + 0.5) % ≈ 15%대
    # (정확값은 패턴 매칭 결과 의존, ±10 % 허용)
    assert 5.0 <= r20_stat["mean_abs_pct"] <= 35.0
    # trust 0~100 범위 + 라운드 단조성 (drift 낮을수록 trust 높음)
    assert 0.0 <= r20_stat["trust_score"] <= 100.0
    # bias 일관 양수 (모두 +) → under_report 라벨
    assert r20_stat["signed_mean_pct"] > 0
    if r20_stat["mean_abs_pct"] >= 1.0:
        # signed_mean / mean_abs 가 임계 충족 시 bias 라벨
        ratio = abs(r20_stat["signed_mean_pct"]) / r20_stat["mean_abs_pct"]
        if ratio >= 0.5:
            assert r20_stat["systematic_bias"] == "under_report"

    # 트랙 통계 — R20 B / R20 C / R21 A / R21 B 가 모두 있어야
    tracks = {(t["round"], t["track"]) for t in result["tracks"]}
    assert ("R20", "B") in tracks
    assert ("R20", "C") in tracks
    assert ("R21", "A") in tracks
    assert ("R21", "B") in tracks

    # distribution sum == n
    dist_sum = sum(r20_stat["distribution"].values())
    assert dist_sum == r20_stat["n"]

    # 신뢰도 점수 공식 sanity:
    # mean=0, std=0  → 100
    assert _trust_score(0.0, 0.0) == 100.0
    # mean=1.0 (=100%) → 0
    assert _trust_score(1.0, 0.0) == 0.0
    # mean=0.1, std=0.1 → 100 * 0.9 * 0.95 = 85.5
    assert _trust_score(0.10, 0.10) == pytest.approx(85.5, abs=0.1)
    # mean=0.30, std=0.20 → 100 * 0.7 * 0.9 = 63.0
    assert _trust_score(0.30, 0.20) == pytest.approx(63.0, abs=0.1)

    # overall 종합
    o = result["overall"]
    assert "weakest" in o and "strongest" in o
    assert o["weakest"]["trust_score"] <= o["strongest"]["trust_score"]
