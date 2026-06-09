"""Bluesky collector 단위 테스트.

두 케이스를 검증한다:
  1) 키 미설정 → crawl() 0건 + warning 로그
  2) 키 + mock 세션 토큰 → mock searchPosts → RawVOC 변환

외부 네트워크 호출 없이 httpx.AsyncClient 를 MagicMock 으로 대체한다.

실행:
  cd crawler && python -m pytest tests/test_bluesky.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms import bluesky as bluesky_mod
from platforms.bluesky import BlueskyCrawler, _has_bluesky_keys, _reset_token_cache


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _mk_session_payload(token: str = "fake-jwt") -> Dict[str, Any]:
    return {
        "accessJwt": token,
        "refreshJwt": "fake-refresh",
        "handle": "signalforge.bsky.social",
        "did": "did:plc:fakeuser",
    }


def _mk_search_payload() -> Dict[str, Any]:
    """가짜 searchPosts 응답 — 2건 (유효 1건 + 빈 text 1건)."""
    return {
        "posts": [
            {
                "uri": "at://did:plc:aaa/app.bsky.feed.post/3kabc",
                "cid": "bafyaaa",
                "author": {
                    "did": "did:plc:aaa",
                    "handle": "fan.bsky.social",
                    "displayName": "Galaxy Fan",
                },
                "record": {
                    "text": "Galaxy S25 Ultra camera is unreal at low light",
                    "createdAt": "2026-06-03T10:00:00.000Z",
                },
                "replyCount": 3,
                "repostCount": 2,
                "likeCount": 15,
                "indexedAt": "2026-06-03T10:00:05.000Z",
            },
            # 빈 text → 무시되어야 함
            {
                "uri": "at://did:plc:bbb/app.bsky.feed.post/3kdef",
                "cid": "bafybbb",
                "author": {"handle": "noop.bsky.social"},
                "record": {"text": "   ", "createdAt": "2026-06-03T10:00:01.000Z"},
                "replyCount": 0,
                "repostCount": 0,
                "likeCount": 0,
                "indexedAt": "2026-06-03T10:00:02.000Z",
            },
        ]
    }


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            req = httpx.Request("GET", "https://bsky.social/")
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
    monkeypatch.delenv("BLUESKY_HANDLE", raising=False)
    monkeypatch.delenv("BLUESKY_PASSWORD", raising=False)
    _reset_token_cache()

    assert _has_bluesky_keys() is False

    crawler = BlueskyCrawler()
    with caplog.at_level("WARNING"):
        result = asyncio.run(crawler.crawl())

    assert result == []
    msgs = " ".join(rec.message for rec in caplog.records)
    assert "Bluesky 인증 키 미설정" in msgs


# --------------------------------------------------------------------------
# 2) 키 + mock 세션 → mock searchPosts → RawVOC
# --------------------------------------------------------------------------

def test_keys_with_mock_session_collects(monkeypatch):
    monkeypatch.setenv("BLUESKY_HANDLE", "signalforge.bsky.social")
    monkeypatch.setenv("BLUESKY_PASSWORD", "app-pw-1234")
    _reset_token_cache()

    # 단일 쿼리만 사용하도록 패치 (테스트 단순화)
    monkeypatch.setattr(bluesky_mod, "QUERY_TERMS", ["Galaxy S25"])

    async def _no_delay(self):
        return None

    monkeypatch.setattr(BlueskyCrawler, "_random_delay", _no_delay)

    search_payload = _mk_search_payload()
    session_payload = _mk_session_payload("tok-A")

    async def fake_get(url, headers=None, params=None, timeout=None):
        if "searchPosts" in url:
            # Authorization 헤더 검증
            assert headers["Authorization"] == "Bearer tok-A"
            assert params["q"] == "Galaxy S25"
            return _FakeResponse(200, search_payload)
        return _FakeResponse(404, {})

    async def fake_post(url, json=None, timeout=None):
        assert url == bluesky_mod.SESSION_URL
        assert json["identifier"] == "signalforge.bsky.social"
        assert json["password"] == "app-pw-1234"
        return _FakeResponse(200, session_payload)

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=fake_get)
    fake_client.post = AsyncMock(side_effect=fake_post)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    crawler = BlueskyCrawler()
    monkeypatch.setattr(crawler, "_make_httpx_client", lambda: fake_client)

    result = asyncio.run(crawler.crawl())

    # 빈 text 1건 제외 → 1건만 수집
    assert len(result) == 1
    voc = result[0]
    assert "Galaxy S25 Ultra camera" in voc.content
    assert voc.likes_count == 15
    assert voc.comments_count == 3
    assert voc.shares_count == 2
    assert voc.source_url == "https://bsky.app/profile/fan.bsky.social/post/3kabc"
    assert voc.author_name == "Galaxy Fan"
    assert voc.meta["uri"] == "at://did:plc:aaa/app.bsky.feed.post/3kabc"
    assert voc.meta["handle"] == "fan.bsky.social"
    # external_id 는 md5 16 char hex
    assert len(voc.external_id) == 16
    # published_at 파싱 확인
    assert voc.published_at is not None
    assert voc.published_at.year == 2026
    assert voc.published_at.month == 6
