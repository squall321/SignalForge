"""ReseteraCrawler 단위 테스트 — GN RSS 파싱·키워드 필터·ID 안정성."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC
from platforms.resetera import (
    ReseteraCrawler,
    GALAXY_KEYWORD_RE,
    TITLE_SUFFIX_RE,
)


# -- 1) title trailing 시그니처 제거 ----------------------------------------

def test_title_suffix_strips_resetera():
    raw = "Galaxy Z Fold 8 hands-on impressions thread - ResetEra"
    out = TITLE_SUFFIX_RE.sub("", raw).strip()
    assert out == "Galaxy Z Fold 8 hands-on impressions thread"


def test_title_suffix_no_match_keeps_original():
    raw = "Samsung Galaxy thread (no suffix)"
    out = TITLE_SUFFIX_RE.sub("", raw).strip()
    assert out == "Samsung Galaxy thread (no suffix)"


# -- 2) Galaxy/Samsung 키워드 필터 ------------------------------------------

def test_galaxy_keyword_positive_basic():
    c = ReseteraCrawler()
    v = RawVOC(external_id="x", content="Samsung Galaxy S26 thread", source_url="u")
    assert c._is_galaxy_related(v)


def test_galaxy_keyword_positive_fold():
    c = ReseteraCrawler()
    v = RawVOC(external_id="x", content="Galaxy Z Fold 7 leaks discussion", source_url="u")
    assert c._is_galaxy_related(v)


def test_galaxy_keyword_negative_unrelated():
    c = ReseteraCrawler()
    v = RawVOC(external_id="x", content="Best 3D platformer thread", source_url="u")
    assert not c._is_galaxy_related(v)


def test_galaxy_keyword_negative_empty():
    c = ReseteraCrawler()
    v = RawVOC(external_id="x", content="", source_url="u")
    assert not c._is_galaxy_related(v)


def test_galaxy_keyword_negative_mario_galaxy():
    """게이밍 포럼 ResetEra 의 핵심 false-positive: Nintendo Mario Galaxy."""
    c = ReseteraCrawler()
    v = RawVOC(external_id="x", content="Super Mario Galaxy 2 remaster announced", source_url="u")
    assert not c._is_galaxy_related(v)


def test_galaxy_keyword_negative_guardians():
    c = ReseteraCrawler()
    v = RawVOC(external_id="x", content="Guardians of the Galaxy rated for Switch 2", source_url="u")
    assert not c._is_galaxy_related(v)


# -- 3) RSS 날짜 파싱 (Google News GMT) -------------------------------------

def test_parse_rss_date_gmt():
    c = ReseteraCrawler()
    dt = c._parse_rss_date("Fri, 05 Jun 2026 10:13:34 GMT")
    assert dt is not None
    assert dt == datetime(2026, 6, 5, 10, 13, 34, tzinfo=timezone.utc)


def test_parse_rss_date_invalid_returns_none():
    c = ReseteraCrawler()
    assert c._parse_rss_date("") is None
    assert c._parse_rss_date(None) is None


# -- 4) GN feed end-to-end XML → RawVOC --------------------------------------

GN_FEED_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test</title>
    <item>
      <title>Samsung Galaxy Z Fold 8 announced - ResetEra</title>
      <link>https://news.google.com/rss/articles/abc123</link>
      <guid isPermaLink="false">CBM-fold8-1</guid>
      <pubDate>Fri, 05 Jun 2026 10:13:34 GMT</pubDate>
    </item>
    <item>
      <title>Best 3D Platformer discussion - ResetEra</title>
      <link>https://news.google.com/rss/articles/def456</link>
      <guid isPermaLink="false">CBM-plat-1</guid>
      <pubDate>Thu, 04 Jun 2026 08:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


def test_parse_gn_feed_extracts_items():
    c = ReseteraCrawler()
    items = c._parse_gn_feed(GN_FEED_SAMPLE)
    # 2건 모두 파싱 (필터링 _is_galaxy_related 는 crawl() 에서 적용)
    assert len(items) == 2
    titles = [it.content for it in items]
    # trailing 시그니처 제거 확인
    assert "Samsung Galaxy Z Fold 8 announced" in titles
    # 메타데이터
    assert items[0].country_code == "US"
    assert items[0].meta["source"] == "google_news_rss"
    assert items[0].meta["publisher"] == "ResetEra"


def test_parse_gn_feed_external_id_stable():
    c = ReseteraCrawler()
    items1 = c._parse_gn_feed(GN_FEED_SAMPLE)
    items2 = c._parse_gn_feed(GN_FEED_SAMPLE)
    ids1 = sorted(it.external_id for it in items1)
    ids2 = sorted(it.external_id for it in items2)
    assert ids1 == ids2
    assert all(len(eid) == 16 for eid in ids1)


def test_parse_gn_feed_malformed_returns_empty():
    c = ReseteraCrawler()
    items = c._parse_gn_feed("<not xml")
    assert items == []
