"""workflow_validator 단위 테스트 (R22 트랙 B).

요구: ≥ 2 케이스.

1. ``test_parse_report_drift_and_alert`` — 보고서 본문 수치 vs 가짜 live 측정
   비교, drift % 와 alert 플래그가 정확히 계산되는지.
2. ``test_inject_sync_block_is_idempotent`` — 자동 동기화 블록 삽입이 *반복
   실행 시 멱등* 인지 (두 번째 호출은 파일 무변경).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from insight.workflow_validator import (  # noqa: E402
    parse_report,
    inject_sync_block,
    validate,
    measure_live,
)


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


# ── 케이스 1 ──────────────────────────────────────────────────────────────
def test_parse_report_drift_and_alert(tmp_path):
    """보고서 표 안의 voc_total / linked / sentiment_pct / f1_overall 수치 vs
    실측 비교. drift_pct 와 alert 플래그를 정확히 계산해야 한다.

    threshold=0.10 기준:
      - voc_total 보고 118,430 vs 실측 119,981 → drift=+1551 / 119981 = +0.013 → alert=False
      - linked 보고 19,439 vs 실측 19,534 → +0.005 → alert=False
      - sentiment_pct 보고 100.00 vs 실측 88.5 → -0.115 → alert=True
      - f1_overall 보고 0.500 vs 실측 0.500 → 0 → alert=False
    """
    report = tmp_path / "R20_TEST_2026-06-05.md"
    body = (
        "# R20 TEST\n"
        "\n"
        "| 지표 | 값 |\n"
        "|---|---|\n"
        "| voc_total | 118,430 |\n"
        "| linked | 19,439 |\n"
        "| sentiment % | 100.00% |\n"
        "| overall F1 | 0.500 |\n"
    )
    _write(report, body)

    live = {
        "available": {"regression": True, "coverage": True, "topic_eval": True},
        "metrics": {
            "voc_total": 119981,
            "linked": 19534,
            "sentiment_pct": 88.5,
            "topic_pct": 88.5,
            "f1_overall": 0.500,
        },
        "sources": {},
        "backend": "http://test",
        "generated_at_utc": "2026-06-05T20:38:00+00:00",
    }
    claims = parse_report(report, live, threshold=0.10)
    by_metric = {c.metric: c for c in claims}

    assert "voc_total" in by_metric
    c = by_metric["voc_total"]
    assert c.reported == 118430
    assert c.actual == 119981
    assert c.drift == 1551
    assert c.drift_pct == pytest.approx(0.0129, abs=1e-3)
    assert c.alert is False

    c = by_metric["linked"]
    assert c.reported == 19439
    assert c.actual == 19534
    assert c.alert is False

    c = by_metric["sentiment_pct"]
    assert c.reported == pytest.approx(100.0)
    assert c.actual == pytest.approx(88.5)
    # drift = 88.5 - 100 = -11.5  /  max(100, 88.5) = 100  → -0.115
    assert c.drift_pct == pytest.approx(-0.115, abs=1e-3)
    assert c.alert is True

    c = by_metric["f1_overall"]
    assert c.reported == pytest.approx(0.500)
    assert c.actual == pytest.approx(0.500)
    assert c.alert is False


# ── 케이스 2 ──────────────────────────────────────────────────────────────
def test_inject_sync_block_is_idempotent(tmp_path):
    """``inject_sync_block`` 가 첫 호출에 블록을 추가하고, 같은 claim 으로 두 번째
    호출 시 *파일 내용이 변하지 않아야 한다*.  본문 (블록 외) 도 보존.
    """
    report = tmp_path / "R20_IDEM_2026-06-05.md"
    body = (
        "# R20 IDEM TEST\n"
        "\n"
        "| 지표 | 값 |\n"
        "|---|---|\n"
        "| voc_total | 118,430 |\n"
    )
    _write(report, body)

    live = {
        "available": {"regression": True, "coverage": True, "topic_eval": False},
        "metrics": {"voc_total": 119981},
        "sources": {},
        "backend": "http://test",
        "generated_at_utc": "2026-06-05T20:38:00+00:00",
    }
    claims = parse_report(report, live, threshold=0.10)
    assert len(claims) >= 1

    # 첫 inject → 변경됨.
    changed_first = inject_sync_block(report, claims)
    assert changed_first is True
    after_first = report.read_text(encoding="utf-8")
    assert "<!-- workflow-sync:begin -->" in after_first
    assert "<!-- workflow-sync:end -->" in after_first
    assert "워크플로우 자동 동기화" in after_first
    assert "voc_total" in after_first
    # 원본 본문 보존.
    assert "# R20 IDEM TEST" in after_first
    assert "| voc_total | 118,430 |" in after_first

    # 두 번째 inject (같은 claim) → 무변경.
    changed_second = inject_sync_block(report, claims)
    after_second = report.read_text(encoding="utf-8")
    assert after_first == after_second
    assert changed_second is False
