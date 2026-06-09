"""Reddit OAuth collector 단위 테스트.

세 케이스를 검증한다:
  1) 키 미설정 → crawl() 0건 + warning 로그
  2) 키 + mock 토큰 → mock subreddit listing → RawVOC 변환
  3) 401 응답 시 토큰 자동 갱신 후 재시도 성공
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms import reddit as reddit_mod
from platforms.reddit import RedditCrawler, _has_reddit_keys, _reset_token_cache


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _mk_listing_payload(subreddit: str) -> Dict[str, Any]:
    """가짜 /r/<sub>/new listing 응답."""
    return {
        "data": {
            "children": [
                {
                    "kind": "t3",
                    "data": {
                        "permalink": f"/r/{subreddit}/comments/abc123/test_post/",
                        "title": "S25 Ultra battery drain bug",
                        "selftext": "Anyone else seeing rapid drain after the June update?",
                        "ups": 42,
                        "num_comments": 5,
                        "created_utc": 1717372800.0,
                        "author": "tester",
                    },
                },
                # kind != t3 → 무시
                {"kind": "more", "data": {}},
            ]
        }
    }


def _mk_token_response(token: str = "fake-token", expires_in: int = 3600) -> Dict[str, Any]:
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in}


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            req = httpx.Request("GET", "https://oauth.reddit.com/")
            raise httpx.HTTPStatusError(
                f"status {self.status_code}", request=req,
                response=httpx.Response(self.status_code),
            )

    def json(self) -> Any:
        return self._payload


# --------------------------------------------------------------------------
# 1) 키 미설정 → 빈 결과 + warning
# --------------------------------------------------------------------------

def test_no_keys_returns_empty(monkeypatch, caplog):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    _reset_token_cache()

    assert _has_reddit_keys() is False

    crawler = RedditCrawler()
    with caplog.at_level("WARNING"):
        result = asyncio.run(crawler.crawl())

    assert result == []
    # 경고 메시지에 키 설정 안내 포함
    msgs = " ".join(rec.message for rec in caplog.records)
    assert "Reddit OAuth 키 미설정" in msgs


# --------------------------------------------------------------------------
# 2) 키 + mock 토큰 → listing → RawVOC
# --------------------------------------------------------------------------

def test_keys_with_mock_token_collects(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "csec")
    monkeypatch.setenv("REDDIT_USER_AGENT", "SignalForge/1.0 test")
    _reset_token_cache()

    # 단일 서브레딧만 사용하도록 패치 (테스트 단순화)
    monkeypatch.setattr(reddit_mod, "SUBREDDITS", ["samsung"])
    monkeypatch.setattr(reddit_mod, "MAX_POSTS", 1)

    async def _no_delay(self):
        return None

    monkeypatch.setattr(RedditCrawler, "_random_delay", _no_delay)

    listing_payload = _mk_listing_payload("samsung")
    # 댓글 응답: [post-listing, comment-listing]
    comments_payload = [
        {},  # 첫 요소는 post 자신
        {
            "data": {
                "children": [
                    {
                        "kind": "t1",
                        "data": {
                            "id": "cmt1",
                            "body": "Same here, GS25U dies in 4 hours",
                            "ups": 7,
                            "created_utc": 1717373100.0,
                            "author": "another",
                        },
                    },
                    {"kind": "more", "data": {}},  # 무시
                    {
                        "kind": "t1",
                        "data": {
                            "id": "cmt2",
                            "body": "[deleted]",  # 제외
                            "ups": 0,
                            "created_utc": 1717373200.0,
                            "author": "[deleted]",
                        },
                    },
                ]
            }
        },
    ]

    async def fake_get(url, headers=None, params=None, timeout=None):
        if "/r/samsung/new" in url:
            return _FakeResponse(200, listing_payload)
        if "/comments/abc123/" in url or "/r/samsung/comments/" in url:
            return _FakeResponse(200, comments_payload)
        return _FakeResponse(404, {})

    async def fake_post(url, headers=None, data=None, timeout=None):
        assert url == reddit_mod.TOKEN_URL
        # Basic auth 헤더 확인
        assert headers["Authorization"].startswith("Basic ")
        return _FakeResponse(200, _mk_token_response("tok-A"))

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=fake_get)
    fake_client.post = AsyncMock(side_effect=fake_post)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    crawler = RedditCrawler()
    monkeypatch.setattr(
        crawler, "_make_httpx_client", lambda: fake_client
    )

    result = asyncio.run(crawler.crawl())

    # 포스트 1 + 유효 댓글 1 = 2
    assert len(result) == 2
    post = result[0]
    assert post.source_url.startswith("https://www.reddit.com/r/samsung/")
    assert "S25 Ultra battery drain" in post.content
    assert post.likes_count == 42
    assert post.country_code == "US"
    assert post.meta["subreddit"] == "samsung"

    comment = result[1]
    assert "Same here" in comment.content
    assert comment.likes_count == 7
    assert comment.meta["parent_post"] == post.source_url


# --------------------------------------------------------------------------
# 3) 401 → 토큰 재발급 → 재시도 성공
# --------------------------------------------------------------------------

def test_token_refresh_on_401(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "csec")
    _reset_token_cache()

    listing_payload = _mk_listing_payload("samsung")

    call_log: List[Tuple[str, str]] = []  # (url, auth_header)
    token_counter = {"n": 0}

    async def fake_get(url, headers=None, params=None, timeout=None):
        call_log.append((url, headers.get("Authorization", "")))
        # 첫 GET 은 401, 두 번째는 200.
        if len([c for c in call_log if c[0] == url]) == 1:
            return _FakeResponse(401, {})
        return _FakeResponse(200, listing_payload)

    async def fake_post(url, headers=None, data=None, timeout=None):
        token_counter["n"] += 1
        return _FakeResponse(200, _mk_token_response(f"tok-{token_counter['n']}"))

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=fake_get)
    fake_client.post = AsyncMock(side_effect=fake_post)

    async def _run():
        result = await RedditCrawler()._fetch_subreddit_new(fake_client, "samsung")
        return result

    posts = asyncio.run(_run())

    # 401 후 재시도로 1건 수집 성공
    assert len(posts) == 1
    assert posts[0].meta["subreddit"] == "samsung"
    # 토큰 발급은 2번 (초기 + 401 후 재발급)
    assert token_counter["n"] == 2
    # 첫 GET 과 두 번째 GET 의 토큰이 다르다
    auths = [c[1] for c in call_log]
    assert auths[0] != auths[1]
