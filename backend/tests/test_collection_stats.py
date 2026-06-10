"""Track M7 — /api/v1/_internal/collection-stats live smoke test.

가동 중인 backend (http://127.0.0.1:8000) 에 직접 HTTP 호출.
test_collection_status.py 와 동일한 live-server 패턴.

검증:
  - 200 응답 + JSON 스키마
  - 최상위 키: generated_at, h24_total, h7d_total, mx_match_h24,
              mx_match_h24_pct, h24_by_site, h7d_by_site
  - h24_total / h7d_total int >= 0
  - h7d_total >= h24_total (7일이 24h를 포함)
  - mx_match_h24 <= h24_total
  - by_site 각 row: code/region/h24_new (또는 h7d_new) 보유
  - h24_by_site 의 h24_new 합 == h24_total (정합성)

실행:
    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_collection_stats.py -v
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
def test_collection_stats_live():
    with httpx.Client(base_url=BACKEND, timeout=20.0) as c:
        r = c.get("/api/v1/_internal/collection-stats")
        assert r.status_code == 200, f"status={r.status_code} body={r.text}"
        body = r.json()

        # 1) 최상위 스키마
        required_top = {
            "generated_at", "h24_total", "h7d_total",
            "mx_match_h24", "mx_match_h24_pct",
            "h24_by_site", "h7d_by_site",
        }
        assert required_top <= set(body.keys()), body.keys()

        # 2) 카운터 타입/범위
        h24 = body["h24_total"]
        h7d = body["h7d_total"]
        mx24 = body["mx_match_h24"]
        assert isinstance(h24, int) and h24 >= 0
        assert isinstance(h7d, int) and h7d >= 0
        assert isinstance(mx24, int) and mx24 >= 0

        # 3) 논리 관계
        assert h7d >= h24, f"7d({h7d}) < 24h({h24}) — 시간 윈도우 역전"
        assert mx24 <= h24, f"mx_match_h24({mx24}) > h24_total({h24})"

        # 4) mx_match_h24_pct 범위
        pct = body["mx_match_h24_pct"]
        assert isinstance(pct, (int, float))
        assert 0 <= pct <= 100

        # 5) by_site 스키마 + 합 정합성
        h24_by = body["h24_by_site"]
        assert isinstance(h24_by, list)
        h24_sum = 0
        for r in h24_by:
            assert {"code", "region", "h24_new"} <= set(r.keys()), r
            assert isinstance(r["code"], str) and r["code"]
            assert isinstance(r["h24_new"], int) and r["h24_new"] > 0
            h24_sum += r["h24_new"]
        assert h24_sum == h24, f"sum(by_site h24_new)={h24_sum} != h24_total={h24}"

        h7d_by = body["h7d_by_site"]
        assert isinstance(h7d_by, list)
        h7d_sum = 0
        for r in h7d_by:
            assert {"code", "region", "h7d_new"} <= set(r.keys()), r
            assert isinstance(r["code"], str) and r["code"]
            assert isinstance(r["h7d_new"], int) and r["h7d_new"] > 0
            h7d_sum += r["h7d_new"]
        assert h7d_sum == h7d, f"sum(by_site h7d_new)={h7d_sum} != h7d_total={h7d}"
