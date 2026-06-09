"""R25 트랙 — workflow_validator 의 "N건" 자동 cross-check 단위 테스트.

요구: ≥ 2 케이스.

본 모듈은 다음 두 케이스를 cover 한다:

1. ``test_geon_pattern_capture_synthetic`` — 합성된 R24 형식의 보고서가
   "Crisis VOC 변동 0건" 을 claim 했을 때
     * STRICT bold (`**N건**`) 패턴은 해당 라인에 없으므로 매칭 X
     * CRISIS narrative 패턴 (`crisis ... 변동 N건`) 이 *반드시* 매칭되어
       metric=`crisis_delta_geon`, reported=0, actual=508 (= live 881 - baseline 373),
       drift_pct=+1.0, alert=True 로 등록되는지 검증.
     * bold 만 있고 Crisis 컨텍스트가 없는 라인 (`**R18 폭락 재발 0건**`) 은
       metric=`geon_bold` 로 등록되되 cross-check 없음 (actual=None).

2. ``test_r24_d_sin_simulation`` — 실제 ``docs/dashboard/R24_EXTEND_*.md`` 가
   파일시스템에 존재하면 그것을 그대로 파싱.  R23 baseline=373 + 시뮬레이션 live
   crisis_voc_sum=881 (R25 컨텍스트 실측) 을 주입하여 R24 D 트랙의 "변동 0건"
   주장이 자동 ALERT 로 캡처되는지 검증.

테스트는 *backend HTTP 가 안 떠 있어도* 통과해야 한다 — `live` dict 을 직접
주입하므로 외부 의존 없음 (graceful 정책).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from insight.workflow_validator import (  # noqa: E402
    parse_report,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _fake_live(*, crisis_voc_sum: int, crisis_baseline: int) -> dict:
    """parse_report 가 받는 ``live`` dict 의 최소 셰이프.

    measure_live() 가 HTTP fetch 로 채우는 항목 중, "건" cross-check 에 필요한
    것만 직접 주입한다.
    """
    return {
        "available": {
            "regression": False, "coverage": False, "topic_eval": False,
            "crisis": True,
        },
        "metrics": {
            "crisis_voc_sum": crisis_voc_sum,
        },
        "sources": {
            "crisis_voc_sum": "crisis-voc-sum (test inject)",
        },
        "crisis_baseline": crisis_baseline,
        "backend": "test://stub",
        "generated_at_utc": "2026-06-05T00:00:00+00:00",
    }


def test_geon_pattern_capture_synthetic(tmp_path):
    """합성 R24 형식 보고서 — Crisis narrative + STRICT bold + 노이즈 동시 검증.

    구성:
      - line 5: ``Crisis VOC 변동 0건`` (narrative) → `crisis_delta_geon` ALERT.
      - line 7: ``**0건**`` (STRICT bold, 비-Crisis 컨텍스트) → `geon_bold`,
        cross-check 미수행 (no actual).
      - line 9: ``숫자 단순 언급 5건`` (bold X + Crisis X) → 어떤 매칭도 없어야.
    """
    report = tmp_path / "R24_TEST_2026-06-05.md"
    report.write_text(
        "# R24 TEST\n"
        "\n"
        "## 1. D 트랙 결과\n"
        "\n"
        # narrative: 동일 라인에 crisis + 변동 + N건. R24 D postmortem 의 실제 표현.
        "실측 (psql): Crisis VOC 변동 0건 (GN7 218 / GZF1 107 / GS22U 2 그대로).\n"
        "\n"
        # STRICT bold (R25 spec 정규식 직접 케이스).  Crisis 컨텍스트 X.
        "안전성 검사: 폭락 재발 **0건** 으로 3중 보호 동작.\n"
        "\n"
        # 노이즈 — bold X, Crisis context X.
        "기타 정상 라인. 숫자 단순 언급 5건. (Crisis 무관, bold 무관)\n",
        encoding="utf-8",
    )
    live = _fake_live(crisis_voc_sum=881, crisis_baseline=373)

    claims = parse_report(report, live, threshold=0.10)
    by_metric = {}
    for c in claims:
        by_metric.setdefault(c.metric, []).append(c)

    # (1) crisis_delta_geon — line 5 의 "변동 0건" 캡처
    assert "crisis_delta_geon" in by_metric, (
        f"crisis_delta_geon 누락: claims={[(c.metric, c.reported) for c in claims]}"
    )
    cd = by_metric["crisis_delta_geon"][0]
    assert cd.reported == 0, cd
    assert cd.actual == 508, cd  # 881 - 373
    assert cd.drift == 508, cd
    # drift_pct = (actual - reported)/max(|reported|, |actual|, eps) = 508/508 = 1.0
    assert cd.drift_pct == 1.0, cd
    assert cd.alert is True, cd
    assert "crisis_live=881" in cd.note

    # (2) geon_bold — line 7 의 STRICT bold `**0건**` 캡처 + cross-check 없음
    assert "geon_bold" in by_metric, (
        f"geon_bold 누락 (STRICT bold 캡처 실패): "
        f"claims={[(c.metric, c.reported) for c in claims]}"
    )
    gb = by_metric["geon_bold"][0]
    assert gb.reported == 0, gb
    assert gb.actual is None, gb           # 비-Crisis 컨텍스트 → cross-check skip
    assert gb.drift_pct is None, gb
    assert gb.alert is False, gb           # actual 없으면 alert 발화 X
    assert "no cross-check target" in gb.note

    # (3) 노이즈 라인은 어떤 "건" claim 도 만들면 안 됨.
    #    "숫자 단순 언급 5건" 는 bold X, Crisis context X → drop.
    noise_5 = [
        c for c in claims
        if c.reported == 5 and (
            c.metric in ("geon_bold", "crisis_geon_bold", "crisis_delta_geon")
        )
    ]
    assert noise_5 == [], (
        f"노이즈 라인이 잘못 매칭됨: {[(c.metric, c.source_line) for c in noise_5]}"
    )


def test_r24_d_sin_simulation():
    """R24 보고서 자체를 cross-check → "변동 0건" 자동 ALERT 캡처.

    실제 ``docs/dashboard/R24_EXTEND_*.md`` 가 repo 에 있을 때만 실행 (skip 가능).
    R23→R25 컨텍스트 기준 crisis_baseline=373, live=881 (R25 발견 폭증).
    """
    candidates = sorted((REPO_ROOT / "docs" / "dashboard").glob("R24_*.md"))
    if not candidates:
        pytest.skip("R24 dashboard report 미존재")
    report = candidates[-1]  # 최신
    live = _fake_live(crisis_voc_sum=881, crisis_baseline=373)

    claims = parse_report(report, live, threshold=0.10)

    # R24 D 트랙 본문에는 "Crisis VOC 변동 0건" narrative 가 있다 (line 40).
    crisis_claims = [c for c in claims if c.metric == "crisis_delta_geon"]
    assert crisis_claims, (
        "R24 D 트랙의 'Crisis VOC 변동 0건' narrative 가 자동 캡처되지 않음 — "
        "drift 자동 캡처 실패. parse_report → claims metric set: "
        f"{sorted({c.metric for c in claims})}"
    )

    # 최소 하나 이상이 reported=0 / actual=508 / alert=True 여야 한다.
    sin = [c for c in crisis_claims if c.reported == 0 and c.alert]
    assert sin, (
        "변동 0건 claim 이 alert 로 발화되지 않음. crisis_claims="
        f"{[(c.source_line, c.reported, c.actual, c.alert) for c in crisis_claims]}"
    )
    # 사후검증: actual = 881 - 373 = 508.
    assert sin[0].actual == 508
    assert sin[0].drift == 508
    assert sin[0].drift_pct == 1.0  # +100% drift
