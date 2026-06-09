"""insight.ops_alerts 단위 테스트 (R20 Track C).

파일 입력 → severity 분류 → cooldown 가드 → 발화 시뮬레이션 (DB 미접속) 을 검증.
DB 의존성이 필요한 INSERT 경로는 fake conn 으로 격리.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.ops_alerts import (  # noqa: E402
    classify_severity,
    insert_alert_events,
    load_ops_status,
    severity_distribution,
)


# ── 픽스처 ─────────────────────────────────────────────────────────────
def _fake_ops_status() -> Dict[str, Any]:
    """ops_history.save_summary 산출물과 동일 스키마."""
    return {
        "captured_at": "2026-06-05T11:36:40+00:00",
        "target_date": "2026-06-05",
        "status": "critical",
        "voc_last": 4806,
        "voc_prev": 37135,
        "sentiment_null_rate": 0.0,
        "topic_rate": 0.0988,
        "grounding_last": 0.3563,
        "regression_ok_ratio": 0.5455,
        "regression_failed": 5,
        "violations_count": 2,
        "violations": [
            {
                "metric": "regression_ok_ratio",
                "severity": "critical",
                "value": 0.5455,
                "threshold": 1.0,
                "reason": "regression ok_ratio=0.545 < 1.0 (failed=5)",
            },
            {
                "metric": "voc_daily_drop_pct",
                "severity": "warning",
                "value": 87.06,
                "threshold": 50.0,
                "reason": "voc 37135 → 4806 (87.1% 감소)",
            },
        ],
    }


# ── classify_severity / severity_distribution ─────────────────────────
def test_classify_severity_recognizes_critical_warning_info():
    """입력 violation 의 severity 라벨이 정확히 매핑된다."""
    assert classify_severity({"severity": "critical"}) == "critical"
    assert classify_severity({"severity": "warning"}) == "warning"
    assert classify_severity({"severity": "info"}) == "info"
    assert classify_severity({"severity": "CRITICAL"}) == "critical"  # case-insensitive
    assert classify_severity({}) == "info"  # 없으면 info
    assert classify_severity({"severity": "unknown_label"}) == "info"


def test_severity_distribution_counts_correctly():
    """전체 violations 의 severity 분포 계산."""
    vs = _fake_ops_status()["violations"]
    dist = severity_distribution(vs)
    assert dist == {"critical": 1, "warning": 1, "info": 0}

    # 빈 입력
    assert severity_distribution([]) == {"critical": 0, "warning": 0, "info": 0}


# ── load_ops_status ────────────────────────────────────────────────────
def test_load_ops_status_reads_existing_file(tmp_path: Path):
    """ops_status_YYYY-MM-DD.json 이 있으면 dict 로 로드."""
    target = date(2026, 6, 5)
    body = _fake_ops_status()
    path = tmp_path / f"ops_status_{target.isoformat()}.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    loaded = load_ops_status(target, report_dir=tmp_path)
    assert loaded is not None
    assert loaded["target_date"] == "2026-06-05"
    assert loaded["violations_count"] == 2


def test_load_ops_status_returns_none_when_missing(tmp_path: Path):
    """파일 없으면 None — graceful."""
    assert load_ops_status(date(2026, 6, 5), report_dir=tmp_path) is None


# ── insert_alert_events (fake conn) ────────────────────────────────────
class _FakeConn:
    """asyncpg.Connection 의 최소 인터페이스를 흉내내는 in-memory fake.

    검증 포인트:
    - rule_resolved 가 True 일 때만 INSERT 진행
    - cooldown_window 내 metric 은 skip
    - INSERT 호출 시 payload 검증
    """
    def __init__(
        self,
        *,
        rule_id: int = 80,
        cooldown_sec: int = 3600,
        last_fired_per_metric: Optional[Dict[str, datetime]] = None,
        rule_present: bool = True,
    ):
        self.rule_id = rule_id
        self.cooldown_sec = cooldown_sec
        self.rule_present = rule_present
        self.last_fired = last_fired_per_metric or {}
        self.inserts: List[Dict[str, Any]] = []

    async def fetchrow(self, query: str, *args):
        if "alert_rules" in query and self.rule_present:
            return {
                "id": self.rule_id,
                "severity": "warning",
                "threshold": 0.0,
                "cooldown_sec": self.cooldown_sec,
            }
        return None

    async def fetch(self, query: str, *args):
        # _last_fired_per_metric 의 호출.
        return [
            {"metric": m, "last": t}
            for m, t in self.last_fired.items()
        ]

    async def execute(self, query: str, *args):
        # alert_events INSERT 캡쳐
        self.inserts.append({
            "rule_id": args[0],
            "severity": args[1],
            "value": args[2],
            "threshold": args[3],
            "payload": json.loads(args[4]),
        })


@pytest.mark.asyncio
async def test_insert_alert_events_inserts_when_no_cooldown():
    """cooldown 윈도우 밖 → 모든 위반이 INSERT 된다."""
    conn = _FakeConn(last_fired_per_metric={})
    vs = _fake_ops_status()["violations"]
    inserted, skipped = await insert_alert_events(
        conn, vs, target_date=date(2026, 6, 5),
    )
    assert inserted == 2
    assert skipped == 0
    assert len(conn.inserts) == 2

    # 첫 위반 (regression critical) payload 확인
    e0 = conn.inserts[0]
    assert e0["severity"] == "critical"
    assert e0["payload"]["type"] == "ops_status_violation"
    assert e0["payload"]["metric"] == "regression_ok_ratio"
    assert e0["payload"]["source_date"] == "2026-06-05"
    assert e0["payload"]["violation"]["value"] == 0.5455
    assert e0["payload"]["violation"]["threshold"] == 1.0

    # 두 번째 위반 (voc warning)
    e1 = conn.inserts[1]
    assert e1["severity"] == "warning"
    assert e1["payload"]["metric"] == "voc_daily_drop_pct"


@pytest.mark.asyncio
async def test_insert_alert_events_skips_metric_in_cooldown():
    """metric 단위 cooldown 내 → skip, 다른 metric 은 INSERT."""
    now = datetime.now(timezone.utc)
    # regression_ok_ratio 는 5분 전 발화 → cooldown 내 skip
    # voc_daily_drop_pct 는 발화 이력 없음 → INSERT
    conn = _FakeConn(
        cooldown_sec=3600,
        last_fired_per_metric={
            "regression_ok_ratio": now - timedelta(minutes=5),
        },
    )
    vs = _fake_ops_status()["violations"]
    inserted, skipped = await insert_alert_events(conn, vs)
    assert inserted == 1
    assert skipped == 1
    assert len(conn.inserts) == 1
    assert conn.inserts[0]["payload"]["metric"] == "voc_daily_drop_pct"


@pytest.mark.asyncio
async def test_insert_alert_events_graceful_when_rule_missing():
    """alert_rules.ops_status_violation 비활성 → (0, 0) 반환, INSERT 없음."""
    conn = _FakeConn(rule_present=False)
    vs = _fake_ops_status()["violations"]
    inserted, skipped = await insert_alert_events(conn, vs)
    assert inserted == 0
    assert skipped == 0
    assert conn.inserts == []


@pytest.mark.asyncio
async def test_insert_alert_events_empty_violations_short_circuits():
    """위반이 없으면 룰 조회 없이 즉시 (0, 0)."""
    conn = _FakeConn()
    inserted, skipped = await insert_alert_events(conn, [])
    assert inserted == 0
    assert skipped == 0
    # 룰 조회조차 안 함
    assert conn.inserts == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
