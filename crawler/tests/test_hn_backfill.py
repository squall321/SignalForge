"""HN Algolia 전기간 backfill 스크립트 단위 테스트.

검증:
- numericFilters 가 *없는* 호출만 발생 (전기간 의도)
- page=0..MAX_PAGES-1 까지 pagination 정상 동작
- nbPages 가 작으면 조기 종료
- objectID 중복 dedup
- story / comment 검색어별 호출
- RawVOC 변환 (published_at, kind 메타)

외부 네트워크 호출 없이 client_factory 를 가짜 클라이언트로 주입.

실행:
  cd crawler && python -m pytest tests/test_hn_backfill.py -v
"""
import asyncio
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.hn_backfill_alltime import (  # noqa: E402
    ALGOLIA_SEARCH,
    collect_all_hits,
)
from scripts import hn_backfill_alltime as mod  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any]):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _PaginatedFakeClient:
    """검색어 'q' + page 별 가짜 hits 반환.

    page 0 → STORY/COMMENT 각각 2 hit (objectID = q-page-i)
    page 1 → 각각 2 hit (objectID = q-page-i)
    page 2 → 빈 hits → 조기 종료 분기 검증
    """

    def __init__(self):
        self.calls: List[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None, **kwargs):
        params = dict(params or {})
        self.calls.append(params)
        assert url == ALGOLIA_SEARCH
        # numericFilters 가 들어오면 안 됨 (전기간 의도)
        assert "numericFilters" not in params, (
            f"전기간 backfill 인데 numericFilters 가 들어옴: {params}"
        )

        page = int(params.get("page", 0))
        q = params["query"]
        tags = params["tags"]
        if page >= 2:
            return _FakeResponse({"hits": [], "nbPages": 2})

        if tags == "story":
            hits = [
                {
                    "objectID": f"S-{q}-{page}-{i}",
                    "title": f"{q} story {page}-{i}",
                    "story_text": "",
                    "url": f"https://example.com/{q}/{page}/{i}",
                    "author": "alice",
                    "created_at_i": 1_400_000_000 + page * 100 + i,
                    "points": 5,
                    "num_comments": 1,
                }
                for i in range(2)
            ]
        else:
            hits = [
                {
                    "objectID": f"C-{q}-{page}-{i}",
                    "comment_text": f"{q} comment {page}-{i}",
                    "author": "bob",
                    "created_at_i": 1_400_500_000 + page * 100 + i,
                    "story_id": "777",
                }
                for i in range(2)
            ]
        return _FakeResponse({"hits": hits, "nbPages": 2})


def _run(coro):
    return asyncio.run(coro)


def test_backfill_pagination_and_no_numeric_filter(monkeypatch):
    # sleep 무효화 (테스트 속도)
    monkeypatch.setattr(mod, "BETWEEN_REQUEST_SLEEP", 0.0)

    fake = _PaginatedFakeClient()
    terms = ["Galaxy S5", "Galaxy Note 7"]

    raw_vocs, stats = _run(
        collect_all_hits(
            terms=terms,
            max_pages=3,            # 3 까지 시도하되 page 2 에서 빈 hits → 조기 종료
            hits_per_page=1000,
            client_factory=lambda: fake,
        )
    )

    # 호출 구조:
    #   검색어 2개 × (story + comment) × page (0, 1)
    # nbPages=2 (= 총 페이지 수 2) 를 응답에서 보고 page=1 호출 직후 조기 종료.
    expected_calls = 2 * 2 * 2
    assert len(fake.calls) == expected_calls, (
        f"예상 호출 {expected_calls}, 실제 {len(fake.calls)}: {fake.calls}"
    )

    # 모든 호출에 numericFilters 없음 (assert 안에서 검증)
    # page 0, 1 만 실제 hits 반환 → story 2*2*2=8, comment 2*2*2=8
    assert stats["story_hits"] == 8, stats
    assert stats["comment_hits"] == 8, stats
    assert stats["story_voc"] == 8, stats
    assert stats["comment_voc"] == 8, stats

    # RawVOC 내용 검증
    stories = [v for v in raw_vocs if v.meta.get("kind") == "story"]
    comments = [v for v in raw_vocs if v.meta.get("kind") == "comment"]
    assert len(stories) == 8
    assert len(comments) == 8

    # published_at 채워짐
    assert all(v.published_at is not None for v in raw_vocs)

    # objectID 별 RawVOC.external_id 고유
    ext_ids = [v.external_id for v in raw_vocs]
    assert len(set(ext_ids)) == len(ext_ids), "external_id 중복 발생"

    # 검색어가 모두 호출에 등장
    queries_called = {c["query"] for c in fake.calls}
    assert queries_called == set(terms)


def test_dedup_across_terms(monkeypatch):
    """두 검색어가 같은 objectID 를 돌려주면 RawVOC 는 1번만 만들어져야 함."""
    monkeypatch.setattr(mod, "BETWEEN_REQUEST_SLEEP", 0.0)

    class _DupFakeClient:
        def __init__(self):
            self.calls: List[dict] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, **kwargs):
            params = dict(params or {})
            self.calls.append(params)
            if int(params.get("page", 0)) > 0:
                return _FakeResponse({"hits": [], "nbPages": 1})
            tags = params["tags"]
            if tags == "story":
                return _FakeResponse({
                    "hits": [{
                        "objectID": "SHARED_S",
                        "title": "shared story",
                        "story_text": "",
                        "url": "https://example.com/x",
                        "author": "x",
                        "created_at_i": 1_400_000_000,
                        "points": 1,
                        "num_comments": 0,
                    }],
                    "nbPages": 1,
                })
            return _FakeResponse({
                "hits": [{
                    "objectID": "SHARED_C",
                    "comment_text": "shared comment",
                    "author": "y",
                    "created_at_i": 1_400_000_500,
                    "story_id": "1",
                }],
                "nbPages": 1,
            })

    fake = _DupFakeClient()
    raw_vocs, stats = _run(
        collect_all_hits(
            terms=["term-A", "term-B"],
            max_pages=2,
            hits_per_page=1000,
            client_factory=lambda: fake,
        )
    )
    # 2 검색어 × story+comment 각 1 hit = 4 hit 총합
    assert stats["story_hits"] == 2
    assert stats["comment_hits"] == 2
    # dedup 후 RawVOC: story 1 + comment 1 = 2
    assert stats["story_voc"] == 1
    assert stats["comment_voc"] == 1
    assert len(raw_vocs) == 2


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
        test_backfill_pagination_and_no_numeric_filter(mp)
        mp.undo()
        mp = _MP()
        test_dedup_across_terms(mp)
    finally:
        mp.undo()
    print("\nAll tests passed.")
