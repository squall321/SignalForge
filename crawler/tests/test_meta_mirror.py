"""workflow_validator 메타 파서의 `_BOLD_GEON_RE` / `_DELTA_GEON_RE` 미러링
단위 테스트 (R26 트랙 B).

배경
====
R25 트랙 B 실험 (`reports/r25_meta_cap_experiment/summary.json`) 에서
cap 5 까지 5단 누적 검증 시 L1 = 38 claim, L2~L5 = 37 claim 으로 1 행씩
탈락하는 현상이 관측되었다.

원인은 primary parser (`parse_report`) 가 ``**N건**`` / ``변동 N건`` 라인을
``geon_bold`` / ``crisis_geon_bold`` / ``crisis_delta_geon`` 메트릭으로
등록하지만, 메타 파서 (`parse_report_meta`) 의 ``_META_KNOWN_METRICS`` 풀에
이 이름들이 누락되어 *L2 부터 행 자체가 사라지는* blind spot 이다.
추가로 primary 가 ``actual=None`` 인 행을 ``missing`` 셀로 직렬화하는데,
메타 파서가 이를 숫자 셀로만 해석해 sentinel 처리를 못 하면 두 번째 셀이
탈락하여 cross-check 가 무산된다.

요구 (R26 트랙 B)
================
1. ``crisis_delta_geon`` / ``crisis_geon_bold`` / ``geon_bold`` 행이
   메타 파서에서도 ``MetricClaim`` 으로 추출된다.
2. ``crisis_delta_geon`` 의 두 번째 셀이 ``missing`` 이라도 행이 보존된다.
3. ``crisis_voc_sum`` & ``crisis_baseline`` 이 모두 주어지면 메타 파서는
   primary 와 동일하게 ``actual = crisis_live - crisis_baseline`` 로
   cross-check 한다.
4. 두 단계 (parse_report → parse_report_meta) 동일 보고서를 입력으로
   넣어도 *geon 행 수가 보존* 된다 (L1 == L2).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from insight import workflow_validator as wv  # noqa: E402


def _write_primary_like_report(tmp_path: Path) -> Path:
    """primary parser 가 ``crisis_delta_geon`` 1행 + ``voc_total`` 1행을
    만들어내는 *재료* 보고서.

    실제 R24 보고서에서 ``L40`` 의 "Crisis VOC 변동 0건" 라인이 만든 형식과
    동일한 표 행을 직접 시뮬레이션한다 — 메타 파서가 이 행을 읽어 같은
    metric 으로 보존하는지 확인.
    """
    body = (
        "# Workflow Validate Meta — R26 (mirror)\n"
        "\n"
        "| metric | line | 보고 (reported) | 실측 (live) | drift% | alert | note |\n"
        "|---|---:|---:|---:|---:|---|---|\n"
        "| voc_total | L10 | 120000 | 120728 | +0.61% |  | ok |\n"
        # primary 가 crisis_delta_geon (reported=0, actual=missing) 직렬화 형식.
        "| crisis_delta_geon | L40 | 0 | missing | — |  | context=crisis_delta |\n"
        # bold-only geon (cross-check 미수행) — 행 보존만.
        "| geon_bold | L52 | 881 | missing | — |  | context=bold_only |\n"
    )
    p = tmp_path / "workflow_validate_R26_meta_iter0.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_meta_parser_mirrors_bold_and_delta_geon(tmp_path):
    """메타 파서가 geon-family 3종 metric 을 모두 보존한다.

    검증:
      a) ``crisis_delta_geon`` 행 1건 보존, ``reported==0``.
      b) ``geon_bold`` 행 1건 보존, ``actual is None`` (cross-check 미수행).
      c) ``crisis_voc_sum`` + ``crisis_baseline`` 주어지면
         ``crisis_delta_geon.actual = live - baseline`` 로 cross-check.
      d) ``MetricClaim`` 의 metric 이름이 정확히 ``crisis_delta_geon`` /
         ``geon_bold`` (primary 와 동일 canonical key).
    """
    seed = _write_primary_like_report(tmp_path)
    live = {
        "available": {"regression": True, "coverage": True, "topic_eval": False},
        "metrics": {
            "voc_total": 120728,
            # primary 의 _push_geon 동일 정책: actual_delta = live - baseline.
            # 여기서는 1000 - 900 = +100 으로 cross-check 결과 검증.
            "crisis_voc_sum": 1000,
        },
        "crisis_baseline": 900,
        "sources": {
            "voc_total": "regression-baseline",
            "crisis_voc_sum": "exact",
        },
        "backend": "http://test",
        "generated_at_utc": "2026-06-05T22:00:00+00:00",
    }
    claims = wv.parse_report_meta(seed, live, threshold=0.10)
    metric_names = [c.metric for c in claims]

    # (a) crisis_delta_geon 보존.
    crisis_claims = [c for c in claims if c.metric == "crisis_delta_geon"]
    assert len(crisis_claims) == 1, (
        f"crisis_delta_geon 행 탈락: metrics={metric_names}"
    )
    cc = crisis_claims[0]
    assert cc.reported == 0.0, f"reported 손상: {cc.reported}"

    # (c) crisis cross-check actual = 1000 - 900 = 100.
    assert cc.actual == 100.0, (
        f"crisis_delta_geon.actual 미적용: actual={cc.actual} "
        f"(기대: crisis_voc_sum - crisis_baseline = 100)"
    )
    # drift_pct = (100 - 0) / max(0, 100, eps) = 1.0
    assert cc.drift_pct is not None
    assert abs(cc.drift_pct - 1.0) < 1e-6, f"drift_pct 미산출: {cc.drift_pct}"
    # threshold 0.10 → alert = True (|1.0| > 0.10).
    assert cc.alert is True, "crisis cross-check drift alert 미발화"

    # (b) geon_bold 보존, cross-check 미수행.
    bold_claims = [c for c in claims if c.metric == "geon_bold"]
    assert len(bold_claims) == 1, (
        f"geon_bold 행 탈락: metrics={metric_names}"
    )
    bc = bold_claims[0]
    assert bc.reported == 881.0
    assert bc.actual is None, "geon_bold cross-check 미수행 정책 위반"
    assert bc.drift_pct is None
    assert bc.alert is False
    assert "no cross-check target" in bc.note

    # (d) voc_total 정상 유지 (regression 보호).
    assert "voc_total" in metric_names
    voc_claim = next(c for c in claims if c.metric == "voc_total")
    assert voc_claim.reported == 120000.0
    assert voc_claim.actual == 120728.0


def test_l1_l2_geon_row_count_parity(tmp_path):
    """L1 (parse_report) vs L2 (parse_report_meta) geon 행 수 동일.

    R25 트랙 B 의 핵심 회귀 보호:
      1) primary 가 ``**N건**`` 줄에서 ``geon_bold`` 1행을 만든다.
      2) 그 결과가 메타 보고서로 직렬화되어 다시 파서를 거칠 때 행이
         탈락하지 않는다.
    """
    # primary 가 인식할 본문 (`**N건**` 라인 1개, *비-Crisis 컨텍스트*).
    # _CRISIS_LINE_RE (`crisis|GN7|GZF1|GS22U|GZFL3|GS20`) 를 회피하여
    # 순수 `geon_bold` 1행만 생성되도록 한다.
    body_primary = (
        "# R26 본문\n"
        "\n"
        "전체 VOC 활동 합계는 **881건** 으로 R25 대비 안정.\n"
    )
    primary_path = tmp_path / "R26_PRIMARY_2026-06-05.md"
    primary_path.write_text(body_primary, encoding="utf-8")

    live = {
        "available": {"regression": True, "coverage": True, "topic_eval": False},
        "metrics": {},  # 의도적 빈 metrics — geon 만 비교.
        "sources": {},
        "backend": "http://test",
        "generated_at_utc": "2026-06-05T22:00:00+00:00",
    }
    primary_claims = wv.parse_report(primary_path, live, threshold=0.10)
    primary_bold = [c for c in primary_claims if c.metric == "geon_bold"]
    assert len(primary_bold) == 1, (
        f"primary 가 **881건** 을 인식 못함: claims={primary_claims}"
    )

    # primary 결과를 메타 보고서로 직렬화 → 메타 파서 재입력.
    meta_md = wv._build_meta_report(
        iteration=0,
        parent_round="R26",
        claims_by_path={"R26_PRIMARY_2026-06-05.md": primary_claims},
        live=live,
        threshold=0.10,
        max_iter=2,
    )
    meta_path = tmp_path / "workflow_validate_R26_meta_iter0.md"
    meta_path.write_text(meta_md, encoding="utf-8")

    meta_claims = wv.parse_report_meta(meta_path, live, threshold=0.10)
    meta_bold = [c for c in meta_claims if c.metric == "geon_bold"]
    assert len(meta_bold) == len(primary_bold), (
        f"L1 geon_bold={len(primary_bold)} vs L2 geon_bold={len(meta_bold)} "
        f"— R25 트랙 B 1차이 재발. meta claims={[c.metric for c in meta_claims]}"
    )
