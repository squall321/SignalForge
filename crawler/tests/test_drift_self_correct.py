"""workflow_drift_stats self-report 재귀 편향 보정 (R24 트랙 C).

요구: ≥ 1 케이스.

검증 범위:
- self-report 키워드 (한국어 + 영문) 감지가 라인 ±5 컨텍스트로 동작
- ``exclude_self=True`` (기본) 시 self 표본이 라운드/트랙 집계에서 제외
- self_drift 섹션은 별도로 보고 (분리)
- ``exclude_self=False`` (호환) 시 R23 이전과 동일 결과
- self 표본 1건 제거 후 *다른* 트랙/표본의 trust_score 가 보존
- 보정 전/후 차이 정량화 (재귀 편향 -25% 이상 감소)
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
    _detect_self_report,
)


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def _make_r23_like_report(path: Path) -> Path:
    """R23 형식 합성 보고서 — self LoC 1건 + 다른 정상 표본 2건.

    실제 R23 보고서 (139 라인 self-report 절) 의 구조를 모방:
    - 상단 트랙 표 (B 200 vs 220, C drift_stats 612 LoC, 등)
    - 본문 narrative — 정상 drift 표본 (B 트랙 보고 vs 실측)
    - 충분히 떨어진 self-report drift 절 (±5 윈도우 밖)
    """
    filler = "\n".join([
        "## 본문 narrative",
        "",
        "- 트랙 B 산출: validator 모듈 보고 200 vs 실측 202 LoC (+1% drift, 미세).",
        "",
        "## 2. 트랙별 상세",
        "",
    ] + ["일반 라인 — 컨텍스트 윈도우 격리용 패딩."] * 20)
    body = (
        "# R23 SELF-CORRECT TEST\n"
        "\n"
        "| 트랙 | 산출 | 검증 |\n"
        "|------|------|------|\n"
        "| **A. LLM apply** | x | y |\n"
        "| **B. validator** | 보고 300 vs 실측 301 LoC | ok |\n"
        "| **D. voc delta** | 보고 304 vs 실측 305 LoC | ok |\n"
        "\n"
        + filler
        + "\n\n"
        + "\n".join(["일반 라인 — 더 멀리 격리용."] * 30)
        + "\n\n"
        "## 10. R18 폭락 재발 + self-report drift 명시\n"
        "\n"
        "- **R18 폭락 (87.30% → 25.50% topic_pct) 재발 여부: 없음**\n"
        "- **self-report drift (이 보고서 자체)**:\n"
        "  - 트랙 C LoC `보고 484 vs 실측 612` = **+26.45% under_report**\n"
        "\n"
    )
    return _write(path, body)


def test_self_report_keyword_detection(tmp_path):
    """한국어 + 영문 키워드 감지 — ±5 라인 윈도우."""
    lines = [
        "## self-report drift 섹션",      # idx 0 — 영문 키워드
        "",                                # 1
        "내용 라인 — drift_pct = +20%",     # 2
        "",                                # 3
        "다른 내용",                       # 4
        "",                                # 5
        "## 일반 본문",                    # 6
        "보고 358 vs 실측 509 LoC (+42%)",  # 7  — self 키워드 6칸 떨어짐
        "",                                # 8
        "## 또 다른 본문",                 # 9
        "보고 358 vs 실측 509 LoC (+42%)",  # 10 — self 키워드 10칸 떨어짐
    ]
    # 라인 2 (드리프트 본문) 는 라인 0 (self-report) 의 ±5 안 → self detected
    is_self, marker = _detect_self_report(lines, 2)
    assert is_self
    assert "self" in marker.lower() or "report" in marker.lower()

    # 라인 7 은 라인 0 에서 7칸 떨어짐 → 미감지 (±5 윈도우 밖)
    is_self, _ = _detect_self_report(lines, 7)
    assert not is_self

    # 라인 10 도 미감지
    is_self, _ = _detect_self_report(lines, 10)
    assert not is_self


def test_self_report_kw_korean(tmp_path):
    """한국어 키워드 감지 — '자기 보고', '자체 보고' 등."""
    lines = [
        "## 자기 보고 drift 절",
        "보고 484 vs 실측 612 LoC (+26%)",
    ]
    is_self, marker = _detect_self_report(lines, 1)
    assert is_self
    assert "자기" in marker or "자체" in marker


def test_module_name_self_reference(tmp_path):
    """모듈명 자기 언급 — 'workflow_drift_stats' / 'drift_stats.py' 감지."""
    lines = [
        "본 라운드 산출: workflow_drift_stats.py 612 LoC",
        "보고 484 vs 실측 612 LoC",
    ]
    is_self, marker = _detect_self_report(lines, 1)
    assert is_self
    assert "drift_stats" in marker.lower() or "workflow_drift_stats" in marker.lower()


def test_self_excluded_from_aggregate(tmp_path):
    """기본 (exclude_self=True): self 표본은 라운드/트랙 통계에서 제외."""
    rp = _make_r23_like_report(tmp_path / "R23_SELFTEST_2026-06-05.md")

    samples = parse_report_samples(rp)
    self_count = sum(1 for s in samples if s.is_self_report)
    non_self_count = sum(1 for s in samples if not s.is_self_report)
    # 합성 보고서: self LoC 1 (484 vs 612) 가 self-report 절에 있어야 함
    assert self_count >= 1, (
        f"self 표본 미감지: "
        f"{[(s.source_line, s.is_self_report, s.self_marker, s.reported, s.actual) for s in samples]}"
    )
    assert non_self_count >= 1

    result_excluded = compute([rp], exclude_self=True)
    result_included = compute([rp], exclude_self=False)

    # active 표본은 self 제외 ↔ 포함 차이 = self_count
    assert result_excluded["overall"]["total_samples"] == non_self_count
    assert result_included["overall"]["total_samples"] == len(samples)

    # self_drift 별도 섹션
    assert result_excluded["self_drift"]["n"] == self_count
    assert result_excluded["overall"]["self_samples_excluded"] == self_count
    # include 모드는 self_samples 분리 없음
    assert result_included["overall"]["self_samples_excluded"] == 0


def test_trust_score_self_correction(tmp_path):
    """보정 효과: self LoC drift +26% 제거 시 라운드 trust_score 상승."""
    rp = _make_r23_like_report(tmp_path / "R23_TRUST_2026-06-05.md")

    excluded = compute([rp], exclude_self=True)
    included = compute([rp], exclude_self=False)

    # 두 결과 모두 R23 단일 라운드여야 함
    rounds_excluded = {r["round"]: r for r in excluded["rounds"]}
    rounds_included = {r["round"]: r for r in included["rounds"]}
    assert "R23" in rounds_excluded
    assert "R23" in rounds_included

    re23 = rounds_excluded["R23"]
    ri23 = rounds_included["R23"]

    # self 제외 시 mean_abs 가 더 낮아야 (recursive bias 제거)
    assert re23["mean_abs_pct"] < ri23["mean_abs_pct"], (
        f"보정 후 mean_abs 더 낮아야: "
        f"excluded={re23['mean_abs_pct']} vs included={ri23['mean_abs_pct']}"
    )
    # 따라서 trust_score 가 더 높아야 함
    assert re23["trust_score"] >= ri23["trust_score"], (
        f"보정 후 trust 더 높아야: "
        f"excluded={re23['trust_score']} vs included={ri23['trust_score']}"
    )

    # 정량: 보정으로 mean_abs 감소 폭이 +5%p 이상 (self LoC drift 가 +26% 였으니)
    delta_pp = ri23["mean_abs_pct"] - re23["mean_abs_pct"]
    assert delta_pp >= 5.0, (
        f"보정 효과 부족: ΔmeanAbs={delta_pp:.2f}pp "
        f"(self drift 가 통계에 충분히 영향 못 줌 — fixture 재검토 필요)"
    )


def test_non_self_samples_unchanged_after_correction(tmp_path):
    """보정은 self 표본만 제거 — 다른 표본 (B 트랙 등) 값은 그대로."""
    rp = _make_r23_like_report(tmp_path / "R23_PRESERVE_2026-06-05.md")

    excluded = compute([rp], exclude_self=True)
    included = compute([rp], exclude_self=False)

    # B 트랙 (보고 200 vs 실측 220) drift 는 양쪽 모두 동일해야 함
    by_track_excl = {
        (t["round"], t["track"]): t for t in excluded["tracks"]
    }
    by_track_incl = {
        (t["round"], t["track"]): t for t in included["tracks"]
    }
    if ("R23", "B") in by_track_excl and ("R23", "B") in by_track_incl:
        # B 트랙 표본은 self 가 아니므로 동일
        be = by_track_excl[("R23", "B")]
        bi = by_track_incl[("R23", "B")]
        assert be["n"] == bi["n"]
        assert be["mean_abs_pct"] == pytest.approx(bi["mean_abs_pct"], abs=1e-6)
        assert be["trust_score"] == pytest.approx(bi["trust_score"], abs=1e-6)
