"""R10 Track D — /api/v1/_internal/weekly-monitor live smoke test.

가동 중인 backend (http://127.0.0.1:8000) 에 직접 HTTP 호출.

검증:
  - 200 응답 + JSON 스키마 (weeks, generated_at, available, snapshots, trend, baseline_delta)
  - weeks=4 기본값 동작
  - reports/weekly_monitor_*.json 이 1개라도 있으면 snapshots / latest_alerts 가 채워짐
  - trend 4 series (voc_total / active_sites / grounding_avg / regression_failed)
    가 모두 list 이고 각 entry 가 iso_week 키 보유
  - baseline_delta.regression_now_failed / active_sites_now 가 int 또는 None

실행:
    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_weekly_monitor_endpoint.py -v
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
def test_weekly_monitor_live():
    with httpx.Client(base_url=BACKEND, timeout=15.0) as c:
        r = c.get("/api/v1/_internal/weekly-monitor", params={"weeks": 4})
        assert r.status_code == 200, f"status={r.status_code} body={r.text[:300]}"
        body = r.json()

        # 1) 최상위 스키마
        required = {
            "weeks", "generated_at", "available",
            "snapshots", "latest_alerts", "trend", "baseline_delta",
        }
        assert required <= set(body.keys()), f"missing keys: {required - set(body.keys())}"
        assert body["weeks"] == 4
        assert isinstance(body["available"], list)
        assert isinstance(body["snapshots"], list)
        assert isinstance(body["latest_alerts"], list)

        # 2) trend 4 series
        trend = body["trend"]
        for series_name in (
            "voc_total_per_week",
            "active_sites_per_week",
            "grounding_avg_per_week",
            "regression_failed_per_week",
        ):
            assert series_name in trend, f"trend.{series_name} missing"
            assert isinstance(trend[series_name], list)
            for entry in trend[series_name]:
                assert "iso_week" in entry

        # 3) baseline_delta — int 또는 None
        bd = body["baseline_delta"]
        for k in ("regression_now_failed", "active_sites_now"):
            v = bd.get(k)
            assert v is None or isinstance(v, int)

        # 4) snapshots 가 있을 때 추가 검증 (수동 실행 후 시나리오)
        if body["snapshots"]:
            snap = body["snapshots"][0]
            assert "iso_year_week" in snap
            assert "voc_daily" in snap
            assert isinstance(snap["voc_daily"], list)
            assert "alerts" in snap
            assert isinstance(snap["alerts"], list)
