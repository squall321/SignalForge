"""4chan /g/ crawler 단위 테스트 (HTTP mock).

검증:
1. catalog → mobile 매칭 thread 추출
2. thread fetch → HTML sanitize + MX 필터 + 짧은 댓글 컷
3. RawVOC 메타 (thread_no/post_no/is_op) 정확성
"""
from __future__ import annotations
import asyncio
import os
import sys
from typing import Any

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms.fourchan_g import FourchanGCrawler, _clean_html


FAKE_CATALOG = [
    {
        "page": 1,
        "threads": [
            {"no": 1001, "sub": "/spg/ - Smartphone General", "com": "Galaxy S25 review", "replies": 200},
            {"no": 1002, "sub": "", "com": "iPhone 16 thread - thoughts?", "replies": 50},
            {"no": 1003, "sub": "Linux distro recommend", "com": "Ubuntu vs Arch", "replies": 30},
            {"no": 1004, "sub": "", "com": "What CPU should I buy", "replies": 10},
        ],
    },
]

FAKE_THREAD_1001 = {
    "posts": [
        {
            "no": 1001, "time": 1781000000,
            "sub": "/spg/ - Smartphone General",
            "com": "<span class=\"quote\">&gt;previous</span><br>Galaxy S25 Ultra build quality is solid, S Pen still useful for note-taking",
        },
        {
            "no": 1002, "time": 1781000100,
            "com": "fold 6 hinge feels better than my old <wbr>z fold 4. samsung dex is underrated honestly",
        },
        {
            "no": 1003, "time": 1781000200,
            "com": "lol",  # 너무 짧음 → 컷
        },
        {
            "no": 1004, "time": 1781000300,
            "com": "Random off-topic comment about gaming PCs without any phone keywords here",  # MX 컷
        },
    ],
}

FAKE_THREAD_1002 = {
    "posts": [
        {
            "no": 2001, "time": 1781000400,
            "sub": "iPhone 16 thread - thoughts?",
            "com": "Apple Intelligence on iPhone 16 Pro is decent but battery drains quicker with it on",
        },
    ],
}


class _MockResp:
    def __init__(self, payload: Any, status: int = 200):
        self._payload = payload
        self.status_code = status
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._payload


class _MockClient:
    def __init__(self):
        self.calls: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, **kwargs):
        self.calls.append(url)
        if "catalog.json" in url:
            return _MockResp(FAKE_CATALOG)
        if "/thread/1001.json" in url:
            return _MockResp(FAKE_THREAD_1001)
        if "/thread/1002.json" in url:
            return _MockResp(FAKE_THREAD_1002)
        return _MockResp({"posts": []})


def test_clean_html_basic():
    s = "<span class=\"quote\">&gt;quote</span><br>Galaxy <wbr>S25 review &amp; thoughts"
    out = _clean_html(s)
    assert "<span" not in out
    assert "<br>" not in out
    assert "<wbr>" not in out
    assert ">quote" in out
    assert "Galaxy S25" in out
    assert "&amp;" not in out and "&" in out


def test_clean_html_empty():
    assert _clean_html("") == ""
    assert _clean_html(None) == ""


def test_crawl_filters_and_sanitizes(monkeypatch):
    crawler = FourchanGCrawler()
    # delay 우회
    async def _no_delay():
        return None
    crawler._random_delay = _no_delay  # type: ignore
    crawler._make_httpx_client = lambda: _MockClient()  # type: ignore

    raw_vocs = asyncio.run(crawler.crawl())

    # 1001 OP + 1001 댓글 + 1002 OP = 3 (Linux/CPU thread 는 catalog 컷)
    # 1003 lol(짧음 컷), 1004(MX 컷) → 제외
    assert len(raw_vocs) == 3, f"expected 3, got {len(raw_vocs)}: {[v.content[:40] for v in raw_vocs]}"

    # OP 메타 확인
    op_voc = next(v for v in raw_vocs if v.meta["post_no"] == 1001)
    assert op_voc.meta["is_op"] is True
    assert op_voc.meta["thread_no"] == 1001
    assert "<span" not in op_voc.content
    assert "Galaxy S25" in op_voc.content
    assert op_voc.source_url.endswith("#p1001")
    assert op_voc.author_name is None  # 익명

    # 댓글 (is_op=False)
    comment_voc = next(v for v in raw_vocs if v.meta["post_no"] == 1002 and v.meta["thread_no"] == 1001)
    assert comment_voc.meta["is_op"] is False
    assert "fold 6" in comment_voc.content.lower()
    assert "<wbr>" not in comment_voc.content
