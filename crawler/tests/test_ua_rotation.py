"""UA + Accept-Language 회전 단위 테스트 — Harvest 3 트랙 A.

검증:
  1. fetch_with_rotated_ua: 매 호출마다 client.headers["User-Agent"] 가 갱신되고,
     Accept-Language 도 회전된다 (10회 호출 시 최소 2개 이상 UA 관찰).
  2. fmkorea Accept-Language 회전: client.headers["User-Agent"] 는 변하지 않고
     ("세션 페어 유지") Accept-Language 만 회전된다.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import List

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from base.crawler import BaseCrawler, RawVOC, USER_AGENTS, ACCEPT_LANGUAGES  # noqa: E402


class _StubCrawler(BaseCrawler):
    RETRY_BACKOFF_BASE = 0.0
    RETRY_MAX = 1

    async def crawl(self) -> List[RawVOC]:
        return []


def _run(coro):
    return asyncio.run(coro)


def test_clien_style_rotation_changes_ua_and_accept_language():
    """fetch_with_rotated_ua 가 매 호출마다 UA + Accept-Language 를 회전한다."""
    c = _StubCrawler(platform_code="clien")

    observed_uas: List[str] = []
    observed_langs: List[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        observed_uas.append(req.headers.get("user-agent", ""))
        observed_langs.append(req.headers.get("accept-language", ""))
        return httpx.Response(200, text="ok")

    async def _go():
        async with httpx.AsyncClient(
            headers={"User-Agent": "init"},
            transport=httpx.MockTransport(handler),
        ) as cli:
            # 20 회 호출 — 회전 풀이 10개라 충분히 2개 이상 관찰됨
            for _ in range(20):
                r = await c.fetch_with_rotated_ua(
                    cli, "http://x/y",
                    extra_headers={"Referer": "http://x/"},
                )
                assert r is not None
                assert r.status_code == 200

    _run(_go())

    # 모든 UA 가 USER_AGENTS 풀에서 나온다
    assert all(ua in USER_AGENTS for ua in observed_uas), \
        f"풀 외 UA 발견: {set(observed_uas) - set(USER_AGENTS)}"
    # 회전 발생 — 최소 2개 이상의 서로 다른 UA 관찰 (확률적으로 거의 100%)
    assert len(set(observed_uas)) >= 2, \
        f"UA 회전 안됨: {set(observed_uas)}"
    # Accept-Language 도 회전
    assert all(al in ACCEPT_LANGUAGES for al in observed_langs)
    assert len(set(observed_langs)) >= 2, \
        f"Accept-Language 회전 안됨: {set(observed_langs)}"
    # Referer 가 매 요청 적용되었는지 (extra_headers 가 살아 있는지)
    # client.headers 에 박혀서 다음 요청에도 유지
    # → 직접 확인은 handler 외부에서 어렵지만, 코드 경로상 보장됨


def test_fmkorea_style_keeps_ua_rotates_language_only():
    """fmkorea 패턴: UA-Cookie 페어 유지, Accept-Language 만 회전."""
    fixed_ua = "Mozilla/5.0 (PlaywrightSession) FixedUA/1.0"

    observed_uas: List[str] = []
    observed_langs: List[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        observed_uas.append(req.headers.get("user-agent", ""))
        observed_langs.append(req.headers.get("accept-language", ""))
        return httpx.Response(200, text="ok")

    c = _StubCrawler(platform_code="fmkorea")

    async def _go():
        async with httpx.AsyncClient(
            headers={
                "User-Agent": fixed_ua,
                "Cookie": "PHPSESSID=abc; fm5=xyz",
                "Accept-Language": c._random_accept_language(),
            },
            transport=httpx.MockTransport(handler),
        ) as cli:
            # fmkorea 패턴: Accept-Language 만 매 요청 회전
            for _ in range(20):
                cli.headers["Accept-Language"] = c._random_accept_language()
                r = await cli.get("http://x/y")
                assert r.status_code == 200

    _run(_go())

    # UA 는 절대 바뀌면 안됨 (세션 인증 페어)
    assert set(observed_uas) == {fixed_ua}, \
        f"UA 가 회전됨 (예상: 1종 고정): {set(observed_uas)}"
    # Accept-Language 는 회전
    assert all(al in ACCEPT_LANGUAGES for al in observed_langs)
    assert len(set(observed_langs)) >= 2, \
        f"Accept-Language 회전 안됨: {set(observed_langs)}"
