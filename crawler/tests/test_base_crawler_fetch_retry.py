"""base.crawler.BaseCrawler.fetch 재시도/백오프 단위 테스트 — R12 트랙 E1.

검증:
  1. 200 응답 → 즉시 반환 + consec_fail_count 리셋.
  2. 429 → 재시도 후 200 → 성공으로 카운트.
  3. 403/503 RETRY_MAX 회 연속 → 마지막 응답 반환 + fail 카운트 +1.
  4. NetworkError RETRY_MAX 회 → None 반환 + fail 카운트 +1.
  5. CONSECUTIVE_FAIL_THRESHOLD 초과 → ERROR 로그 (deactivate 권고).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import List

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from base.crawler import BaseCrawler, RawVOC  # noqa: E402


class _StubCrawler(BaseCrawler):
    """abstract crawl() stub — 테스트 전용."""
    # 테스트 가속 — 백오프 거의 0
    RETRY_BACKOFF_BASE = 0.0
    RETRY_MAX = 3
    CONSECUTIVE_FAIL_THRESHOLD = 5

    async def crawl(self) -> List[RawVOC]:
        return []


def _make_client_with_handler(handler) -> httpx.AsyncClient:
    """MockTransport 로 응답을 제어한다."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _run(coro):
    return asyncio.run(coro)


# ── 1) 200 즉시 성공 ───────────────────────────────────────────────────
def test_fetch_immediate_success_resets_counter():
    c = _StubCrawler(platform_code="test")
    c._consec_fail_count = 3  # 인위적으로 누적

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    async def _go():
        async with _make_client_with_handler(handler) as cli:
            r = await c.fetch(cli, "http://x/y")
            return r

    r = _run(_go())
    assert r is not None
    assert r.status_code == 200
    assert c._consec_fail_count == 0


# ── 2) 429 → 200 재시도 성공 ───────────────────────────────────────────
def test_fetch_429_then_200_succeeds():
    c = _StubCrawler(platform_code="test")

    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, text="rate")
        return httpx.Response(200, text="ok")

    async def _go():
        async with _make_client_with_handler(handler) as cli:
            return await c.fetch(cli, "http://x/y")

    r = _run(_go())
    assert r is not None
    assert r.status_code == 200
    assert state["n"] == 2  # 1 retry
    assert c._consec_fail_count == 0


# ── 3) 503 연속 — RETRY_MAX 모두 소진 → 마지막 503 반환 ────────────────
def test_fetch_503_exhaust_returns_last_response():
    c = _StubCrawler(platform_code="test")

    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(503, text="down")

    async def _go():
        async with _make_client_with_handler(handler) as cli:
            return await c.fetch(cli, "http://x/y")

    r = _run(_go())
    assert r is not None
    assert r.status_code == 503
    assert state["n"] == c.RETRY_MAX
    assert c._consec_fail_count == 1


# ── 4) NetworkError 연속 → None + fail 카운트 ──────────────────────────
def test_fetch_network_error_returns_none():
    c = _StubCrawler(platform_code="test")

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async def _go():
        async with _make_client_with_handler(handler) as cli:
            return await c.fetch(cli, "http://x/y")

    r = _run(_go())
    assert r is None
    assert c._consec_fail_count == 1


# ── 5) 연속 실패 임계 초과 → deactivate 권고 로그 ────────────────────
def test_fetch_threshold_logs_deactivate_recommendation(caplog):
    c = _StubCrawler(platform_code="testplatform")
    c.CONSECUTIVE_FAIL_THRESHOLD = 2  # 빠른 트리거를 위해 낮춤

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="blocked")

    async def _go():
        async with _make_client_with_handler(handler) as cli:
            # 2 회 연속 fail → 임계 도달
            await c.fetch(cli, "http://x/a")
            await c.fetch(cli, "http://x/b")

    with caplog.at_level(logging.ERROR, logger=f"crawler.testplatform"):
        _run(_go())

    msgs = [r.message for r in caplog.records if r.levelno == logging.ERROR]
    assert any("RECOMMEND_DEACTIVATE" in m for m in msgs), msgs
    assert c._consec_fail_count >= c.CONSECUTIVE_FAIL_THRESHOLD


# ── 6) 200 후 fail → 다시 성공 시 카운터 리셋 ─────────────────────────
def test_fetch_success_after_fail_resets_counter():
    c = _StubCrawler(platform_code="test")

    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        # 첫 RETRY_MAX 회는 503 (1회 실패 카운트), 다음 호출은 200
        if state["n"] <= c.RETRY_MAX:
            return httpx.Response(503, text="down")
        return httpx.Response(200, text="ok")

    async def _go():
        async with _make_client_with_handler(handler) as cli:
            await c.fetch(cli, "http://x/a")  # 503 exhaust → fail+1
            return await c.fetch(cli, "http://x/b")  # 200

    r = _run(_go())
    assert r is not None
    assert r.status_code == 200
    assert c._consec_fail_count == 0
