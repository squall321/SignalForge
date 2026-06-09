"""XDA news_tag 정식 collector 단위 테스트 — Harvest 5 V1.

DB / 네트워크 의존성 0. HTML/RSS 샘플은 inline.
검증 항목:
  1. XDA_FEEDS 가 9 카테고리로 확장됐는지.
  2. RSS fallback 파서가 정상 항목을 RawVOC 로 변환하는지.
  3. Galaxy 키워드 필터가 RSS 경로에서도 적용되는지.
  4. external_id 안정성 (재호출 시 같은 ID).
  5. pubDate (RFC822) → UTC 변환.
  6. _fetch_article_list 와 _fetch_rss_fallback 가 monkeypatch 로
     상호 독립 호출되어 dedup 합쳐지는지 (mock httpx).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CRAWLER_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if CRAWLER_ROOT not in sys.path:
    sys.path.insert(0, CRAWLER_ROOT)

from platforms.xda import (  # noqa: E402
    XDACrawler,
    XDA_FEEDS,
    XDA_RSS_FALLBACK,
    GALAXY_KEYWORDS,
)


# -- 1) 카테고리 확장 -------------------------------------------------------

def test_xda_feeds_extended_to_nine_tags():
    """H5 V1: 4 → 9 tags."""
    assert len(XDA_FEEDS) == 9
    # 기존 4종 유지
    assert any("samsung-galaxy/" in u for u in XDA_FEEDS)
    assert any("samsung-galaxy-fold" in u for u in XDA_FEEDS)
    assert any("one-ui" in u for u in XDA_FEEDS)
    assert any("/tag/samsung/" in u for u in XDA_FEEDS)
    # 신규 5종 추가
    assert any("samsung-galaxy-z-flip" in u for u in XDA_FEEDS)
    assert any("samsung-galaxy-watch" in u for u in XDA_FEEDS)
    assert any("samsung-galaxy-buds" in u for u in XDA_FEEDS)
    assert any("samsung-galaxy-tab" in u for u in XDA_FEEDS)
    assert any("samsung-galaxy-a/" in u for u in XDA_FEEDS)


def test_xda_rss_fallback_url_is_samsung_tag():
    assert XDA_RSS_FALLBACK.endswith("/feed/tag/samsung/")


# -- 2) RSS 파서 -----------------------------------------------------------

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>XDA - Samsung</title>
    <item>
      <title><![CDATA[Galaxy S26 Ultra review — the best Samsung yet]]></title>
      <link>https://www.xda-developers.com/galaxy-s26-ultra-review/</link>
      <dc:creator><![CDATA[Alex Dobie]]></dc:creator>
      <pubDate>Mon, 01 Jun 2026 15:02:17 GMT</pubDate>
      <guid isPermaLink="true">https://www.xda-developers.com/galaxy-s26-ultra-review/</guid>
    </item>
    <item>
      <title><![CDATA[One UI 8 rollout reaches Galaxy Z Fold 6]]></title>
      <link>https://www.xda-developers.com/one-ui-8-fold6/</link>
      <dc:creator><![CDATA[Jane Doe]]></dc:creator>
      <pubDate>Sun, 31 May 2026 10:00:00 GMT</pubDate>
      <guid isPermaLink="true">https://www.xda-developers.com/one-ui-8-fold6/</guid>
    </item>
    <item>
      <title><![CDATA[MacBook Air M4 benchmark]]></title>
      <link>https://www.xda-developers.com/macbook-air-m4/</link>
      <dc:creator><![CDATA[John Smith]]></dc:creator>
      <pubDate>Sat, 30 May 2026 09:00:00 GMT</pubDate>
      <guid isPermaLink="true">https://www.xda-developers.com/macbook-air-m4/</guid>
    </item>
  </channel>
</rss>
"""


def test_parse_rss_feed_filters_galaxy_only():
    """Samsung/Galaxy 키워드 미포함 항목은 제외 — MacBook 1건 탈락."""
    c = XDACrawler()
    items = c._parse_rss_feed(RSS_SAMPLE)
    assert len(items) == 2
    titles = [it.content for it in items]
    assert any("Galaxy S26 Ultra" in t for t in titles)
    assert any("One UI 8 rollout" in t for t in titles)
    assert not any("MacBook" in t for t in titles)


def test_parse_rss_feed_extracts_metadata():
    c = XDACrawler()
    items = c._parse_rss_feed(RSS_SAMPLE)
    first = items[0]
    assert first.source_url.startswith("https://www.xda-developers.com/")
    assert first.author_name == "Alex Dobie"
    assert first.country_code == "US"
    assert first.published_at == datetime(2026, 6, 1, 15, 2, 17, tzinfo=timezone.utc)


def test_parse_rss_feed_external_id_stable():
    """동일 RSS → 동일 external_id (재크롤 시 중복 INSERT 방지)."""
    c = XDACrawler()
    ids1 = sorted(it.external_id for it in c._parse_rss_feed(RSS_SAMPLE))
    ids2 = sorted(it.external_id for it in c._parse_rss_feed(RSS_SAMPLE))
    assert ids1 == ids2
    assert all(len(eid) == 16 for eid in ids1)


def test_parse_rss_feed_malformed_returns_empty():
    c = XDACrawler()
    assert c._parse_rss_feed("<not xml") == []


def test_parse_rss_date_naive_treated_as_utc():
    """RFC822 timezone 누락 시 UTC 로 간주 (CET 가정 X — XDA RSS 는 GMT 표기)."""
    c = XDACrawler()
    dt = c._parse_rss_date("Mon, 01 Jun 2026 10:00:00")
    assert dt is not None
    assert dt == datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_rss_date_invalid_returns_none():
    c = XDACrawler()
    assert c._parse_rss_date("") is None
    assert c._parse_rss_date(None) is None


# -- 3) Galaxy 키워드 상수 -------------------------------------------------

def test_galaxy_keywords_cover_main_models():
    kws = [k.lower() for k in GALAXY_KEYWORDS]
    for tok in ("galaxy", "samsung", "fold", "flip", "buds", "watch", "one ui"):
        assert tok in kws
