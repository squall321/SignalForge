"""
HN 라운드 3 — 검색어 50+ 확장 검증

목표:
- QUERY_TERMS 가 최소 50 개 이상이고 4 그룹 키워드가 모두 들어 있어야 한다.
- QUERY_SAMPLE_SIZE=None (기본) 일 때 _select_terms() 가 전체를 반환한다.
- QUERY_SAMPLE_SIZE=N (N<50) 일 때 _select_terms() 가 정확히 N 개 + QUERY_TERMS 의 부분집합을 반환한다.
- 샘플링 모드에서 crawl() 이 N 회씩만 호출(story / comment 각각) 하고
  RawVOC dedup 가 정상 동작한다.

외부 호출 없이 _make_httpx_client / _random_delay 만 monkeypatch.

실행:
  cd crawler && python -m pytest tests/test_hackernews_50terms.py -v
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


class _CountingFakeClient:
    """검색어별 story / comment 호출 횟수를 추적, 가짜 hits 1건씩 반환."""

    def __init__(self):
        self.story_calls: List[dict] = []
        self.comment_calls: List[dict] = []
        self.item_calls: List[str] = []
        self._sc = 0
        self._cc = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None, **kwargs):
        if url.startswith(ALGOLIA_SEARCH):
            tags = (params or {}).get("tags")
            if tags == "story":
                self.story_calls.append(dict(params or {}))
                self._sc += 1
                return _FakeResponse({"hits": [{
                    "objectID": f"S{self._sc}",
                    "title": f"Story {self._sc}",
                    "story_text": "",
                    "url": f"https://example.com/{self._sc}",
                    "author": "alice",
                    "created_at_i": 1_750_000_000 + self._sc,
                    "points": 10,
                    "num_comments": 1,
                }]})
            if tags == "comment":
                self.comment_calls.append(dict(params or {}))
                self._cc += 1
                return _FakeResponse({"hits": [{
                    "objectID": f"C{self._cc}",
                    "comment_text": f"comment {self._cc}",
                    "author": "bob",
                    "created_at_i": 1_750_100_000 + self._cc,
                    "story_id": "999",
                }]})
            return _FakeResponse({"hits": []})

        if url.startswith(ALGOLIA_ITEM):
            self.item_calls.append(url)
            return _FakeResponse({"id": 1, "children": []})

        return _FakeResponse({}, status=404)


def _install_fake(crawler: HackerNewsCrawler) -> _CountingFakeClient:
    fake = _CountingFakeClient()
    crawler._make_httpx_client = lambda: fake  # type: ignore[assignment]

    async def _no_delay():
        return None

    crawler._random_delay = _no_delay  # type: ignore[assignment]
    return fake


# ------------------------------------------------------------
# Test: 50+ 검색어 + 4 그룹 키워드 + 샘플링 + dedup
# ------------------------------------------------------------
def test_query_terms_expanded_and_sampling_works(monkeypatch):
    # 1) QUERY_TERMS 가 50 개 이상
    assert len(QUERY_TERMS) >= 50, (
        f"QUERY_TERMS 50개 이상 기대, 실제 {len(QUERY_TERMS)}"
    )

    # 2) 4 그룹 대표 키워드 포함 확인
    must_have = [
        "Galaxy S25",        # 모델
        "Galaxy A55",        # 모델 (저가)
        "Galaxy Watch",      # 액세서리
        "Galaxy Ring",       # 액세서리
        "One UI",            # SW
        "SmartThings",       # 생태계
        "samsung",           # 키워드
        "Snapdragon Galaxy", # 키워드
    ]
    missing = [k for k in must_have if k not in QUERY_TERMS]
    assert not missing, f"4 그룹 필수 키워드 누락: {missing}"

    # 3) 기본 (QUERY_SAMPLE_SIZE=None) → 전체 반환
    crawler = HackerNewsCrawler("hackernews")
    monkeypatch.setattr(hn_mod, "QUERY_SAMPLE_SIZE", None)
    terms_full = crawler._select_terms()
    assert len(terms_full) == len(QUERY_TERMS), (
        f"None 모드는 전체 반환 기대, 실제 {len(terms_full)}"
    )

    # 4) 샘플링 (N=20) → 정확히 20 개 + 부분집합
    monkeypatch.setattr(hn_mod, "QUERY_SAMPLE_SIZE", 20)
    terms_sample = crawler._select_terms()
    assert len(terms_sample) == 20, (
        f"sample_size=20 기대, 실제 {len(terms_sample)}"
    )
    assert set(terms_sample).issubset(set(QUERY_TERMS)), "샘플은 QUERY_TERMS 부분집합이어야 함"
    assert len(set(terms_sample)) == 20, "샘플 내 중복 발생 — random.sample 동작 이상"

    # 5) 샘플링 모드에서 crawl() — 호출 횟수 = N (story/comment 각각)
    fake = _install_fake(crawler)
    raw = asyncio.run(crawler.crawl())

    assert len(fake.story_calls) == 20, (
        f"story 검색 20회 기대, 실제 {len(fake.story_calls)}"
    )
    assert len(fake.comment_calls) == 20, (
        f"comment 검색 20회 기대, 실제 {len(fake.comment_calls)}"
    )

    # 6) RawVOC: story 20 + comment 20 (모두 고유 ID)
    stories = [r for r in raw if r.meta.get("kind") == "story"]
    comments = [r for r in raw if r.meta.get("kind") == "comment"]
    assert len(stories) == 20, f"story RawVOC 20 기대, 실제 {len(stories)}"
    assert len(comments) == 20, f"comment RawVOC 20 기대, 실제 {len(comments)}"

    # 7) numericFilters 가 모든 호출에 들어가 있는지 회귀 검증
    for p in fake.story_calls:
        assert p.get("numericFilters", "").startswith("created_at_i>"), p
    for p in fake.comment_calls:
        assert p.get("numericFilters", "").startswith("created_at_i>"), p

    print(
        f"  [PASS] terms_full={len(terms_full)} sample={len(terms_sample)} "
        f"story_calls={len(fake.story_calls)} comment_calls={len(fake.comment_calls)} "
        f"stories={len(stories)} comments={len(comments)}"
    )


if __name__ == "__main__":
    import types

    class _MP:
        def __init__(self):
            self._restore: List[tuple] = []

        def setattr(self, obj, name, value):
            self._restore.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, val in reversed(self._restore):
                setattr(obj, name, val)

    mp = _MP()
    try:
        test_query_terms_expanded_and_sampling_works(mp)
    finally:
        mp.undo()
    print("\nAll tests passed.")
