"""
HN R6 — 검색어 80+ 확장 + 옛 모델 / 위기 키워드 검증.

목표:
- QUERY_TERMS 가 최소 80 개 이상.
- 옛 모델 (Note 7, S10 5G, Galaxy Fold) 키워드 포함.
- 위기 키워드 (Samsung recall, Galaxy fire, GoS) 포함.
- 비교 키워드 (Galaxy vs iPhone) 포함.
- 'Galaxy Note 7' 검색 시 일반 검색기에 정확한 query 가 전달되어
  옛 모델 매칭 경로가 동작함.

외부 호출 없이 monkeypatch.

실행:
  cd crawler && python -m pytest tests/test_hackernews_search_v3.py -v
"""
import asyncio
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms import hackernews as hn_mod
from platforms.hackernews import (
    ALGOLIA_SEARCH,
    ALGOLIA_ITEM,
    HackerNewsCrawler,
    QUERY_TERMS,
)


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any], status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Dict[str, Any]:
        return self._payload


class _QueryRecordingClient:
    """모든 query 파라미터를 기록, 가짜 hit 1건씩 반환."""

    def __init__(self):
        self.queries: List[str] = []
        self._i = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None, **kwargs):
        if url.startswith(ALGOLIA_SEARCH):
            q = (params or {}).get("query")
            tags = (params or {}).get("tags")
            if q:
                self.queries.append(q)
            self._i += 1
            if tags == "story":
                return _FakeResponse({"hits": [{
                    "objectID": f"S{self._i}",
                    "title": f"Story {q}",
                    "story_text": "",
                    "url": f"https://example.com/{self._i}",
                    "author": "alice",
                    "created_at_i": 1_750_000_000 + self._i,
                    "points": 5,
                    "num_comments": 1,
                }]})
            if tags == "comment":
                return _FakeResponse({"hits": [{
                    "objectID": f"C{self._i}",
                    "comment_text": f"comment about {q}",
                    "author": "bob",
                    "created_at_i": 1_750_100_000 + self._i,
                    "story_id": "999",
                }]})
            return _FakeResponse({"hits": []})

        if url.startswith(ALGOLIA_ITEM):
            return _FakeResponse({"id": 1, "children": []})

        return _FakeResponse({}, status=404)


def test_query_terms_v3_expanded():
    """검색어 80+ + 옛 모델/위기/비교 키워드 매칭."""
    # 1) 80 개 이상
    assert len(QUERY_TERMS) >= 80, (
        f"QUERY_TERMS 80개 이상 기대, 실제 {len(QUERY_TERMS)}"
    )

    # 2) 옛 모델 키워드
    legacy_must = [
        "Galaxy Note 7",
        "Note 7 explosion",
        "Note 7 recall",
        "Galaxy Fold",
        "Galaxy S21",
        "Galaxy S20",
        "Galaxy S10",
        "Galaxy S10 5G",
        "Galaxy S9",
        "Galaxy S8",
        "Galaxy Note 10",
        "Galaxy Note 20",
        "Galaxy Fold 3",
        "Galaxy Z Flip 3",
        "Galaxy Watch Active",
        "Galaxy Buds Live",
    ]
    missing_legacy = [k for k in legacy_must if k not in QUERY_TERMS]
    assert not missing_legacy, f"옛 모델 누락: {missing_legacy}"

    # 3) 위기 / 이슈 키워드
    crisis_must = [
        "Samsung recall",
        "Samsung defect",
        "Samsung lawsuit",
        "Galaxy fire",
        "Galaxy hinge",
        "Samsung GoS",
    ]
    missing_crisis = [k for k in crisis_must if k not in QUERY_TERMS]
    assert not missing_crisis, f"위기 키워드 누락: {missing_crisis}"

    # 4) 비교 키워드
    compare_must = ["Galaxy vs iPhone", "Samsung vs Apple"]
    missing_compare = [k for k in compare_must if k not in QUERY_TERMS]
    assert not missing_compare, f"비교 키워드 누락: {missing_compare}"

    # 5) crawl() 시 'Galaxy Note 7' 가 실제 query 로 전송되는지 확인
    crawler = HackerNewsCrawler("hackernews")
    fake = _QueryRecordingClient()
    crawler._make_httpx_client = lambda: fake  # type: ignore[assignment]

    async def _no_delay():
        return None

    crawler._random_delay = _no_delay  # type: ignore[assignment]

    asyncio.run(crawler.crawl())

    # story + comment 각각 호출되므로 동일 query 가 2회 등장
    assert "Galaxy Note 7" in fake.queries, (
        f"'Galaxy Note 7' query 누락. 실제 일부: {fake.queries[:5]}"
    )
    assert "Galaxy S10 5G" in fake.queries
    assert "Samsung recall" in fake.queries

    print(
        f"  [PASS] terms={len(QUERY_TERMS)} legacy_ok={len(legacy_must)} "
        f"crisis_ok={len(crisis_must)} unique_queries={len(set(fake.queries))}"
    )


if __name__ == "__main__":
    test_query_terms_v3_expanded()
    print("\nAll tests passed.")
