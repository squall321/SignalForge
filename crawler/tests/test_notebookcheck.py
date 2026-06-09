"""NotebookCheckCrawler 단위 테스트 — Google News RSS 파싱, 키워드 필터, ID 안정성."""
import hashlib
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC
from platforms.notebookcheck import (
    NotebookCheckCrawler,
    GALAXY_KEYWORD_RE,
    TITLE_SUFFIX_RE,
)


# -- 1) title trailing site 시그니처 제거 -----------------------------------

def test_title_suffix_strips_notebookcheck_news():
    raw = "Samsung Galaxy Z Flip 8 leaks - Notebookcheck.net News"
    out = TITLE_SUFFIX_RE.sub("", raw).strip()
    assert out == "Samsung Galaxy Z Flip 8 leaks"


def test_title_suffix_strips_short_form():
    raw = "Galaxy S26 Ultra benchmark - Notebookcheck"
    out = TITLE_SUFFIX_RE.sub("", raw).strip()
    assert out == "Galaxy S26 Ultra benchmark"


def test_title_suffix_no_match_keeps_original():
    raw = "Samsung Galaxy review"
    out = TITLE_SUFFIX_RE.sub("", raw).strip()
    assert out == "Samsung Galaxy review"


# -- 2) Galaxy/Samsung 키워드 필터 ------------------------------------------

def test_galaxy_keyword_positive_basic():
    crawler = NotebookCheckCrawler()
    v = RawVOC(external_id="x", content="Samsung Galaxy S26 review", source_url="u")
    assert crawler._is_galaxy_related(v)


def test_galaxy_keyword_positive_fold():
    crawler = NotebookCheckCrawler()
    v = RawVOC(external_id="x", content="Galaxy Z Fold 7 official leak", source_url="u")
    assert crawler._is_galaxy_related(v)


def test_galaxy_keyword_positive_oneui():
    crawler = NotebookCheckCrawler()
    v = RawVOC(external_id="x", content="One UI 8 rollout schedule", source_url="u")
    assert crawler._is_galaxy_related(v)


def test_galaxy_keyword_negative_unrelated():
    crawler = NotebookCheckCrawler()
    v = RawVOC(external_id="x", content="MacBook Air M4 review", source_url="u")
    assert not crawler._is_galaxy_related(v)


def test_galaxy_keyword_negative_empty():
    crawler = NotebookCheckCrawler()
    v = RawVOC(external_id="x", content="", source_url="u")
    assert not crawler._is_galaxy_related(v)


# -- 3) RSS 날짜 파싱 (Google News GMT) -------------------------------------

def test_parse_rss_date_gmt():
    c = NotebookCheckCrawler()
    dt = c._parse_rss_date("Fri, 05 Jun 2026 10:13:34 GMT")
    assert dt is not None
    assert dt == datetime(2026, 6, 5, 10, 13, 34, tzinfo=timezone.utc)


def test_parse_rss_date_naive_to_cet():
    c = NotebookCheckCrawler()
    # naive (no tz) → CET (UTC+1) → UTC
    dt = c._parse_rss_date("Fri, 05 Jun 2026 10:00:00")
    assert dt is not None
    assert dt == datetime(2026, 6, 5, 9, 0, 0, tzinfo=timezone.utc)


def test_parse_rss_date_invalid_returns_none():
    c = NotebookCheckCrawler()
    assert c._parse_rss_date("") is None
    assert c._parse_rss_date(None) is None
    # parsedate_to_datetime is lenient — really bad input still may parse to None or raise.
    # We accept either None or a real datetime; only ensure no exception.
    _ = c._parse_rss_date("nonsense")


# -- 4) GN feed 파싱 (end-to-end XML → RawVOC) ------------------------------

GN_FEED_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test</title>
    <item>
      <title>Samsung Galaxy S26 Ultra leaked - Notebookcheck.net News</title>
      <link>https://news.google.com/rss/articles/abc123</link>
      <guid isPermaLink="false">CBM-galaxy-s26-1</guid>
      <pubDate>Fri, 05 Jun 2026 10:13:34 GMT</pubDate>
    </item>
    <item>
      <title>Galaxy Z Fold 7 specs revealed - Notebookcheck.net News</title>
      <link>https://news.google.com/rss/articles/def456</link>
      <guid isPermaLink="false">CBM-fold7-1</guid>
      <pubDate>Thu, 04 Jun 2026 08:00:00 GMT</pubDate>
    </item>
    <item>
      <title>MacBook Pro 16 benchmark - Notebookcheck.net News</title>
      <link>https://news.google.com/rss/articles/xyz789</link>
      <guid isPermaLink="false">CBM-macbook-1</guid>
      <pubDate>Wed, 03 Jun 2026 09:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


def test_parse_gn_feed_extracts_items():
    c = NotebookCheckCrawler()
    items = c._parse_gn_feed(GN_FEED_SAMPLE)
    # 3건 모두 파싱 (필터링 _is_galaxy_related 는 crawl() 에서 적용)
    assert len(items) == 3
    titles = [it.content for it in items]
    # trailing 시그니처 제거 확인
    assert "Samsung Galaxy S26 Ultra leaked" in titles
    assert "Galaxy Z Fold 7 specs revealed" in titles
    # 메타데이터
    assert items[0].country_code == "DE"
    assert items[0].meta["source"] == "google_news_rss"
    assert items[0].meta["publisher"] == "NotebookCheck"


def test_parse_gn_feed_external_id_stable():
    """동일 guid → 동일 external_id (재크롤 시 중복 INSERT 방지)."""
    c = NotebookCheckCrawler()
    items1 = c._parse_gn_feed(GN_FEED_SAMPLE)
    items2 = c._parse_gn_feed(GN_FEED_SAMPLE)
    ids1 = sorted(it.external_id for it in items1)
    ids2 = sorted(it.external_id for it in items2)
    assert ids1 == ids2
    # md5 16자
    assert all(len(eid) == 16 for eid in ids1)


def test_parse_gn_feed_malformed_returns_empty():
    c = NotebookCheckCrawler()
    items = c._parse_gn_feed("<not xml")
    assert items == []
