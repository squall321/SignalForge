"""Track E — /api/v1/_internal/coverage-status live smoke test.

가동 중인 backend (http://127.0.0.1:8000) 에 직접 HTTP 호출.
test_collection_status.py 와 동일한 live-server 패턴.

검증:
  - 200 응답 + JSON 스키마 (voc_total, linked, unmapped{}, analyzable,
    analyzable_pct, linked_pct, excluded, generated_at).
  - 정합성: linked + sum(unmapped.values()) == voc_total
  - analyzable = linked + no_model_mention + unknown
  - excluded   = noise + too_short + non_galaxy
  - 0 <= linked_pct <= analyzable_pct <= 100

실행:
    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_coverage_status.py -v
"""
import os

import httpx
import pytest


BACKEND = os.getenv("SF_BACKEND_URL", "http://127.0.0.1:8000")

_UNMAPPED_KEYS = {"no_model_mention", "noise", "too_short", "non_galaxy", "unknown"}
_TOP_KEYS = {
    "voc_total", "linked", "unmapped",
    "analyzable", "analyzable_pct", "linked_pct",
    "excluded", "generated_at",
}


def _alive() -> bool:
    try:
        return httpx.get(f"{BACKEND}/health", timeout=1.5).status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_coverage_status_live():
    with httpx.Client(base_url=BACKEND, timeout=20.0) as c:
        r = c.get("/api/v1/_internal/coverage-status")
        assert r.status_code == 200, f"status={r.status_code} body={r.text}"
        body = r.json()

        # 1) 최상위 스키마
        assert set(body.keys()) >= _TOP_KEYS, body.keys()

        # 2) unmapped 5 키 모두 보유 + int
        unmapped = body["unmapped"]
        assert set(unmapped.keys()) == _UNMAPPED_KEYS, unmapped.keys()
        for k, v in unmapped.items():
            assert isinstance(v, int) and v >= 0, (k, v)

        # 3) 정합성: linked + sum(unmapped) == voc_total
        voc_total = body["voc_total"]
        linked = body["linked"]
        unmapped_sum = sum(unmapped.values())
        assert linked + unmapped_sum == voc_total, (
            linked, unmapped_sum, voc_total,
        )

        # 4) analyzable = linked + no_model_mention + unknown
        expected_analyzable = (
            linked + unmapped["no_model_mention"] + unmapped["unknown"]
        )
        assert body["analyzable"] == expected_analyzable, (
            body["analyzable"], expected_analyzable,
        )

        # 5) excluded = noise + too_short + non_galaxy
        expected_excluded = (
            unmapped["noise"] + unmapped["too_short"] + unmapped["non_galaxy"]
        )
        assert body["excluded"] == expected_excluded, (
            body["excluded"], expected_excluded,
        )

        # 6) 비율 범위
        assert 0.0 <= body["linked_pct"] <= 100.0, body["linked_pct"]
        assert 0.0 <= body["analyzable_pct"] <= 100.0, body["analyzable_pct"]
        assert body["linked_pct"] <= body["analyzable_pct"], (
            body["linked_pct"], body["analyzable_pct"],
        )
