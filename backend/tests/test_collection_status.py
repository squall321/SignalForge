"""Track B — /api/v1/_internal/collection-status live smoke test.

가동 중인 backend (http://127.0.0.1:8000) 에 직접 HTTP 호출.
test_noise_scan.py 와 동일한 live-server 패턴 (TestClient + autouse engine.dispose
fixture 의 cross-loop 충돌 회피).

검증:
  - 200 응답 + JSON 스키마 (hours, generated_at, summary, platforms, by_region)
  - summary 필드 4개 모두 int (total_active, total_inactive, total_records_24h, total_records_1h)
  - platforms 길이 >= 60 (활성 62 + 비활성 11 = 73 기준, 60+ 운영 합의)
  - 각 platform 행 필수 키 보유 + health 가 {'active','slow','stale','dead'} 중 하나
  - total_active == sum(p.is_active=True), records_24h sum 일치
  - by_region 최소 'KR' 보유 (운영 데이터 기준 KR 사이트 다수)

실행:
    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_collection_status.py -v
"""
import os

import httpx
import pytest


BACKEND = os.getenv("SF_BACKEND_URL", "http://127.0.0.1:8000")
_HEALTH_VALUES = {"active", "slow", "stale", "dead"}
_REQUIRED_PLATFORM_KEYS = {
    "code", "name", "region", "is_active",
    "records_24h", "records_1h", "records_7d",
    "last_collected", "hours_since_last",
    "avg_per_day_7d", "health",
}


def _alive() -> bool:
    try:
        return httpx.get(f"{BACKEND}/health", timeout=1.5).status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_collection_status_live():
    with httpx.Client(base_url=BACKEND, timeout=20.0) as c:
        r = c.get("/api/v1/_internal/collection-status", params={"hours": 24})
        assert r.status_code == 200, f"status={r.status_code} body={r.text}"
        body = r.json()

        # 1) 최상위 스키마
        assert set(body.keys()) >= {
            "hours", "generated_at", "summary", "platforms", "by_region"
        }, body.keys()
        assert body["hours"] == 24

        # 2) summary 4 카운터
        summary = body["summary"]
        for k in ("total_active", "total_inactive", "total_records_24h", "total_records_1h"):
            assert k in summary, summary
            assert isinstance(summary[k], int), (k, summary[k])

        # 3) platforms 60+ (실 운영 73 중 최소 60 보장)
        platforms = body["platforms"]
        assert isinstance(platforms, list)
        assert len(platforms) >= 60, f"platforms={len(platforms)} (expected >= 60)"

        # 4) 각 행 스키마 + health enum
        active_count = 0
        records_24h_sum = 0
        for p in platforms:
            assert set(p.keys()) >= _REQUIRED_PLATFORM_KEYS, p
            assert isinstance(p["code"], str) and p["code"]
            assert isinstance(p["records_24h"], int) and p["records_24h"] >= 0
            assert isinstance(p["records_1h"], int) and p["records_1h"] >= 0
            assert isinstance(p["records_7d"], int) and p["records_7d"] >= 0
            assert p["health"] in _HEALTH_VALUES, p["health"]
            if p["is_active"]:
                active_count += 1
            records_24h_sum += p["records_24h"]

        # 5) summary 정합성
        assert summary["total_active"] == active_count, (
            summary["total_active"], active_count,
        )
        assert summary["total_records_24h"] == records_24h_sum, (
            summary["total_records_24h"], records_24h_sum,
        )

        # 6) by_region 에 KR 존재 (운영 KR 사이트 다수)
        assert "KR" in body["by_region"], list(body["by_region"].keys())
        kr = body["by_region"]["KR"]
        assert {"active", "total", "records_24h"} <= set(kr.keys()), kr
