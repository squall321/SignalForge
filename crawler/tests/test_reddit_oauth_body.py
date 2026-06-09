"""Reddit OAuth collector 본체(body) 단위 테스트 — 4 케이스.

기존 test_reddit_oauth.py 가 listing/comment 변환과 토큰 재발급 로직을
검증한다면, 본 파일은 트랙 B 가 요구하는 "collector 본체" 시나리오를
좀 더 강하게 검증한다:

  1) 키 무 → crawl() 0건 + WARNING 로그 ("Reddit OAuth 키 미설정")
  2) 키 유 + mock token (200) + mock subreddit (200) → RawVOC ≥ 1
  3) 토큰 만료 시 자동 갱신 (get_reddit_token 캐시 TTL 동작)
  4) 401 응답 시 graceful — crawl() 이 raise 없이 [] 반환

전제:
  - 외부 httpx 호출은 전부 AsyncMock 으로 차단.
  - SUBREDDITS / MAX_POSTS / _random_delay 는 테스트 단순화를 위해 패치.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms import reddit as reddit_mod
from platforms.reddit import (
    RedditCrawler,
    _has_reddit_keys,
    _reset_token_cache,
    get_reddit_token,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _mk_listing_payload(subreddit: str) -> Dict[str, Any]:
    """가짜 /r/<sub>/new listing 응답 (포스트 1건)."""
    return {
        "data": {
            "children": [
                {
                    "kind": "t3",
                    "data": {
                        "permalink": f"/r/{subreddit}/comments/xyz789/foldable_hinge/",
                        "title": "Fold7 hinge creak after 2 weeks",
                        "selftext": "Anyone else noticing this audible click?",
                        "ups": 11,
                        "num_comments": 3,
                        "created_utc": 1717380000.0,
                        "author": "tester2",
                    },
                }
            ]
        }
    }


def _mk_empty_comments_payload() -> List[Dict[str, Any]]:
    """가짜 comments 응답 — children 없음 (RawVOC 추가 안 함)."""
    return [{}, {"data": {"children": []}}]


def _mk_token_response(token: str, expires_in: int = 3600) -> Dict[str, Any]:
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in}


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            req = httpx.Request("GET", "https://oauth.reddit.com/")
            raise httpx.HTTPStatusError(
                f"status {self.status_code}",
                request=req,
                response=httpx.Response(self.status_code),
            )

    def json(self) -> Any:
        return self._payload


def _patch_single_subreddit(monkeypatch) -> None:
    """테스트에서 SUBREDDITS=[samsung] / MAX_POSTS=1 / delay=0 로 단순화."""
    monkeypatch.setattr(reddit_mod, "SUBREDDITS", ["samsung"])
    monkeypatch.setattr(reddit_mod, "MAX_POSTS", 1)

    async def _no_delay(self):
        return None

    monkeypatch.setattr(RedditCrawler, "_random_delay", _no_delay)


# --------------------------------------------------------------------------
# 1) 키 미설정 → crawl() 0건 + warning
# --------------------------------------------------------------------------

def test_body_no_keys_skips_with_warning(monkeypatch, caplog):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    _reset_token_cache()

    assert _has_reddit_keys() is False

    crawler = RedditCrawler()
    with caplog.at_level("WARNING"):
        result = asyncio.run(crawler.crawl())

    assert result == []
    joined = " ".join(rec.message for rec in caplog.records)
    assert "Reddit OAuth 키 미설정" in joined


# --------------------------------------------------------------------------
# 2) 키 유 + mock token + mock subreddit → ≥ 1 RawVOC
# --------------------------------------------------------------------------

def test_body_keys_with_token_collects_at_least_one(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "csec")
    monkeypatch.setenv("REDDIT_USER_AGENT", "SignalForge/1.0 body-test")
    _reset_token_cache()
    _patch_single_subreddit(monkeypatch)

    listing = _mk_listing_payload("samsung")
    comments = _mk_empty_comments_payload()

    async def fake_get(url, headers=None, params=None, timeout=None):
        assert headers["Authorization"].startswith("bearer ")
        if "/r/samsung/new" in url:
            return _FakeResponse(200, listing)
        if "/comments/xyz789/" in url:
            return _FakeResponse(200, comments)
        return _FakeResponse(404, {})

    async def fake_post(url, headers=None, data=None, timeout=None):
        assert url == reddit_mod.TOKEN_URL
        assert headers["Authorization"].startswith("Basic ")
        assert data == {"grant_type": "client_credentials"}
        return _FakeResponse(200, _mk_token_response("tok-body"))

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=fake_get)
    fake_client.post = AsyncMock(side_effect=fake_post)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    crawler = RedditCrawler()
    monkeypatch.setattr(crawler, "_make_httpx_client", lambda: fake_client)

    result = asyncio.run(crawler.crawl())

    assert len(result) >= 1
    post = result[0]
    assert post.source_url.startswith("https://www.reddit.com/r/samsung/")
    assert "Fold7 hinge creak" in post.content
    assert post.meta["subreddit"] == "samsung"
    assert post.country_code == "US"


# --------------------------------------------------------------------------
# 3) 토큰 만료 → get_reddit_token() 이 자동 재발급
# --------------------------------------------------------------------------

def test_body_token_expiry_triggers_refresh(monkeypatch):
    """캐시된 토큰의 expires_at 이 과거이면 새 토큰을 발급한다."""
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "csec")
    _reset_token_cache()

    issued: List[str] = []

    async def fake_post(url, headers=None, data=None, timeout=None):
        assert url == reddit_mod.TOKEN_URL
        new_tok = f"tok-{len(issued) + 1}"
        issued.append(new_tok)
        return _FakeResponse(200, _mk_token_response(new_tok, expires_in=3600))

    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=fake_post)

    async def _run():
        # 첫 발급
        t1 = await get_reddit_token(fake_client)
        # 캐시 hit — 같은 토큰 반환, post 호출 추가 없음
        t2 = await get_reddit_token(fake_client)
        # 만료 시뮬레이션 — expires_at 을 과거로
        reddit_mod._token_cache["expires_at"] = time.time() - 1.0
        t3 = await get_reddit_token(fake_client)
        return t1, t2, t3

    t1, t2, t3 = asyncio.run(_run())

    assert t1 == "tok-1"
    assert t2 == "tok-1"  # 캐시 hit
    assert t3 == "tok-2"  # 만료 후 재발급
    assert len(issued) == 2
    # 발급 호출 횟수도 2 (캐시 hit 때는 호출 안 함)
    assert fake_client.post.await_count == 2


# --------------------------------------------------------------------------
# 4) 401 응답 시 graceful — crawl() 이 raise 없이 [] 반환
# --------------------------------------------------------------------------

def test_body_persistent_401_returns_empty_gracefully(monkeypatch, caplog):
    """모든 subreddit listing 이 401 이어도 crawl() 은 예외 없이 [] 반환."""
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "csec")
    _reset_token_cache()
    _patch_single_subreddit(monkeypatch)

    async def fake_get(url, headers=None, params=None, timeout=None):
        # listing/comments 양쪽 모두 영구 401 → _authed_get 이 재시도해도 401
        return _FakeResponse(401, {})

    async def fake_post(url, headers=None, data=None, timeout=None):
        # 토큰 발급은 정상 — 그러나 GET 이 영구 401 이라 결국 401 raise
        return _FakeResponse(200, _mk_token_response("tok-graceful"))

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=fake_get)
    fake_client.post = AsyncMock(side_effect=fake_post)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    crawler = RedditCrawler()
    monkeypatch.setattr(crawler, "_make_httpx_client", lambda: fake_client)

    with caplog.at_level("WARNING"):
        result = asyncio.run(crawler.crawl())

    # crawl() 은 raise 없이 끝나야 한다.
    assert result == []
    # 서브레딧 listing 실패 경고가 남는다.
    joined = " ".join(rec.message for rec in caplog.records)
    assert "Reddit r/samsung listing 실패" in joined
