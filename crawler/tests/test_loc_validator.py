"""loc_validator 단위 테스트 (R21 트랙 B).

요구: ≥ 1 케이스. 본 모듈은 *주요 파싱 경로 1건* 으로 R20 형식
("실측 N LoC, 보고 M LoC") + 인라인 형식 ("(N lines)") 둘 다 한 번에 검증.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.loc_validator import (  # noqa: E402
    parse_report,
    validate,
    _count_lines,
)


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_parse_report_inline_and_actual_reported_patterns(tmp_path, monkeypatch):
    """인라인 (N lines) + R20 표 ("실측 X LoC, 보고 Y LoC") 패턴 동시 검증.

    가짜 코드 파일 2개를 만들고, 보고서가 그 파일을 두 가지 패턴으로 인용한다.
    drift 계산이 정확하고 threshold 초과 시 alert=True 가 되는지 확인.
    """
    # 1) 모듈의 REPO_ROOT 를 tmp_path 로 가로채기 — 가짜 repo 구성.
    import scripts.loc_validator as lv
    monkeypatch.setattr(lv, "REPO_ROOT", tmp_path)

    # 2) 가짜 코드 파일 2개 (각각 100, 250 lines).
    f1 = tmp_path / "crawler" / "scripts" / "fake_a.py"
    f1.parent.mkdir(parents=True, exist_ok=True)
    f1.write_text("\n".join(f"# l{i}" for i in range(1, 101)) + "\n",
                  encoding="utf-8")
    assert _count_lines(f1) == 100

    f2 = tmp_path / "crawler" / "insight" / "fake_b.py"
    f2.parent.mkdir(parents=True, exist_ok=True)
    f2.write_text("\n".join(f"# l{i}" for i in range(1, 251)) + "\n",
                  encoding="utf-8")
    assert _count_lines(f2) == 250

    # 3) 보고서 작성:
    #    - fake_a 는 인라인 "(95 lines)" → drift +5 / max(95,100)=100 → +5%
    #      (threshold 20% 미만, alert=False)
    #    - fake_b 는 R20 표 형식 "실측 180 LoC, 보고 180 LoC" 인데 실제 250
    #      → drift = 250-180=+70 / 250 = +28% (alert=True)
    body = (
        "# R99 TEST\n"
        "\n"
        "| 트랙 | 산출 | 단위 | verify |\n"
        "|---|---|---|---|\n"
        "| A | `crawler/scripts/fake_a.py` (95 lines) | PASS | pass |\n"
        "| B | `crawler/insight/fake_b.py` (실측 180 LoC, 보고 180 LoC) "
        "| PASS | pass |\n"
    )
    report = _write(tmp_path, "R99_TEST_2026-06-05.md", body)

    claims = parse_report(report, threshold=0.20)
    by_file = {c.file: c for c in claims}

    # fake_a
    a = by_file["crawler/scripts/fake_a.py"]
    assert a.reported == 95
    assert a.actual == 100
    assert a.drift == 5
    assert a.drift_pct == pytest.approx(0.05, abs=1e-3)
    assert a.alert is False
    assert a.round == "R99"

    # fake_b — R20 표 형식. reported 는 "보고 180" 이고 실측은 측정값 250.
    b = by_file["crawler/insight/fake_b.py"]
    assert b.reported == 180
    assert b.actual == 250
    assert b.drift == 70
    assert b.drift_pct == pytest.approx(0.28, abs=1e-3)
    assert b.alert is True
    assert b.round == "R99"

    # 4) validate() 요약.
    result = validate([report], threshold=0.20)
    assert result["summary"]["total_claims"] == 2
    assert result["summary"]["alerts"] == 1
    assert result["summary"]["files_missing"] == 0
