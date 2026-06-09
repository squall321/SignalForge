"""R22 Track C — /api/v1/_internal/ops-alerts-history endpoint live smoke test.

가동 중인 backend (http://127.0.0.1:8000) 에 직접 HTTP 호출.
다른 _internal 테스트 (test_alert_trends.py) 와 동일한 live-server 패턴.

검증
~~~~
- 200 응답 + JSON 스키마 (rule, days, generated_at, total_fires,
  severity_counts, by_day, by_metric, cooldown_violations,
  operations_monitor_compare, recommendations, events)
- rule 메타에 id, name='ops_status_violation', cooldown_sec=3600, is_active
- severity_counts 의 합 == total_fires == len(events)
- by_day 의 n 합 == total_fires
- by_metric 의 n 합 == total_fires
- operations_monitor_compare 항목은 metric / fires_rule80 / fires_rule78 보유
- recommendations 는 최소 1개 권고 문자열 보유
- days 경계 (1, 30) 정상 200 / 31 은 422

실행::

    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_ops_alerts_history.py -v
"""
import os

import httpx
import pytest


BACKEND = os.getenv("SF_BACKEND_URL", "http://127.0.0.1:8000")


def _alive() -> bool:
    try:
        return httpx.get(f"{BACKEND}/health", timeout=1.5).status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_ops_alerts_history_live():
    with httpx.Client(base_url=BACKEND, timeout=20.0) as c:
        # 1) 기본 호출 — days=7.
        r = c.get("/api/v1/_internal/ops-alerts-history", params={"days": 7})
        assert r.status_code == 200, f"status={r.status_code} body={r.text}"
        body = r.json()

        required_top = {
            "rule", "days", "generated_at", "total_fires",
            "severity_counts", "by_day", "by_metric", "cooldown_violations",
            "operations_monitor_compare", "recommendations", "events",
        }
        assert required_top <= set(body.keys()), set(body.keys())
        assert body["days"] == 7

        # rule 메타 스키마.
        rule = body["rule"]
        assert isinstance(rule, dict)
        assert {"id", "name", "threshold", "cooldown_sec", "severity_default", "is_active"} <= set(rule.keys())
        assert rule["name"] == "ops_status_violation"
        assert rule["cooldown_sec"] == 3600
        assert rule["is_active"] is True

        # 합계 정합성.
        total = int(body["total_fires"])
        assert total >= 0
        assert isinstance(body["events"], list)
        assert len(body["events"]) == total

        sev = body["severity_counts"]
        assert {"critical", "warning", "info"} <= set(sev.keys())
        assert sum(int(v) for v in sev.values()) == total

        # by_day 정합성 — 일별 n 합 == total.
        assert isinstance(body["by_day"], list)
        assert sum(int(d["n"]) for d in body["by_day"]) == total
        for d in body["by_day"]:
            assert {"day", "critical", "warning", "info", "n"} <= set(d.keys())

        # by_metric 정합성 — metric 별 n 합 == total.
        assert isinstance(body["by_metric"], list)
        assert sum(int(m["n"]) for m in body["by_metric"]) == total
        for m in body["by_metric"]:
            assert {"metric", "n", "severity", "first_fired_at", "last_fired_at"} <= set(m.keys())

        # operations_monitor 비교 — 항상 list (rule 78 미시드면 빈 리스트).
        assert isinstance(body["operations_monitor_compare"], list)
        for entry in body["operations_monitor_compare"]:
            assert {"metric", "fires_rule80", "fires_rule78", "dedupe_ratio"} <= set(entry.keys())
            assert int(entry["fires_rule78"]) >= 0
            assert int(entry["fires_rule80"]) >= 0

        # cooldown_violations — 항상 list, 항목은 metric/gap_seconds 보유.
        assert isinstance(body["cooldown_violations"], list)
        for cv in body["cooldown_violations"]:
            assert {"metric", "gap_seconds", "cooldown_sec",
                    "first_fired_at", "second_fired_at"} <= set(cv.keys())
            assert float(cv["gap_seconds"]) < float(cv["cooldown_sec"])

        # 권고 — 최소 1개 (없으면 "이상 패턴 없음").
        assert isinstance(body["recommendations"], list)
        assert len(body["recommendations"]) >= 1
        for rec in body["recommendations"]:
            assert isinstance(rec, str) and rec.strip()

        # 2) days=1 경계 정상.
        r2 = c.get("/api/v1/_internal/ops-alerts-history", params={"days": 1})
        assert r2.status_code == 200, r2.text
        assert r2.json()["days"] == 1

        # 3) days=31 은 422 (1~30 클램프).
        r3 = c.get("/api/v1/_internal/ops-alerts-history", params={"days": 31})
        assert r3.status_code == 422, r3.text
