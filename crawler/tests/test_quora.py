"""Quora crawler 단위 테스트 — graceful Cloudflare 차단 동작 검증.

라이브 차단 상태에서는 항상 빈 결과를 반환해야 하며 (예외 없이), stats 에
차단 이유가 명시되어야 한다.  본 라운드 collector 는 graceful 스켈레톤이라
'차단 시 정상 종료' 가 핵심 계약.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms.quora import QuoraCrawler


CF_HTML = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
    "<body>Cloudflare challenge</body></html>"
)


class _Resp:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=None
            )


class _Client403:
    """모든 GET 에 403 + Cloudflare challenge HTML 반환 (현 운영 상태 재현)."""

    def __init__(self):
        self.calls: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, **kwargs):
        self.calls.append(url)
        return _Resp(403, CF_HTML)


class _ClientException:
    """probe 자체가 네트워크 예외로 실패 (오프라인/DNS 실패 등) 재현."""

    def __init__(self):
        self.calls: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, **kwargs):
        self.calls.append(url)
        raise httpx.ConnectError("simulated network down")


def test_cloudflare_block_graceful():
    """403 + Cloudflare challenge probe 시 빈 결과 + stats.blocked 정확."""
    crawler = QuoraCrawler()
    crawler._make_httpx_client = lambda: _Client403()  # type: ignore

    raw_vocs = asyncio.run(crawler.crawl())

    assert raw_vocs == [], f"차단 시 빈 결과여야: {len(raw_vocs)}건 반환"
    assert crawler.stats["blocked"] is True
    assert crawler.stats["probe_status"] == 403
    assert crawler.stats["reason"] is not None
    assert "cloudflare" in crawler.stats["reason"].lower()
    # topic fan-out 은 skip 되어야 함 (probe 1회만 호출)
    assert crawler.stats["topics_attempted"] == 0


def test_probe_exception_graceful():
    """probe 가 네트워크 예외로 실패해도 crawl() 은 예외 없이 빈 결과 반환."""
    crawler = QuoraCrawler()
    crawler._make_httpx_client = lambda: _ClientException()  # type: ignore

    raw_vocs = asyncio.run(crawler.crawl())

    assert raw_vocs == []
    assert crawler.stats["blocked"] is True
    assert crawler.stats["reason"] is not None
    assert "probe_exception" in crawler.stats["reason"]


def test_to_rawvoc_field_mapping():
    """라이브화 대비 _to_rawvoc 매핑 정확성 — 추후 fetch_topic 교체 시 안전망."""
    crawler = QuoraCrawler()
    item = {
        "qid": "q-abc-123",
        "title": "What do you think of the Samsung Galaxy S25 Ultra?",
        "answer_text": "S Pen + 200MP camera makes it the best Android flagship of 2026.",
        "url": "https://www.quora.com/What-do-you-think-of-the-Samsung-Galaxy-S25-Ultra",
        "created_at": "2026-06-01T12:00:00Z",
        "author": "Jane Reviewer",
    }

    voc = crawler._to_rawvoc(item, "Samsung-Galaxy")
    assert voc is not None
    assert voc.external_id and len(voc.external_id) == 16
    assert voc.content.startswith("What do you think")
    assert "S Pen" in voc.content
    assert voc.source_url == item["url"]
    assert voc.author_name == "Jane Reviewer"
    assert voc.published_at is not None
    assert voc.meta["topic"] == "Samsung-Galaxy"
    assert voc.meta["qid"] == "q-abc-123"

    # 필수 필드 결손 시 None
    assert crawler._to_rawvoc({"title": "x", "url": "y"}, "Samsung-Galaxy") is None
    assert crawler._to_rawvoc({"qid": "x", "url": "y"}, "Samsung-Galaxy") is None
