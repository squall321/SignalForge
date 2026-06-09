"""
HN 다양화 라운드 2 검증 (story + comment 검색 분리 + numericFilters)

목표:
- tags=story / tags=comment 두 종류 검색이 각각 호출되는지
- numericFilters 시간 윈도우가 params 에 들어가는지
- objectID 중복이 검색어 간 dedup 되는지
- 댓글 검색 결과가 별도 RawVOC 로 변환되는지

외부 호출 없이 _make_httpx_client 만 monkeypatch.

실행:
  cd crawler && python -m pytest tests/test_hackernews_diversify.py -v
"""
import asyncio
import os
import sys
from typing import Any, Dict, List

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


class _RoutingFakeClient:
    """tags 파라미터에 따라 story/comment 응답을 다르게 돌려준다."""

    def __init__(self):
        self.story_calls: List[dict] = []
        self.comment_calls: List[dict] = []
        self.item_calls: List[str] = []
        # 검색어별로 다른 objectID 를 반환해 dedup 동작 확인
        self._story_counter = 0
        self._comment_counter = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None, **kwargs):
        if url.startswith(ALGOLIA_SEARCH):
            tags = (params or {}).get("tags")
            if tags == "story":
                self.story_calls.append(dict(params or {}))
                # 검색어마다 새 objectID 2건 + 공통 objectID 1건 (중복 검증)
                self._story_counter += 1
                hits = [
                    {
                        "objectID": f"S{self._story_counter}_a",
                        "title": f"Galaxy story {self._story_counter} A",
                        "story_text": "",
                        "url": f"https://example.com/{self._story_counter}a",
                        "author": "alice",
                        "created_at_i": 1_750_000_000 + self._story_counter,
                        "points": 10 + self._story_counter,
                        "num_comments": 3,
                    },
                    {
                        "objectID": f"S{self._story_counter}_b",
                        "title": f"Galaxy story {self._story_counter} B",
                        "story_text": "",
                        "url": f"https://example.com/{self._story_counter}b",
                        "author": "bob",
                        "created_at_i": 1_750_000_500 + self._story_counter,
                        "points": 5,
                        "num_comments": 1,
                    },
                    # 공통 글 — 모든 검색어에서 동일 ID 반환 → dedup 후 1개만 남아야 함
                    {
                        "objectID": "S_SHARED",
                        "title": "Shared Galaxy story",
                        "story_text": "",
                        "url": "https://example.com/shared",
                        "author": "carol",
                        "created_at_i": 1_749_000_000,
                        "points": 99,
                        "num_comments": 50,
                    },
                ]
                return _FakeResponse({"hits": hits})
            if tags == "comment":
                self.comment_calls.append(dict(params or {}))
                self._comment_counter += 1
                hits = [
                    {
                        "objectID": f"C{self._comment_counter}_a",
                        "comment_text": f"<p>comment {self._comment_counter} A about Galaxy</p>",
                        "author": "dave",
                        "created_at_i": 1_750_100_000 + self._comment_counter,
                        "story_id": "999",
                    },
                    {
                        "objectID": f"C{self._comment_counter}_b",
                        "comment_text": "Plain text comment",
                        "author": "eve",
                        "created_at_i": 1_750_100_500 + self._comment_counter,
                        "story_id": "999",
                    },
                ]
                return _FakeResponse({"hits": hits})
            return _FakeResponse({"hits": []})

        if url.startswith(ALGOLIA_ITEM):
            self.item_calls.append(url)
            # 트리 보강 — 자식 댓글 1개 (검색에서 안 나온 ID)
            return _FakeResponse({
                "id": 1,
                "children": [
                    {
                        "id": 90001,
                        "type": "comment",
                        "author": "tree_author",
                        "text": "<p>tree-only comment</p>",
                        "created_at_i": 1_750_200_000,
                        "children": [],
                    }
                ],
            })

        return _FakeResponse({}, status=404)


def _install_fake(crawler: HackerNewsCrawler) -> _RoutingFakeClient:
    fake = _RoutingFakeClient()
    crawler._make_httpx_client = lambda: fake  # type: ignore[assignment]

    async def _no_delay():
        return None

    crawler._random_delay = _no_delay  # type: ignore[assignment]
    return fake


def test_story_and_comment_searches_are_separate():
    crawler = HackerNewsCrawler("hackernews")
    fake = _install_fake(crawler)

    raw = asyncio.run(crawler.crawl())

    # 1) story / comment 호출이 모두 검색어 수만큼 발생
    assert len(fake.story_calls) == len(QUERY_TERMS), (
        f"story 검색 호출 {len(QUERY_TERMS)} 기대, 실제 {len(fake.story_calls)}"
    )
    assert len(fake.comment_calls) == len(QUERY_TERMS), (
        f"comment 검색 호출 {len(QUERY_TERMS)} 기대, 실제 {len(fake.comment_calls)}"
    )

    # 2) numericFilters 파라미터가 항상 들어가야 함
    for p in fake.story_calls:
        assert "numericFilters" in p, "story 호출에 numericFilters 누락"
        assert p["numericFilters"].startswith("created_at_i>"), p["numericFilters"]
        assert p["tags"] == "story"
        assert p["hitsPerPage"] == 50
    for p in fake.comment_calls:
        assert "numericFilters" in p, "comment 호출에 numericFilters 누락"
        assert p["tags"] == "comment"
        assert p["hitsPerPage"] == 100

    # 3) RawVOC 분리: kind=story / kind=comment
    stories = [r for r in raw if r.meta.get("kind") == "story"]
    comments = [r for r in raw if r.meta.get("kind") == "comment"]

    # 검색어 N 개 × 고유 story 2 + 공통 1 = 2N + 1
    expected_stories = 2 * len(QUERY_TERMS) + 1
    assert len(stories) == expected_stories, (
        f"고유 story {expected_stories} 기대, 실제 {len(stories)}"
    )

    # 검색어 N 개 × 댓글 2 = 2N (모두 고유 ID)
    expected_comments_min = 2 * len(QUERY_TERMS)
    assert len(comments) >= expected_comments_min, (
        f"comment 최소 {expected_comments_min} 기대, 실제 {len(comments)}"
    )

    # 4) 공통 story 가 단 1번만 등장 (dedup)
    shared = [s for s in stories if s.meta.get("hn_id") == "S_SHARED"]
    assert len(shared) == 1, f"S_SHARED dedup 실패: {len(shared)}"

    # 5) 댓글 검색 결과는 parent_story 메타 보유
    for c in comments:
        if c.meta.get("hn_id", "").startswith("C"):
            assert c.meta.get("parent_story") == "999"

    # 6) 트리 보강 호출이 상위 스토리 수만큼 발생
    assert len(fake.item_calls) > 0, "item 트리 보강 호출이 없음"
    tree_comments = [c for c in comments if c.meta.get("hn_id") == "90001"]
    assert len(tree_comments) >= 1, "트리 전용 댓글이 결과에 없음"

    print(
        f"  [PASS] stories={len(stories)} comments={len(comments)} "
        f"story_calls={len(fake.story_calls)} comment_calls={len(fake.comment_calls)} "
        f"item_calls={len(fake.item_calls)}"
    )


if __name__ == "__main__":
    test_story_and_comment_searches_are_separate()
    print("\nAll tests passed.")
