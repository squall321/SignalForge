"""insight.ops_history 단위 테스트 (R18 Track D).

summarize 가 collect_status payload 원형에서 슬림 시계열 dict 를 만들고,
save_summary 가 reports/ops_status_YYYY-MM-DD.json 으로 정확히 1파일 저장하는지
확인. DB / HTTP 의존성이 없는 순수 함수 테스트.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.ops_history import save_summary, summarize  # noqa: E402


def _fake_payload() -> dict:
    """operations_monitor.collect_status 가 돌려주는 payload 와 동일 스키마."""
    return {
        "generated_at": "2026-06-05T00:30:00+00:00",
        "status": "warning",
        "thresholds": {
            "voc_daily_drop_pct": 50.0,
            "sentiment_null_rate": 0.10,
            "topic_drop_pct": 20.0,
            "grounding_min": 0.30,
            "regression_ok_min": 1.0,
        },
        "data_quality": {"alerts": []},
        "regression": {"total": 10, "ok": 10, "failed": 0,
                       "ok_ratio": 1.0, "alembic_ok": True},
        "voc": {
            "days": [
                # voc[0]=어제, voc[1]=그제 (DESC)
                {"day": "2026-06-04", "n": 5234,
                 "sentiment_null_rate": 0.02, "topic_rate": 0.89},
                {"day": "2026-06-03", "n": 6120,
                 "sentiment_null_rate": 0.03, "topic_rate": 0.91},
            ],
        },
        "grounding_last": 0.42,
        "violations": [
            {"metric": "data_quality_alerts_count", "severity": "warning",
             "value": 1.0, "threshold": 0.0, "reason": "stub"},
        ],
    }


def test_summarize_extracts_voc_grounding_regression():
    """summarize 가 payload 의 핵심 수치를 top-level 키로 끌어올린다."""
    payload = _fake_payload()
    target = date(2026, 6, 5)
    summary = summarize(payload, target)

    # 메타
    assert summary["target_date"] == "2026-06-05"
    assert summary["captured_at"] == "2026-06-05T00:30:00+00:00"
    assert summary["status"] == "warning"

    # voc
    assert summary["voc_last"] == 5234
    assert summary["voc_prev"] == 6120
    assert summary["sentiment_null_rate"] == 0.02
    assert summary["topic_rate"] == 0.89

    # llm / regression
    assert summary["grounding_last"] == 0.42
    assert summary["regression_ok_ratio"] == 1.0
    assert summary["regression_failed"] == 0

    # violations 보존
    assert summary["violations_count"] == 1
    assert summary["violations"][0]["metric"] == "data_quality_alerts_count"


def test_summarize_handles_empty_voc():
    """voc.days 가 비어 있어도 None 으로 graceful 처리."""
    payload = _fake_payload()
    payload["voc"] = {"days": []}
    payload["regression"] = {}
    summary = summarize(payload, date(2026, 6, 5))

    assert summary["voc_last"] is None
    assert summary["voc_prev"] is None
    assert summary["sentiment_null_rate"] is None
    assert summary["topic_rate"] is None
    assert summary["regression_ok_ratio"] is None
    assert summary["violations_count"] == 1


def test_save_summary_writes_one_dated_file(tmp_path: Path):
    """reports/ops_status_YYYY-MM-DD.json 형식으로 정확히 1파일 생성 + 재실행 시 덮어쓴다."""
    target = date(2026, 6, 5)
    s1 = summarize(_fake_payload(), target)
    p1 = save_summary(s1, target, report_dir=tmp_path)

    assert p1.name == "ops_status_2026-06-05.json"
    assert p1.is_file()
    body = json.loads(p1.read_text(encoding="utf-8"))
    assert body["target_date"] == "2026-06-05"
    assert body["voc_last"] == 5234

    # 같은 날짜 재실행 → 덮어쓰기 (파일 1개 유지)
    payload2 = _fake_payload()
    payload2["voc"]["days"][0]["n"] = 9999
    s2 = summarize(payload2, target)
    p2 = save_summary(s2, target, report_dir=tmp_path)
    assert p2 == p1
    assert json.loads(p2.read_text(encoding="utf-8"))["voc_last"] == 9999
    assert len(list(tmp_path.glob("ops_status_*.json"))) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
