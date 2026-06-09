"""workflow_validator 메타-루프 cap 확장 (3 → 5) 단위 테스트 (R25 트랙 B).

목표
====
R24 권고 — "B 메타-루프 cap 확장 (3 → 5)" — 의 회귀 검증.

핵심
----
* ``META_CAP`` 환경변수로 cap 제어 가능 (``_meta_cap_default``).
* ``validate_meta`` 에 ``max_iter=None`` 으로 호출 시 환경변수 fallback.
* ``max_iter=5`` 명시 호출 시 deep level 5 까지 cap_reached=True 도달
  *또는* claim 0 으로 자연 종료 — 둘 중 결정론적.
* 결과에 ``drift_distribution`` (level 1..N) 필드가 채워져 cap 별 drift
  분포 요약을 제공해야 한다.

요구: ≥ 1 케이스 (PLAN.md, R25 트랙 B).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from insight import workflow_validator as wv  # noqa: E402


def _seed_validator_self_report(tmp_path: Path, round_id: str = "R25") -> Path:
    """validator 의 자기 보고서 형식 (표 셀) 시드 생성.

    ``parse_report_meta`` 가 인식하는 표 형식만 포함 — 메타 파서가
    *이 시드를 reported=X / actual=Y 행으로* 분해할 수 있어야 한다.

    표 1 행: ``| voc_total | L10 | 120000 | 120728 | +0.61% | |``
    이 행이 메타 파서로 detect → reported=120000, recorded_actual=120728.
    fake_live.voc_total=120728 → drift ≈ 0 (alert 없음).
    """
    body = (
        f"# Workflow Validate Meta — {round_id} (seed)\n"
        "\n"
        "| metric | line | reported | actual | drift% | alert |\n"
        "|---|---:|---:|---:|---:|---|\n"
        "| voc_total | L10 | 120000 | 120728 | +0.61% | |\n"
        "| linked    | L11 | 20000  | 20084  | +0.42% | |\n"
    )
    p = tmp_path / f"workflow_validate_{round_id}_meta_seed.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_meta_cap_extends_from_3_to_5_via_env_and_arg(tmp_path, monkeypatch):
    """cap=5 까지 deep level 진행 + drift_distribution 채움 + 환경변수 fallback.

    검증 4가지:
      1) ``META_CAP=5`` 환경변수 → ``_meta_cap_default()`` 가 5 반환.
      2) ``validate_meta(max_iter=None)`` → 환경변수 fallback 으로 cap=5 적용.
      3) ``validate_meta(max_iter=5)`` 명시 호출 → ``max_iter==5`` 결과 키.
      4) 결과 ``drift_distribution`` 가 *비어있지 않으며* 각 entry 가
         ``level``, ``mean_abs_pct``, ``max_abs_pct``, ``samples`` 키를 가짐.
         level 은 1부터 시작.
    """
    # (1) env → default 함수 결과.
    monkeypatch.setenv("META_CAP", "5")
    # _meta_cap_default 는 호출 시점에 환경변수를 읽음.
    assert wv._meta_cap_default() == 5

    # 가드 — 0/음수/과도값.
    monkeypatch.setenv("META_CAP", "0")
    assert wv._meta_cap_default() == 1
    monkeypatch.setenv("META_CAP", "999")
    assert wv._meta_cap_default() == 10
    monkeypatch.setenv("META_CAP", "abc")
    assert wv._meta_cap_default() == 3  # invalid → fallback 3
    # 정상 5 로 복귀.
    monkeypatch.setenv("META_CAP", "5")
    assert wv._meta_cap_default() == 5

    # (2) seed → validate_meta(max_iter=5) — 메타 파서가 시드를 인식해야 함.
    seed = _seed_validator_self_report(tmp_path, round_id="R25")
    fake_live_metrics = {
        "voc_total": 120728,
        "linked": 20084,
    }

    # measure_live 가 backend 미가용 시 partial 결과만 반환하므로,
    # 결정론적 환경을 위해 monkeypatch.
    def _fake_measure(*a, **kw):
        return {
            "available": {"regression": True, "coverage": True, "topic_eval": False},
            "metrics": fake_live_metrics,
            "sources": {"voc_total": "regression-baseline",
                        "linked": "coverage-status"},
            "backend": "http://test",
            "generated_at_utc": "2026-06-05T22:00:00+00:00",
        }
    monkeypatch.setattr(wv, "measure_live", _fake_measure)

    # (2-a) max_iter=None → env fallback (=5).
    res_env = wv.validate_meta(
        [seed],
        backend="http://test",
        threshold=0.10,
        max_iter=None,
        meta_output_dir=tmp_path,
        parent_round="R25",
        write_reports=True,
    )
    assert res_env["max_iter"] == 5, (
        f"env fallback 미적용: max_iter={res_env['max_iter']}"
    )
    assert res_env["cap_used"] == 5
    assert res_env["cap_env_default"] == 5

    # (3) max_iter=5 명시.
    res = wv.validate_meta(
        [seed],
        backend="http://test",
        threshold=0.10,
        max_iter=5,
        meta_output_dir=tmp_path,
        parent_round="R25b",
        write_reports=True,
    )
    assert res["max_iter"] == 5
    assert res["cap_used"] == 5

    # (4) drift_distribution 필드 — level 1..N 단조 증가.
    dist = res["drift_distribution"]
    assert isinstance(dist, list)
    assert dist, "drift_distribution 비어있음 — iteration 0회 실행"
    # 첫 entry level=1 (= iter 0 + 1 사용자 가독).
    assert dist[0]["level"] == 1
    # 각 entry 의 키 스키마.
    for entry in dist:
        for key in ("level", "kind", "claims",
                    "mean_abs_pct", "max_abs_pct", "min_abs_pct", "samples"):
            assert key in entry, f"drift_distribution entry 키 누락: {key}"
        # 분포 통계는 음이 아니어야 함 (abs drift).
        assert entry["mean_abs_pct"] >= 0.0
        assert entry["max_abs_pct"] >= entry["mean_abs_pct"]
        assert entry["min_abs_pct"] <= entry["mean_abs_pct"]

    # cap 동작 — iter 0 (primary) 은 seed (검증 보고서) 형식 이라
    # parse_report 가 0 claim → 자연 종료 가능. 그래도 dist[0] 은 존재.
    # cap_reached 또는 자연 종료 둘 다 허용 — 단 *iter 0 이상* 실행.
    assert isinstance(res["cap_reached"], bool)
    assert len(res["iterations"]) >= 1
    # cap 가드 — R27 트랙 E 이후: max_iter > META_HARD_CAP 은 silent 클램프가
    # 아니라 RuntimeError 즉시 발생. META_HARD_CAP 기본 10 에서 999 요청 →
    # 명시적 audit alert 메시지.
    with pytest.raises(RuntimeError, match="meta_cap_hard_cap_exceeded"):
        wv.validate_meta(
            [seed],
            backend="http://test",
            threshold=0.10,
            max_iter=999,
            meta_output_dir=tmp_path,
            parent_round="R25c",
            write_reports=False,
        )
    # 경계값 (= hard_cap) 은 정상 실행 — 회귀 가드.
    res_boundary = wv.validate_meta(
        [seed],
        backend="http://test",
        threshold=0.10,
        max_iter=10,
        meta_output_dir=tmp_path,
        parent_round="R25c",
        write_reports=False,
    )
    assert res_boundary["max_iter"] == 10
