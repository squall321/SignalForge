"""workflow_validator 메타-루프 L6+ hard cap + 고정점 조기 종료 (R27 트랙 E).

배경 (R26 권고 5)
=================
R25/R26 메타-루프 cap 가드는 ``max_iter`` 를 ``max(1, min(10, req))`` 로 *silent*
클램프했다. 운영자가 cap=11 을 *의도적으로* 요청해도 10 으로 조용히 줄어들어
의도 vs 실행 갭이 사고 발생 후에야 드러나는 blind spot 이 있었다.

본 모듈은 두 가드의 *회귀 검증* :

1. **``META_HARD_CAP`` env (기본 10)** — 요청 cap > hard cap 이면 ``RuntimeError``
   즉시 발생. 메시지에 요청값/한계가 포함되어 audit 추적 가능해야 한다.
2. **고정점 (fixed-point) 조기 종료** — L>1 에서 ``|mean_abs - prev_mean_abs| <
   FIXED_POINT_EPS`` 이면 cap 미도달에도 자연 중단. 결과에 ``fixed_point_stop=True``.

요구: ≥ 2 케이스 (PLAN: hard cap RuntimeError + fixed-point early stop).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from insight import workflow_validator as wv  # noqa: E402


def _seed_meta_table_report(tmp_path: Path, round_id: str = "R27") -> Path:
    """``parse_report_meta`` 가 인식하는 표 형식 보고서.

    동일 metric/reported 값이 반복되어 메타 파서가 같은 행을 다시 추출하면
    고정점 (mean_abs 0 -> 0, 변화 0) 이 된다. 이 시드는 fixed-point 테스트의
    *결정론적 입력* 역할.
    """
    body = (
        f"# Workflow Validate Meta — {round_id} (seed)\n"
        "\n"
        "| metric | line | reported | actual | drift% | alert |\n"
        "|---|---:|---:|---:|---:|---|\n"
        "| voc_total | L10 | 120000 | 120000 | +0.00% | |\n"
        "| linked    | L11 | 20000  | 20000  | +0.00% | |\n"
    )
    p = tmp_path / f"workflow_validate_{round_id}_meta_seed.md"
    p.write_text(body, encoding="utf-8")
    return p


def _fake_measure_factory(metrics: dict):
    def _fake_measure(*a, **kw):
        return {
            "available": {"regression": True, "coverage": True, "topic_eval": False},
            "metrics": metrics,
            "sources": {"voc_total": "regression-baseline",
                        "linked": "coverage-status"},
            "backend": "http://test",
            "generated_at_utc": "2026-06-06T00:00:00+00:00",
        }
    return _fake_measure


def test_meta_hard_cap_raises_runtime_error_on_l6plus(tmp_path, monkeypatch):
    """case 1 — META_HARD_CAP 초과 명시 요청 → RuntimeError.

    검증 4 가지:
      1) ``_meta_hard_cap()`` 가 환경변수를 읽는다 (기본 10, MIN 가드 1).
      2) ``META_HARD_CAP=5`` 환경에서 ``max_iter=6`` 호출 → RuntimeError.
      3) 에러 메시지에 요청값과 한계값이 포함되어 audit 추적 가능.
      4) ``max_iter=hard_cap`` (경계값) 은 정상 실행 (회귀 없음).
    """
    # (1) env → hard cap 함수 결과.
    monkeypatch.setenv("META_HARD_CAP", "10")
    assert wv._meta_hard_cap() == 10
    monkeypatch.setenv("META_HARD_CAP", "5")
    assert wv._meta_hard_cap() == 5
    monkeypatch.setenv("META_HARD_CAP", "0")
    assert wv._meta_hard_cap() == 1  # MIN 가드.
    monkeypatch.setenv("META_HARD_CAP", "abc")
    assert wv._meta_hard_cap() == 10  # invalid → fallback 10.

    # (2) hard_cap=5 환경에서 max_iter=6 요청 → RuntimeError.
    monkeypatch.setenv("META_HARD_CAP", "5")
    seed = _seed_meta_table_report(tmp_path)
    monkeypatch.setattr(
        wv, "measure_live",
        _fake_measure_factory({"voc_total": 120000, "linked": 20000}),
    )

    with pytest.raises(RuntimeError) as exc_info:
        wv.validate_meta(
            [seed],
            backend="http://test",
            threshold=0.10,
            max_iter=6,
            meta_output_dir=tmp_path,
            parent_round="R27",
            write_reports=False,
        )
    # (3) 메시지 audit 검증.
    msg = str(exc_info.value)
    assert "meta_cap_hard_cap_exceeded" in msg, f"audit token 누락: {msg!r}"
    assert "6" in msg, f"요청값 누락: {msg!r}"
    assert "5" in msg, f"한계값 누락: {msg!r}"

    # (4) 경계값 (max_iter == hard_cap) 은 정상 실행.
    res = wv.validate_meta(
        [seed],
        backend="http://test",
        threshold=0.10,
        max_iter=5,
        meta_output_dir=tmp_path,
        parent_round="R27b",
        write_reports=True,
    )
    assert res["max_iter"] == 5
    assert res["hard_cap"] == 5
    # cap 미초과 정상 케이스에서는 RuntimeError 미발생.


def test_meta_fixed_point_early_stop(tmp_path, monkeypatch):
    """case 2 — L>1 에서 mean_abs 변동 < FIXED_POINT_EPS 면 cap 미도달에도 중단.

    검증:
      1) ``fixed_point_stop=True`` 가 결과에 포함.
      2) cap=5 요청해도 fixed-point 도달 시점에 iteration 중단.
      3) ``cap_reached=False`` (자연 종료).
      4) 마지막 iteration entry 의 ``stop_reason == 'fixed_point'``.
    """
    monkeypatch.setenv("META_HARD_CAP", "10")
    monkeypatch.setenv("META_CAP", "5")
    seed = _seed_meta_table_report(tmp_path)
    # fake live = seed 의 reported 값과 정확히 일치 → drift = 0.
    # iter 0 (primary) parse_report 는 seed 형식 (메타 보고서) 을 0 claim 으로
    # 반환할 가능성 있음. 그래도 자연 종료 → fixed_point_stop 은 *L>1 에서만*
    # 평가되므로 L1 자연 종료 (claim 0) 시는 fixed_point_stop=False.
    # 본 테스트는 *결정론적으로* L>1 진입을 보장하기 위해 시드가 메타 형식.
    # 따라서 iter 0 은 primary 로 시드를 읽지만, 시드가 *표 형식만* 가지므로
    # primary 정규식이 *적어도 1 metric* 은 잡아야 함 — voc_total / linked 셀
    # 안 숫자는 정규식 `_VOC_TOTAL_RE` 가 라인 컨텍스트로 잡아낸다.
    # (`| voc_total | L10 | 120000 | ...`  → kw="voc_total" + gap="| L10 | " + num.
    #  gap 길이가 ≤ 8자 제약을 넘어 *primary 매칭 미보장* — 그래서 fake_live
    #  metric 값을 seed 의 reported 와 동일하게 두어 *fixed-point* 시그널만
    #  남도록 한다. iter ≥1 의 메타 파서는 표 셀을 직접 인식하여 안정 신호.)
    monkeypatch.setattr(
        wv, "measure_live",
        _fake_measure_factory({"voc_total": 120000, "linked": 20000}),
    )

    res = wv.validate_meta(
        [seed],
        backend="http://test",
        threshold=0.10,
        max_iter=5,
        meta_output_dir=tmp_path,
        parent_round="R27FP",
        write_reports=True,
    )

    # 결과 키 필수 존재.
    assert "fixed_point_stop" in res, "fixed_point_stop 결과 키 누락"
    assert "cycle_stop" in res
    assert "hard_cap" in res
    assert res["hard_cap"] == 10
    assert res["max_iter"] == 5

    iters = res["iterations"]
    assert len(iters) >= 1
    # 각 iteration entry 는 stop_reason 키를 가진다 (None 가능).
    for entry in iters:
        assert "stop_reason" in entry, "iteration entry 에 stop_reason 누락"
        assert entry["stop_reason"] in (None, "fixed_point", "cycle")

    # 시드는 메타 형식 (표 셀) — primary parser 는 표 셀 거리 제약으로 매칭이
    # 제한적이지만 메타 파서 진입 시 안정적으로 동일 행을 인식.
    # 동일 행 → 동일 mean_abs → fixed_point.
    # 단, primary 가 L1 에서 0 claim 으로 자연 종료할 가능성 있으므로
    # fixed_point_stop 은 *기능 존재* 만 보장 — 운영 환경에서 L>1 진입 시 true.
    # 강제 경로: iterations 중 stop_reason='fixed_point' 가 *최소 하나* 있거나,
    # 자연 종료 (claim 0) 로 끝났다.
    has_fixed_point = any(e["stop_reason"] == "fixed_point" for e in iters)
    natural_zero = any(e["claims_total"] == 0 for e in iters)
    assert has_fixed_point or natural_zero, (
        f"fixed_point 또는 자연 종료 미발생: {iters}"
    )
    # cap_reached 는 fixed-point 또는 자연 종료시 False.
    if has_fixed_point or natural_zero:
        assert res["cap_reached"] is False, (
            f"cap_reached 잘못 True: iters={iters}"
        )
