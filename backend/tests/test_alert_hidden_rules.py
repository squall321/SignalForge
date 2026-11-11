# ALERT_HIDDEN_RULES 가 알림 화면 셋(/alerts/rules · /alerts/recent · alert-monitor)에서 그 규칙을 목록과 합계 모두에서 빼는지 본다
"""cae00 은 운영 알림 넷을 가리고 결함 급등만 보인다(2026-11-11). 비면 dev 화면 그대로다.

세션 안 TEMP 표가 같은 이름의 진짜 표를 가린다 — 진짜 알림 행은 읽지도 쓰지도 않는다.
DB 가 없는 환경이면 건너뛴다.
"""
import asyncio

import pytest
from sqlalchemy import text

from app.api import alerts as alerts_api
from app.api._internal import _alert_monitor_payload
from app.config import settings
from app.database import AsyncSessionLocal, engine

_SETUP = [
    "CREATE TEMP TABLE alert_rules (LIKE public.alert_rules INCLUDING INDEXES)",    # 기본 키 — GROUP BY r.id 가 기댄다
    "CREATE TEMP TABLE alert_events (LIKE public.alert_events INCLUDING INDEXES)",
    """INSERT INTO alert_rules (id, name, metric_path, op, threshold, severity, cooldown_sec, is_active, created_at)
       VALUES (1, 'collection_health', 'collection.site.n_24h', '<', 1, 'warning',  3600, true, now()),
              (2, 'defect_anomaly',    'defect.share_ratio',    '>', 3, 'warning',  3600, true, now()),
              (3, 'backup_fail',       'system.backup_ok',      '<', 1, 'critical', 3600, true, now())""",
    # 가린 규칙이 cooldown(3600s) 보다 촘촘히 두 번 — 그 위반 1건도 합계에서 빠져야 한다
    """INSERT INTO alert_events (id, rule_id, fired_at, severity, value, threshold, payload, dispatched_channels)
       VALUES (1, 1, now() - interval '1 hour',          'warning',  0, 1, '{}', '{}'),
              (2, 1, now() - interval '1 hour 1 minute', 'warning',  0, 1, '{}', '{}'),
              (3, 2, now() - interval '2 hours',         'warning',  4, 3, '{}', '{merged}'),
              (4, 3, now() - interval '3 hours',         'critical', 0, 1, '{}', '{}')""",
]


def _db_alive() -> bool:
    async def ping():
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(text("SELECT 1"))
        finally:
            await engine.dispose()   # 이 루프에 묶인 연결을 남기지 않는다 — 시험은 새 루프에서 돈다
    try:
        asyncio.run(ping())
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _db_alive(), reason="DB 없음")
def test_hidden_rules_leave_lists_and_counts(monkeypatch):
    async def no_metrics(db):
        return {}
    monkeypatch.setattr(alerts_api, "collect_metrics", no_metrics)   # 지표 계산은 이 시험 밖

    async def screen(db, hidden_setting):
        monkeypatch.setattr(settings, "ALERT_HIDDEN_RULES", hidden_setting)
        rules = [r.name for r in await alerts_api.list_rules(db=db)]
        recent = [e.rule_name for e in await alerts_api.recent_events(limit=50, db=db)]
        mon = await _alert_monitor_payload.__wrapped__(db, 7, tuple(settings.alert_hidden_rules_list))
        return rules, recent, mon

    async def run():
        async with AsyncSessionLocal() as db:
            for sql in _SETUP:
                await db.execute(text(sql))
            try:
                return await screen(db, ""), await screen(db, "collection_health, backup_fail")
            finally:
                await db.rollback()

    (rules, recent, mon), (h_rules, h_recent, h_mon) = asyncio.run(run())

    # 비면 그대로 — dev 화면
    assert rules == ["collection_health", "defect_anomaly", "backup_fail"]
    assert recent == ["collection_health", "collection_health", "defect_anomaly", "backup_fail"]
    assert mon["summary"] == {"active_rules": 3, "fires_24h": 4, "fires_7d": 4, "cooldown_violations_24h": 1}

    # 가리면 목록에서도 합계에서도 빠진다 — cae00 화면
    assert h_rules == ["defect_anomaly"]
    assert h_recent == ["defect_anomaly"]
    assert [r["name"] for r in h_mon["rules"]] == ["defect_anomaly"]
    assert h_mon["summary"] == {"active_rules": 1, "fires_24h": 1, "fires_7d": 1, "cooldown_violations_24h": 0}
    assert set(h_mon["metric_distribution"]) == {"defect.share_ratio"}
    assert not any("collection_health" in s or "backup_fail" in s for s in h_mon["recommendations"])
