"""insight.operations_monitor 단위 테스트 (3 케이스).

1) evaluate_violations — 6 점검 룰의 정확한 발화 / 미발화
2) overall_status — critical / warning / ok 매핑
3) insert_alert_events cooldown — 직전 1시간 내 발화 있으면 0
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.operations_monitor import (  # noqa: E402
    _overall_status,
    evaluate_violations,
    insert_alert_events,
)


def test_evaluate_violations_triggers_all_six():
    """6 metric 을 한꺼번에 위반시키는 합성 payload — 각 metric 1회씩 발화."""
    payload: Dict[str, Any] = {
        # 1) data_quality alerts ≥ 1
        "data_quality": {
            "alerts": [
                {"metric": "duplicate_rate", "level": "warning"},
            ],
        },
        # 2) regression ok_ratio < 1.0
        "regression": {"total": 10, "ok": 8, "failed": 2, "ok_ratio": 0.80,
                       "alembic_ok": True},
        # 3) voc 일별 50% 감소 + 4) sentiment NULL > 0.10 (어제) + 5) topic 20% 감소
        "voc": {"days": [
            # voc[0]=어제, voc[1]=그제 (DESC)
            {"day": "2026-06-03", "n": 3000, "sentiment_null_rate": 0.25,
             "topic_rate": 0.50},
            {"day": "2026-06-02", "n": 10000, "sentiment_null_rate": 0.02,
             "topic_rate": 0.90},
        ]},
        # 6) grounding < 0.3
        "grounding_last": 0.20,
    }
    violations = evaluate_violations(payload)
    metrics = sorted(v["metric"] for v in violations)
    assert metrics == [
        "data_quality_alerts_count",
        "llm_grounding_last",
        "regression_ok_ratio",
        "sentiment_null_rate",
        "topic_classified_rate_drop",
        "voc_daily_drop_pct",
    ], f"got {metrics}"

    # severity 매핑 확인 — regression 만 critical, 나머지 warning
    sev = {v["metric"]: v["severity"] for v in violations}
    assert sev["regression_ok_ratio"] == "critical"
    assert sev["voc_daily_drop_pct"] == "warning"
    assert sev["llm_grounding_last"] == "warning"


def test_overall_status_severity_mapping():
    """_overall_status 가 critical > warning > ok 우선순위로 동작."""
    assert _overall_status([]) == "ok"
    assert _overall_status([{"severity": "warning"}]) == "warning"
    assert _overall_status([
        {"severity": "warning"},
        {"severity": "critical"},
    ]) == "critical"


def test_insert_alert_events_cooldown_skips(monkeypatch):
    """직전 cooldown_sec 이내 발화가 있으면 INSERT 0건."""

    class FakeConn:
        def __init__(self):
            self.executed: List[tuple] = []

        async def fetchrow(self, query, *args):
            # alert_rules.operations_monitor
            return {
                "id": 999,
                "severity": "warning",
                "threshold": 0.0,
                "cooldown_sec": 3600,
            }

        async def fetchval(self, query, *args):
            # 5분 전 발화 → cooldown 활성
            return datetime.now(timezone.utc) - timedelta(seconds=300)

        async def execute(self, *args, **kwargs):
            self.executed.append(args)

    conn = FakeConn()
    violations = [
        {"metric": "voc_daily_drop_pct", "severity": "warning",
         "value": 70.0, "threshold": 50.0, "reason": "test"},
    ]
    inserted = asyncio.run(insert_alert_events(conn, violations))
    assert inserted == 0
    assert conn.executed == []


def test_insert_alert_events_no_rule_returns_zero():
    """alert_rules.operations_monitor 가 없으면 0 (graceful)."""

    class FakeConn:
        async def fetchrow(self, query, *args):
            return None

        async def fetchval(self, query, *args):  # pragma: no cover
            return None

        async def execute(self, *args, **kwargs):  # pragma: no cover
            pass

    inserted = asyncio.run(insert_alert_events(
        FakeConn(),
        [{"metric": "x", "severity": "warning", "value": 1.0, "threshold": 0.0,
          "reason": "y"}],
    ))
    assert inserted == 0


def test_insert_alert_events_inserts_when_cooldown_passed():
    """cooldown 경과 시 위반 수만큼 INSERT."""

    class FakeConn:
        def __init__(self):
            self.execute_calls: List[tuple] = []

        async def fetchrow(self, query, *args):
            return {
                "id": 999,
                "severity": "warning",
                "threshold": 0.0,
                "cooldown_sec": 60,
            }

        async def fetchval(self, query, *args):
            # 충분히 옛날
            return datetime.now(timezone.utc) - timedelta(hours=2)

        async def execute(self, *args, **kwargs):
            self.execute_calls.append(args)

    conn = FakeConn()
    violations = [
        {"metric": "a", "severity": "warning", "value": 1.0,
         "threshold": 0.0, "reason": "r1"},
        {"metric": "b", "severity": "critical", "value": 2.0,
         "threshold": 1.0, "reason": "r2"},
    ]
    inserted = asyncio.run(insert_alert_events(conn, violations))
    assert inserted == 2
    assert len(conn.execute_calls) == 2
