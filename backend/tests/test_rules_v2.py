"""Track C — alert_rules v2 seed 검증.

검증 대상:
  - alembic 0007 이 platforms_negative_share_watch (info, 0.08, 3600s) 시드.
  - 기존 rule 35 (platforms_negative_share, warning, 0.15) 와 *동일 metric_path* 이지만
    severity / threshold 가 분리되어 이중 thresholds 패턴을 형성.
  - /api/v1/_internal/alert-trends 응답에 두 룰이 모두 노출.

실행:
    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_rules_v2.py -v
"""
import asyncio
import os

import httpx
import pytest
from sqlalchemy import text

from app.database import AsyncSessionLocal


BACKEND = os.getenv("SF_BACKEND_URL", "http://127.0.0.1:8000")


def _alive() -> bool:
    try:
        return httpx.get(f"{BACKEND}/health", timeout=1.5).status_code == 200
    except Exception:
        return False


def test_watch_rule_seeded_in_db():
    """0007 seed 가 DB 에 반영되었는지 — 같은 metric_path, 다른 severity 두 룰."""
    async def _run():
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    text(
                        """
                        SELECT name, metric_path, op, threshold, severity, cooldown_sec, is_active
                        FROM alert_rules
                        WHERE metric_path = 'community.platforms_negative_pct'
                        ORDER BY threshold
                        """
                    )
                )
            ).all()
        return rows

    rows = asyncio.run(_run())
    assert len(rows) >= 2, f"이중 thresholds 패턴 미적용: {rows}"

    by_name = {r.name: r for r in rows}
    assert "platforms_negative_share_watch" in by_name, by_name
    assert "platforms_negative_share" in by_name, by_name

    watch = by_name["platforms_negative_share_watch"]
    assert watch.severity == "info"
    assert watch.op == ">"
    assert abs(watch.threshold - 0.08) < 1e-9
    assert watch.cooldown_sec == 3600
    assert watch.is_active is True

    main = by_name["platforms_negative_share"]
    assert main.severity == "warning"
    assert main.threshold > watch.threshold, (
        f"warning 임계 ({main.threshold}) 가 info ({watch.threshold}) 보다 커야 함"
    )


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_watch_rule_in_alert_trends():
    """/api/v1/_internal/alert-trends 응답에 watch 룰이 포함."""
    with httpx.Client(base_url=BACKEND, timeout=15.0) as c:
        r = c.get("/api/v1/_internal/alert-trends", params={"days": 7})
        assert r.status_code == 200, r.text
        body = r.json()
        names = {rule["name"] for rule in body["rules"]}
        assert "platforms_negative_share_watch" in names, names
        assert "platforms_negative_share" in names, names

        watch = next(
            r for r in body["rules"] if r["name"] == "platforms_negative_share_watch"
        )
        assert watch["threshold"] == 0.08
        assert watch["cooldown_sec"] == 3600
