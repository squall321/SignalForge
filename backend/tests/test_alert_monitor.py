"""Track A — /api/v1/_internal/alert-monitor endpoint live smoke test.

가동 중인 backend (http://127.0.0.1:8000) 에 직접 HTTP 호출.
다른 _internal 테스트(test_alert_trends.py, test_noise_scan.py)와 동일 패턴.

검증:
  1) 200 응답 + 최상위 스키마 (days, generated_at, summary, rules,
     metric_distribution, recommendations) 보유.  summary 4 필드.
  2) recommendations 가 list[str] 이고, 각 룰의 health 값과 일관된 권고를 만든다:
     - silent rule  → "임계 검토" 문구 포함
     - violating rule → "cooldown 위반" 문구 포함
     - noisy rule  → "검토 — 24h ... 발화" 문구 포함

실행:
    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_alert_monitor.py -v
"""
import os

import httpx
import pytest


BACKEND = os.getenv("SF_BACKEND_URL", "http://127.0.0.1:8000")

_REQUIRED_TOP_KEYS = {
    "days", "generated_at", "summary", "rules",
    "metric_distribution", "recommendations",
}
_REQUIRED_SUMMARY_KEYS = {
    "active_rules", "fires_24h", "fires_7d", "cooldown_violations_24h",
}
_REQUIRED_RULE_KEYS = {
    "rule_id", "name", "metric_path", "threshold", "cooldown_sec",
    "severity", "fires_24h", "fires_7d", "avg_value_7d", "max_value_7d",
    "last_fired_at", "cooldown_violations_24h", "silent_window", "health",
}
_HEALTH_VALUES = {"normal", "silent", "noisy", "violating"}


def _alive() -> bool:
    try:
        return httpx.get(f"{BACKEND}/health", timeout=1.5).status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_alert_monitor_schema_live():
    """1) 200 + 최상위/summary/rules 스키마 + metric_distribution 형식."""
    with httpx.Client(base_url=BACKEND, timeout=20.0) as c:
        r = c.get("/api/v1/_internal/alert-monitor", params={"days": 7})
        assert r.status_code == 200, f"status={r.status_code} body={r.text}"
        body = r.json()
        assert set(body.keys()) >= _REQUIRED_TOP_KEYS, body
        assert body["days"] == 7

        # summary
        s = body["summary"]
        assert set(s.keys()) >= _REQUIRED_SUMMARY_KEYS, s
        for k in _REQUIRED_SUMMARY_KEYS:
            assert isinstance(s[k], int) and s[k] >= 0, (k, s[k])

        # rules
        assert isinstance(body["rules"], list)
        assert body["rules"], "활성 룰 0 — alembic seed 가 비어있나?"
        for rule in body["rules"]:
            assert set(rule.keys()) >= _REQUIRED_RULE_KEYS, rule
            assert rule["health"] in _HEALTH_VALUES, rule["health"]
            assert isinstance(rule["fires_7d"], int) and rule["fires_7d"] >= 0
            assert isinstance(rule["fires_24h"], int) and rule["fires_24h"] >= 0
            assert rule["silent_window"] == (rule["fires_7d"] == 0)
            if rule["fires_7d"] == 0:
                assert rule["avg_value_7d"] is None
                assert rule["max_value_7d"] is None
            assert isinstance(rule["cooldown_violations_24h"], int)
            assert rule["cooldown_violations_24h"] >= 0

        # metric_distribution: 모든 활성 룰의 metric_path 가 키로 존재
        md = body["metric_distribution"]
        assert isinstance(md, dict) and md
        for rule in body["rules"]:
            assert rule["metric_path"] in md, rule["metric_path"]
            entry = md[rule["metric_path"]]
            assert {"p50", "p90", "p95", "p99", "n", "current"} <= set(entry.keys())
            assert isinstance(entry["n"], int) and entry["n"] >= 0
            # n>0 이면 percentile 필드는 숫자
            if entry["n"] > 0:
                for q in ("p50", "p90", "p95", "p99"):
                    assert isinstance(entry[q], (int, float)), (q, entry[q])


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_alert_monitor_recommendations_format_live():
    """2) recommendations 형식 — 각 health 별 권고 키워드 일관성."""
    with httpx.Client(base_url=BACKEND, timeout=20.0) as c:
        body = c.get("/api/v1/_internal/alert-monitor", params={"days": 7}).json()
        recs = body["recommendations"]
        assert isinstance(recs, list)
        for line in recs:
            assert isinstance(line, str) and line

        # 룰 health 별 권고가 존재해야 하는 키워드
        for rule in body["rules"]:
            health = rule["health"]
            if health == "silent":
                # silent 면 "임계 검토" + 룰 이름 또는 rule id 가 한 권고에 들어가야 함
                key = f"`{rule['name']}`"
                assert any(
                    "임계 검토" in line and key in line
                    for line in recs
                ), f"silent 권고 누락: {rule['name']}"
            elif health == "violating":
                key = f"`{rule['name']}`"
                assert any(
                    "cooldown 위반" in line and key in line
                    for line in recs
                ), f"violating 권고 누락: {rule['name']}"
            elif health == "noisy":
                key = f"`{rule['name']}`"
                assert any(
                    "24h" in line and "발화" in line and key in line
                    for line in recs
                ), f"noisy 권고 누락: {rule['name']}"
            # normal 은 권고 없음 — 별도 검증 안 함
