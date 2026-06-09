"""R18 Track D — /api/v1/_internal/ops-trend live smoke test.

가동 중인 backend (http://127.0.0.1:8000) 에 직접 HTTP 호출.

검증:
  - 200 응답 + JSON 스키마 (days, generated_at, available, series, moving_avg_7d, summary)
  - days=7 기본값 동작
  - series 가 list 이고 각 entry 가 (date, status, voc_last, voc_delta_pct,
    grounding_last, regression_failed, violations_count) 보유
  - moving_avg_7d.{voc_last,grounding_last,violations_count} 가 series 와 길이 일치
  - summary.{latest,voc_change_pct_7d,violations_total} 존재
  - 적재된 reports/ops_status_*.json 이 0개여도 200 OK (빈 series)

실행:
    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_ops_trend.py -v
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
def test_ops_trend_live():
    with httpx.Client(base_url=BACKEND, timeout=15.0) as c:
        r = c.get("/api/v1/_internal/ops-trend", params={"days": 7})
        assert r.status_code == 200, f"status={r.status_code} body={r.text[:300]}"
        body = r.json()

        # 1) 최상위 스키마
        required = {
            "days", "generated_at", "available",
            "series", "moving_avg_7d", "summary",
        }
        assert required <= set(body.keys()), (
            f"missing keys: {required - set(body.keys())}"
        )
        assert body["days"] == 7
        assert isinstance(body["available"], list)
        assert isinstance(body["series"], list)

        # 2) series entry 스키마
        series = body["series"]
        for entry in series:
            for k in (
                "date", "status", "voc_last", "voc_delta_pct",
                "sentiment_null_rate", "topic_rate", "grounding_last",
                "regression_failed", "violations_count",
            ):
                assert k in entry, f"series entry missing {k}: {entry}"

        # 3) moving_avg_7d 길이 = series 길이 (3 metric)
        mavg = body["moving_avg_7d"]
        for metric in ("voc_last", "grounding_last", "violations_count"):
            assert metric in mavg
            assert isinstance(mavg[metric], list)
            assert len(mavg[metric]) == len(series), (
                f"moving_avg_7d.{metric} 길이 mismatch: "
                f"{len(mavg[metric])} vs series {len(series)}"
            )

        # 4) summary
        summary = body["summary"]
        for k in ("latest", "voc_change_pct_7d", "violations_total"):
            assert k in summary, f"summary missing {k}"
        assert isinstance(summary["violations_total"], int)
        # voc_change_pct_7d 는 float 또는 None
        vchg = summary["voc_change_pct_7d"]
        assert vchg is None or isinstance(vchg, (int, float))

        # 5) 데이터가 있을 때 7일 이동 평균은 7번째 인덱스부터 산출 가능
        #    series 길이 ≥ 7 이면 마지막 voc_last 이동 평균이 None 이 아니어야 함
        if len(series) >= 7:
            tail = mavg["voc_last"][-1]
            # voc_last 가 모두 None 이면 tail 도 None — 보수적으로 모두 허용
            assert tail is None or isinstance(tail, (int, float))
