"""Track E2 — /api/v1/_internal/alert-trends endpoint live smoke test.

가동 중인 backend (http://127.0.0.1:8000) 에 직접 HTTP 호출.
다른 _internal 테스트(test_noise_scan.py)와 동일한 live-server 패턴.

검증:
  - 200 응답 + JSON 스키마 (days, generated_at, cooldown_violations_24h, rules)
  - rules[i] 가 (rule_id, name, metric_path, threshold, cooldown_sec,
    fires_window, fires_24h, avg_value, max_value, last_fired_at, silent_window) 키 보유
  - days 파라미터 1~30 클램프
  - cooldown_violations_24h 가 int >= 0
  - 활성 룰 rule 35 (platforms_negative_share) 가 응답에 포함

실행:
    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_alert_trends.py -v
"""
import os

import httpx
import pytest


BACKEND = os.getenv("SF_BACKEND_URL", "http://127.0.0.1:8000")

_REQUIRED_RULE_KEYS = {
    "rule_id", "name", "metric_path", "threshold", "cooldown_sec",
    "fires_window", "fires_24h", "avg_value", "max_value",
    "last_fired_at", "silent_window",
}


def _alive() -> bool:
    try:
        return httpx.get(f"{BACKEND}/health", timeout=1.5).status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_alert_trends_live():
    with httpx.Client(base_url=BACKEND, timeout=20.0) as c:
        # 1) 기본 호출 — days=7.
        r = c.get("/api/v1/_internal/alert-trends", params={"days": 7})
        assert r.status_code == 200, f"status={r.status_code} body={r.text}"
        body = r.json()
        assert set(body.keys()) >= {
            "days", "generated_at", "cooldown_violations_24h", "rules",
        }, body
        assert body["days"] == 7
        assert isinstance(body["cooldown_violations_24h"], int)
        assert body["cooldown_violations_24h"] >= 0
        assert isinstance(body["rules"], list)
        assert body["rules"], "활성 룰 0 — alembic seed 가 비어있나?"

        rule_names = set()
        for rule in body["rules"]:
            assert set(rule.keys()) >= _REQUIRED_RULE_KEYS, rule
            assert isinstance(rule["rule_id"], int)
            assert isinstance(rule["name"], str) and rule["name"]
            assert isinstance(rule["metric_path"], str)
            assert isinstance(rule["threshold"], (int, float))
            assert isinstance(rule["cooldown_sec"], int)
            assert isinstance(rule["fires_window"], int) and rule["fires_window"] >= 0
            assert isinstance(rule["fires_24h"], int) and rule["fires_24h"] >= 0
            # avg/max 는 fires=0 일 때 None
            if rule["fires_window"] == 0:
                assert rule["avg_value"] is None
                assert rule["max_value"] is None
                assert rule["silent_window"] is True
            else:
                assert isinstance(rule["avg_value"], (int, float))
                assert isinstance(rule["max_value"], (int, float))
                assert rule["silent_window"] is False
            rule_names.add(rule["name"])

        # 활성 룰 35 (platforms_negative_share) 가 시드되어 있어야 한다.
        assert "platforms_negative_share" in rule_names, rule_names

        # 2) days 파라미터 — 1 도 정상.
        r2 = c.get("/api/v1/_internal/alert-trends", params={"days": 1})
        assert r2.status_code == 200, r2.text
        assert r2.json()["days"] == 1

        # 3) days 경계 — 31 은 422 (1~30 클램프).
        r3 = c.get("/api/v1/_internal/alert-trends", params={"days": 31})
        assert r3.status_code == 422, r3.text
