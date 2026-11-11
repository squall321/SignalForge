# 병합으로 들어온 알림(merged)은 slack-notifier 가 다시 보내지 않는다 — 실제 고르기 SQL 을 임시 표에 돌려 본다(DB 가 없으면 건너뛴다)
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight import slack_notifier  # noqa: E402

asyncpg = pytest.importorskip("asyncpg")


def _dsn() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        pw = os.getenv("PGPASSWORD", os.getenv("POSTGRES_PASSWORD", "signalforge_pass"))
        url = (f"postgresql://{os.getenv('POSTGRES_USER', 'signalforge')}:{pw}@{os.getenv('POSTGRES_HOST', '127.0.0.1')}:"
               f"{os.getenv('POSTGRES_PORT', '5434')}/{os.getenv('POSTGRES_DB', 'signalforge')}")
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _picked() -> list:
    try:
        conn = await asyncpg.connect(_dsn(), timeout=5)
    except Exception as exc:                                   # noqa: BLE001
        pytest.skip(f"DB 없음: {exc}")
    try:
        # 임시 표는 이 세션의 이름 찾기에서 public 보다 먼저 온다 — 운영 표는 건드리지 않는다
        await conn.execute("""
            CREATE TEMP TABLE alert_rules (id int PRIMARY KEY, name varchar);
            CREATE TEMP TABLE alert_events (id int PRIMARY KEY, rule_id int, fired_at timestamptz, severity varchar,
                                            value float8, threshold float8, payload jsonb, dispatched_channels varchar[]);
            INSERT INTO alert_rules VALUES (1, 'defect_anomaly');
            INSERT INTO alert_events VALUES
              (1, 1, now() - interval '1 hour', 'warning', 3.9, 3, '{}', ARRAY['slack:dry','merged']),  -- dev 가 보낸 뒤 옴
              (2, 1, now() - interval '1 hour', 'warning', 3.9, 3, '{}', ARRAY['merged']),              -- 덤프 직전이라 표지 없이 옴
              (3, 1, now() - interval '1 hour', 'warning', 3.9, 3, '{}', ARRAY[]::varchar[]);           -- 이 서버에서 난 알림
        """)
        rows = await conn.fetch(slack_notifier._FETCH_UNSENT_SQL, 24, 50)
        return [r["id"] for r in rows]
    finally:
        await conn.close()


def test_merged_events_are_not_sent_again_but_local_ones_are():
    assert asyncio.run(_picked()) == [3]
