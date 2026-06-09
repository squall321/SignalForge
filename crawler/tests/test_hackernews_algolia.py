"""
Hacker News (Algolia HN Search API) 크롤러 단위 테스트.

외부 네트워크 호출 없이 httpx.AsyncClient 를 monkeypatch 해
RawVOC 변환 / 빈 응답 처리만 검증한다.

실행:
  cd crawler && python -m pytest tests/test_hackernews_algolia.py -v
"""
import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms.hackernews import (
    HackerNewsCrawler,
    ALGOLIA_SEARCH,
    ALGOLIA_ITEM,
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


class _FakeClient:
    """search_by_date 요청은 가짜 hits 1건씩, items 요청은 가짜 댓글 트리 1건 반환."""

    def __init__(self, search_payload: Dict[str, Any], item_payload: Dict[str, Any]):
        self._search = search_payload
        self._item = item_payload
        self.calls: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None, **kwargs):
        self.calls.append((url, params))
        if url.startswith(ALGOLIA_SEARCH):
            return _FakeResponse(self._search)
        if url.startswith(ALGOLIA_ITEM):
            return _FakeResponse(self._item)
        return _FakeResponse({}, status=404)


def _install_fake(crawler: HackerNewsCrawler, search_payload, item_payload) -> _FakeClient:
    fake = _FakeClient(search_payload, item_payload)
    crawler._make_httpx_client = lambda: fake  # type: ignore[assignment]

    async def _no_delay():
        return None

    crawler._random_delay = _no_delay  # type: ignore[assignment]
    return fake


# ------------------------------------------------------------
# Test 1: 정상 응답 → RawVOC 변환 (스토리 + 댓글)
# ------------------------------------------------------------
def test_search_and_item_to_rawvoc():
    crawler = HackerNewsCrawler("hackernews")

    search_payload = {
        "hits": [
            {
                "objectID": "987654",
                "title": "Galaxy S25 Ultra review",
                "story_text": "<p>Camera is great.</p>",
                "url": "https://example.com/s25",
                "author": "alice",
                "created_at_i": 1_717_000_000,
                "points": 42,
                "num_comments": 7,
            }
        ]
    }

    item_payload = {
        "id": 987654,
        "children": [
            {
                "id": 111,
                "type": "comment",
                "author": "bob",
                "text": "<p>Battery life impressive</p>",
                "created_at_i": 1_717_000_500,
                "children": [
                    {
                        "id": 222,
                        "type": "comment",
                        "author": "carol",
                        "text": "Agreed, big jump from S24.",
                        "created_at_i": 1_717_000_900,
                        "children": [],
                    }
                ],
            }
        ],
    }

    _install_fake(crawler, search_payload, item_payload)

    raw = asyncio.run(crawler.crawl())

    # 검색어 수만큼 동일 hit 가 들어오지만 objectID dedup → 스토리 1건
    stories = [r for r in raw if r.meta.get("parent_story") is None]
    comments = [r for r in raw if r.meta.get("parent_story") == "987654"]

    assert len(stories) == 1, f"스토리 1건 기대, 실제 {len(stories)}"
    s = stories[0]
    assert "Galaxy S25 Ultra review" in s.content
    assert "Camera is great." in s.content, "story_text HTML 태그 제거 후 합성 필요"
    assert s.source_url == "https://example.com/s25"
    assert s.author_name == "alice"
    assert s.likes_count == 42
    assert s.comments_count == 7
    assert s.published_at == datetime.fromtimestamp(1_717_000_000, tz=timezone.utc)
    assert s.meta["hn_id"] == "987654"

    assert len(comments) == 2, f"댓글 2건 기대 (재귀 평탄화), 실제 {len(comments)}"
    bodies = {c.content for c in comments}
    assert any("Battery life impressive" in b for b in bodies)
    assert any("Agreed" in b for b in bodies)
    for c in comments:
        assert c.meta["parent_story"] == "987654"

    print(f"  [PASS] stories={len(stories)}, comments={len(comments)}")


# ------------------------------------------------------------
# Test 2: 빈 검색 결과 → 빈 리스트, 예외 없음
# ------------------------------------------------------------
def test_empty_search_returns_empty_list():
    crawler = HackerNewsCrawler("hackernews")
    _install_fake(crawler, search_payload={"hits": []}, item_payload={"children": []})

    raw = asyncio.run(crawler.crawl())
    assert raw == [], f"빈 응답 → 빈 리스트 기대, 실제 {len(raw)}건"
    print(f"  [PASS] empty: {len(raw)}건")


if __name__ == "__main__":
    test_search_and_item_to_rawvoc()
    test_empty_search_returns_empty_list()
    print("\nAll tests passed.")
