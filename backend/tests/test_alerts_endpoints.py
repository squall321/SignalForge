"""P4 트랙 A — alerts endpoint smoke tests.

가동 중인 backend 서버 (http://127.0.0.1:8000) 에 직접 HTTP 호출.
conftest.py 의 engine.dispose autouse fixture 와 TestClient 충돌을 회피.
"""
import os
import pytest
import httpx


BACKEND = os.getenv("SF_BACKEND_URL", "http://127.0.0.1:8000")


def _alive() -> bool:
    try:
        return httpx.get(f"{BACKEND}/health", timeout=1.5).status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_alerts_endpoints_live():
    with httpx.Client(base_url=BACKEND, timeout=20.0) as c:
        # 1) seed 룰 목록
        r = c.get("/api/v1/alerts/rules")
        assert r.status_code == 200
        rules = r.json()
        assert isinstance(rules, list)
        assert len(rules) >= 3
        names = {x["name"] for x in rules}
        assert {"anomaly_z_high", "negative_rate_spike", "new_term_spike"} <= names

        # 2) 최근 발화
        r = c.get("/api/v1/alerts/recent", params={"limit": 5})
        assert r.status_code == 200
        assert isinstance(r.json(), list)

        # 3) 테스트 발화
        r = c.post("/api/v1/alerts/test", json={})
        assert r.status_code == 200
        body = r.json()
        assert "evaluated" in body and "fired" in body and "events" in body
        assert body["evaluated"] >= 3
