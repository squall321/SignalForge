"""Mastodon collector 단위 테스트.

검증:
  1) status_to_rawvoc 가 HTML 본문을 평문으로 정리하고 메타에
     instance/tag/status_id 를 채워 RawVOC 를 만든다.
  2) crawl() 이 모킹된 _fetch_tag 응답을 모아 중복 url 을 제거하고
     MX 키워드 필터가 통과되는 항목만 반환한다 (MX 필터는 import 실패
     해도 무해해야 한다).
  3) HTTP 4xx 가 발생해도 다른 instance/tag 는 계속 시도한다.

외부 네트워크 호출 없음 — _fetch_tag 를 monkeypatch.

실행:
  cd crawler && python -m pytest tests/test_mastodon.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms import mastodon as mastodon_mod
from platforms.mastodon import (
    INSTANCES,
    MastodonCrawler,
    TAGS,
    status_to_rawvoc,
)


def _mk_status(sid: str, text_html: str, url: str = "") -> dict:
    return {
        "id": sid,
        "uri": f"https://mastodon.social/users/foo/statuses/{sid}",
        "url": url or f"https://mastodon.social/@foo/{sid}",
        "content": text_html,
        "created_at": "2026-06-09T10:00:00.000Z",
        "language": "en",
        "replies_count": 1,
        "reblogs_count": 2,
        "favourites_count": 5,
        "account": {"acct": "foo@mastodon.social", "display_name": "Foo"},
    }


# --------------------------------------------------------------------------
# 1) status_to_rawvoc 단위 검증
# --------------------------------------------------------------------------

def test_status_to_rawvoc_basic():
    st = _mk_status(
        "112345",
        "<p>Just bought a <b>Galaxy S25 Ultra</b> &mdash; cameras are insane</p>",
    )
    voc = status_to_rawvoc(st, "mastodon.social", "galaxy")
    assert voc is not None
    # HTML 태그 제거되었는지
    assert "<p>" not in voc.content
    assert "<b>" not in voc.content
    assert "Galaxy S25 Ultra" in voc.content
    # 메타·통계 채워졌는지
    assert voc.meta["instance"] == "mastodon.social"
    assert voc.meta["tag"] == "galaxy"
    assert voc.meta["status_id"] == "112345"
    assert voc.meta["acct"] == "foo@mastodon.social"
    assert voc.likes_count == 5
    assert voc.comments_count == 1
    assert voc.shares_count == 2
    assert voc.author_name == "Foo"
    assert voc.published_at is not None
    assert voc.published_at.year == 2026
    # external_id 는 md5 16 hex
    assert len(voc.external_id) == 16


def test_status_to_rawvoc_skips_empty_text():
    """content 가 공백/태그뿐이거나 id 가 없으면 None."""
    assert status_to_rawvoc(_mk_status("111", ""), "mastodon.social", "galaxy") is None
    assert (
        status_to_rawvoc(_mk_status("111", "<p>   </p>"), "mastodon.social", "galaxy")
        is None
    )
    # id 결손
    bad = _mk_status("111", "<p>Galaxy S25</p>")
    bad["id"] = ""
    assert status_to_rawvoc(bad, "mastodon.social", "galaxy") is None


# --------------------------------------------------------------------------
# 2) crawl() — fan-out + dedup + MX filter
# --------------------------------------------------------------------------

def test_crawl_fanout_and_dedup(monkeypatch):
    # fan-out 축소로 테스트 단순화 (1 instance × 2 tag)
    monkeypatch.setattr(mastodon_mod, "INSTANCES", ["mastodon.social"])
    monkeypatch.setattr(mastodon_mod, "TAGS", ["galaxy", "samsung"])

    async def _no_delay(self):
        return None

    monkeypatch.setattr(MastodonCrawler, "_random_delay", _no_delay)

    # 동일 url 2건 (중복 dedup 검증) + MX 무관 1건 (filter 검증) + MX 매칭 1건
    galaxy_payload = [
        _mk_status("aa1", "<p>Galaxy S25 Ultra camera review</p>",
                   url="https://mastodon.social/@a/aa1"),
        _mk_status("dup", "<p>Galaxy Z Fold7 hinge feels solid</p>",
                   url="https://mastodon.social/@b/shared"),  # 중복 url
    ]
    samsung_payload = [
        _mk_status("dup2", "<p>Samsung One UI 7 new gestures</p>",
                   url="https://mastodon.social/@b/shared"),  # 위와 같은 url → dedup
        _mk_status("nomx", "<p>Just had pizza for lunch today</p>",
                   url="https://mastodon.social/@c/nomx"),  # mx 무관
    ]

    async def fake_fetch(self, client, instance, tag):
        if tag == "galaxy":
            return galaxy_payload
        if tag == "samsung":
            return samsung_payload
        return []

    monkeypatch.setattr(MastodonCrawler, "_fetch_tag", fake_fetch)

    crawler = MastodonCrawler()
    vocs = asyncio.run(crawler.crawl())

    # dedup → 3건 (aa1, dup, nomx).  이후 MX 필터 → nomx 제외, 최종 2건.
    urls = sorted(v.source_url for v in vocs)
    assert "https://mastodon.social/@a/aa1" in urls
    assert "https://mastodon.social/@b/shared" in urls
    # mx 무관 항목은 mx_keywords 필터로 제거되어야 함 (필터 실패 시 graceful 통과 가능)
    # 최소한 MX 매칭 2건은 반드시 포함.
    assert len(vocs) >= 2
    assert all(("Galaxy" in v.content or "Samsung" in v.content) for v in vocs)

    # stats per_tag 기록 확인
    assert crawler.stats["per_tag"].get("mastodon.social/galaxy") == 2
    # samsung tag 는 dedup 으로 1건만 신규 카운트 (dup2 는 동일 url, nomx 만 신규)
    assert crawler.stats["per_tag"].get("mastodon.social/samsung") == 1


# --------------------------------------------------------------------------
# 3) HTTP 4xx 발생 → 다른 tag 는 계속 시도
# --------------------------------------------------------------------------

def test_crawl_handles_http_error(monkeypatch):
    monkeypatch.setattr(mastodon_mod, "INSTANCES", ["mastodon.social"])
    monkeypatch.setattr(mastodon_mod, "TAGS", ["galaxy", "samsung"])

    async def _no_delay(self):
        return None

    monkeypatch.setattr(MastodonCrawler, "_random_delay", _no_delay)

    async def fake_fetch(self, client, instance, tag):
        if tag == "galaxy":
            req = httpx.Request("GET", f"https://{instance}/api/v1/timelines/tag/{tag}")
            resp = httpx.Response(429, request=req)
            raise httpx.HTTPStatusError("429 rate limit", request=req, response=resp)
        return [_mk_status("ok1", "<p>Samsung Galaxy S25 quick take</p>",
                           url="https://mastodon.social/@x/ok1")]

    monkeypatch.setattr(MastodonCrawler, "_fetch_tag", fake_fetch)

    crawler = MastodonCrawler()
    vocs = asyncio.run(crawler.crawl())

    # galaxy 는 blocked 로 기록, samsung 만 1건 수집
    assert any("galaxy" in b for b in crawler.stats["blocked"])
    assert len(vocs) == 1
    assert "Galaxy S25" in vocs[0].content
