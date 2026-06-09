"""R14 Track E — /api/v1/_internal/ops-status live smoke test.

가동 중인 backend (http://127.0.0.1:8000) 에 직접 HTTP 호출.

검증:
  - 200 응답 + JSON 스키마 (status, generated_at, thresholds, data_quality,
    regression, voc, grounding_last, violations)
  - status ∈ {"ok","warning","critical"}
  - violations 가 list 이고 각 entry 가 (metric, severity, value, threshold, reason)
  - thresholds 가 5종 임계값 보유

실행:
    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_ops_status.py -v
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
def test_ops_status_live():
    with httpx.Client(base_url=BACKEND, timeout=15.0) as c:
        r = c.get("/api/v1/_internal/ops-status")
        assert r.status_code == 200, f"status={r.status_code} body={r.text[:300]}"
        body = r.json()

        # 1) 최상위 스키마
        required = {
            "status", "generated_at", "thresholds",
            "data_quality", "regression", "voc",
            "grounding_last", "violations",
        }
        assert required <= set(body.keys()), (
            f"missing keys: {required - set(body.keys())}"
        )

        # 2) status enum
        assert body["status"] in {"ok", "warning", "critical"}, body["status"]

        # 3) thresholds 5종
        thr_required = {
            "voc_daily_drop_pct", "sentiment_null_rate",
            "topic_drop_pct", "grounding_min", "regression_ok_min",
        }
        assert thr_required <= set(body["thresholds"].keys())

        # 4) violations list 구조
        assert isinstance(body["violations"], list)
        for v in body["violations"]:
            assert "metric" in v and "severity" in v
            assert v["severity"] in {"info", "warning", "critical"}
            assert "value" in v and "threshold" in v and "reason" in v

        # 5) voc.days list
        voc = body.get("voc") or {}
        assert "days" in voc and isinstance(voc["days"], list)

        # 6) regression 키 존재 (error 인 경우는 키만 'error')
        reg = body.get("regression") or {}
        assert isinstance(reg, dict)
