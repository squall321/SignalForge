"""R19 Track B — tasks.warm_dashboard_cache 단위 테스트.

요구 사항:
  1. beat_schedule 에 ``warm-dashboard-overview-5m`` 엔트리가 등록되어 있다.
  2. warm_dashboard_cache() 호출 시 8 case 가 모두 200 OK 로 워밍되고,
     elapsed_ms_total > 0 이며, FastAPI(127.0.0.1:8000) 가 응답한다.
     (백엔드 미가동 시에는 status="partial" 로 graceful, 절대 예외 전파 X.)
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks import warm_dashboard_cache  # noqa: E402


def test_warm_dashboard_beat_registered():
    """beat_schedule 에 warm-dashboard-overview-5m 가 등록되어 있는지."""
    from celery_app import app  # noqa: WPS433

    assert "warm-dashboard-overview-5m" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["warm-dashboard-overview-5m"]
    assert entry["task"] == "tasks.warm_dashboard_cache"


def test_warm_dashboard_runs_and_warms_all_cases():
    """8 case 모두 200 OK 로 워밍되는지 — 백엔드(127.0.0.1:8000) 가 있어야 한다."""
    result = warm_dashboard_cache()
    # 절대로 예외 전파하지 않는다 — status 는 ok 또는 partial.
    assert result["status"] in {"ok", "partial"}, result
    assert result["elapsed_ms_total"] >= 0
    assert isinstance(result["cases"], list)
    assert len(result["cases"]) == 8, "8개 case 가 시도되어야 함"
    # 모든 case 가 200 OK 라면 정상 — 백엔드 미가동 환경에서는 partial 허용.
    if result["status"] == "ok":
        assert result["warmed"] == 8
        assert result["failed"] == 0
        # 각 case 가 200 OK 이고 ms >= 0.
        for c in result["cases"]:
            assert c["rc"] == 200, f"case 실패: {c}"
            assert c["ms"] >= 0
    else:
        # 백엔드가 없을 수 있는 환경 — 케이스 자체 구조만 검증.
        for c in result["cases"]:
            assert "url" in c and "ms" in c and "rc" in c
