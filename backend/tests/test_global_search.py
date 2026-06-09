"""Track E — /api/v1/_internal/search endpoint live smoke test.

가동 중인 backend (http://127.0.0.1:8000) 에 직접 HTTP 호출.
conftest 의 engine.dispose autouse fixture 와 TestClient 가 cross-loop 충돌하므로
다른 _internal endpoint 테스트(test_noise_scan.py)와 동일한 live-server 패턴 사용.

검증 (3 케이스):
  1) 정확 매칭  — 'galaxy' → keywords[0].score == 1.0, products/keywords 둘 다 결과 있음
  2) 부분 매칭  — '배터리' → categories.code == 'battery', 한국어 keywords 다수
  3) 빈 결과    — 'zzz_no_match_xxx' → 4 도메인 모두 빈 배열, 200 응답 유지

실행::

    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_global_search.py -v
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
def test_search_exact_match_galaxy():
    """'galaxy' 검색 — 정확 매칭 keyword score=1.0 + products 결과 보유."""
    with httpx.Client(base_url=BACKEND, timeout=10.0) as c:
        r = c.get("/api/v1/_internal/search", params={"q": "galaxy", "limit": 10})
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body.keys()) >= {"q", "products", "platforms", "categories", "keywords"}
        assert body["q"] == "galaxy"
        # 4 도메인 모두 list
        for k in ("products", "platforms", "categories", "keywords"):
            assert isinstance(body[k], list), k
        # keywords[0] 가 정확 매칭이면 score == 1.0
        assert body["keywords"], "keywords 결과가 비어있음"
        assert body["keywords"][0]["score"] == 1.0
        assert body["keywords"][0]["keyword"].lower() == "galaxy"
        # products 도 ILIKE prefix/contains 로 다수
        assert body["products"], "products 결과가 비어있음"
        for p in body["products"]:
            assert set(p.keys()) >= {"code", "name_ko", "score"}, p
            assert 0.0 < p["score"] <= 1.0


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_search_partial_match_battery_ko():
    """'배터리' 검색 — voc_categories.battery + 한국어 keywords 매칭."""
    with httpx.Client(base_url=BACKEND, timeout=10.0) as c:
        r = c.get("/api/v1/_internal/search", params={"q": "배터리", "limit": 10})
        assert r.status_code == 200, r.text
        body = r.json()
        # categories 에 battery code 가 prefix/contains 로 매칭되어야 함
        cat_codes = {c["code"] for c in body["categories"]}
        assert "battery" in cat_codes, f"categories: {body['categories']}"
        # 한국어 keyword 다수
        ko_keywords = [k for k in body["keywords"] if k.get("lang") == "ko"]
        assert len(ko_keywords) >= 1, body["keywords"]
        # 정확 매칭 '배터리' 가 있다면 score 가 1.0 (정확 매칭 + 빈도 가산 clip)
        exact = [k for k in body["keywords"] if k["keyword"] == "배터리"]
        if exact:
            assert exact[0]["score"] >= 0.9


@pytest.mark.skipif(not _alive(), reason="backend 미가동")
def test_search_empty_result_no_match():
    """매칭 없는 키워드 → 200 + 4 도메인 모두 빈 배열."""
    with httpx.Client(base_url=BACKEND, timeout=10.0) as c:
        r = c.get(
            "/api/v1/_internal/search",
            params={"q": "zzz_no_match_xxx_qqq", "limit": 5},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["products"] == []
        assert body["platforms"] == []
        assert body["categories"] == []
        assert body["keywords"] == []
