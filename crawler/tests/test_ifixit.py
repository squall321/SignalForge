"""IFixitCrawler 단위 테스트 — News RSS, Answers OG meta, 키워드 필터."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC
from platforms.ifixit import (
    IFixitCrawler,
    GALAXY_KEYWORD_RE,
    QID_RE,
)


# -- 1) Galaxy/Samsung 키워드 필터 ------------------------------------------

def test_galaxy_keyword_positive_galaxy_s():
    c = IFixitCrawler()
    v = RawVOC(external_id="x", content="Galaxy S25 Ultra teardown", source_url="u")
    assert c._is_galaxy_related(v)


def test_galaxy_keyword_positive_buds():
    c = IFixitCrawler()
    v = RawVOC(external_id="x", content="Galaxy Buds right side volume low", source_url="u")
    assert c._is_galaxy_related(v)


def test_galaxy_keyword_negative():
    c = IFixitCrawler()
    v = RawVOC(external_id="x", content="iPhone 17 Pro repair guide", source_url="u")
    assert not c._is_galaxy_related(v)


def test_galaxy_keyword_negative_empty():
    c = IFixitCrawler()
    v = RawVOC(external_id="x", content="", source_url="u")
    assert not c._is_galaxy_related(v)


# -- 2) Answers qid 정규식 --------------------------------------------------

def test_qid_extract_basic():
    url = "https://www.ifixit.com/Answers/View/758924/Samsung+Galaxy+A12"
    m = QID_RE.search(url)
    assert m and m.group(1) == "758924"


def test_qid_extract_no_match():
    url = "https://www.ifixit.com/Guide/Samsung+Galaxy+S25+Battery"
    assert QID_RE.search(url) is None


# -- 3) RSS 날짜 파싱 -------------------------------------------------------

def test_parse_rss_date_gmt():
    c = IFixitCrawler()
    dt = c._parse_rss_date("Wed, 27 May 2026 13:35:50 +0000")
    assert dt is not None
    assert dt == datetime(2026, 5, 27, 13, 35, 50, tzinfo=timezone.utc)


def test_parse_rss_date_invalid_returns_none():
    c = IFixitCrawler()
    assert c._parse_rss_date("") is None
    assert c._parse_rss_date(None) is None


def test_parse_iso_with_offset():
    c = IFixitCrawler()
    dt = IFixitCrawler._parse_iso("2022-12-10T04:42:32-07:00")
    assert dt is not None
    assert dt == datetime(2022, 12, 10, 11, 42, 32, tzinfo=timezone.utc)


def test_parse_iso_z():
    dt = IFixitCrawler._parse_iso("2026-06-01T12:00:00Z")
    assert dt is not None
    assert dt == datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# -- 4) News RSS end-to-end -------------------------------------------------

NEWS_RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
  <title>iFixit News</title>
  <item>
    <title>Samsung Galaxy S25 Ultra Teardown Reveals New Glue Strategy</title>
    <link>https://www.ifixit.com/News/12345/galaxy-s25-teardown</link>
    <dc:creator><![CDATA[Sandra Hiller]]></dc:creator>
    <pubDate>Wed, 27 May 2026 13:35:50 +0000</pubDate>
    <guid isPermaLink="false">https://.ifixit.com/News/?p=12345</guid>
    <description><![CDATA[A look at the new Galaxy S25 Ultra repairability...]]></description>
    <content:encoded><![CDATA[<p>Full body of the article about Galaxy S25 Ultra teardown</p>]]></content:encoded>
  </item>
  <item>
    <title>Robot Vacuum Repair Guide</title>
    <link>https://www.ifixit.com/News/67890/robot-vacuum</link>
    <pubDate>Tue, 26 May 2026 10:00:00 +0000</pubDate>
    <guid isPermaLink="false">https://.ifixit.com/News/?p=67890</guid>
    <description><![CDATA[Unrelated robot vacuum article.]]></description>
  </item>
</channel>
</rss>
"""


def test_parse_news_rss_extracts_items():
    c = IFixitCrawler()
    items = c._parse_news_rss(NEWS_RSS_SAMPLE)
    assert len(items) == 2
    # Galaxy 기사 확인
    titles = [it.content[:80] for it in items]
    assert any("Galaxy S25" in t for t in titles)
    # 메타데이터
    assert items[0].country_code == "US"
    assert items[0].meta["source"] == "ifixit_news_rss"
    assert items[0].author_name == "Sandra Hiller"


def test_parse_news_rss_external_id_stable():
    c = IFixitCrawler()
    items1 = c._parse_news_rss(NEWS_RSS_SAMPLE)
    items2 = c._parse_news_rss(NEWS_RSS_SAMPLE)
    ids1 = sorted(it.external_id for it in items1)
    ids2 = sorted(it.external_id for it in items2)
    assert ids1 == ids2
    assert all(len(eid) == 16 for eid in ids1)


def test_parse_news_rss_malformed_returns_empty():
    c = IFixitCrawler()
    assert c._parse_news_rss("<not xml") == []


# -- 5) Answers OG meta 파싱 -------------------------------------------------

ANSWER_HTML_SAMPLE = """<!DOCTYPE html><html><head>
<meta property="og:title" content="Samsung Galaxy A12, Screen black but responsive. - RESOLVED - Samsung Galaxy A" />
<meta property="og:description" content="Samsung Galaxy A12 with black screen that is responsive. Clicking power button whilst on causes back lights to come on." />
</head><body>
<time datetime="2022-12-10T04:42:32-07:00">Dec 10, 2022</time>
</body></html>"""


def test_parse_question_extracts_content():
    c = IFixitCrawler()
    voc = c._parse_question(
        "758924",
        "https://www.ifixit.com/Answers/View/758924",
        ANSWER_HTML_SAMPLE,
    )
    assert voc is not None
    # title trailing 카테고리 제거
    assert "Samsung Galaxy A" not in voc.content.split("\n")[0].split(" - ")[-1]
    # title + desc 결합
    assert "Screen black" in voc.content
    assert "black screen that is responsive" in voc.content
    assert voc.country_code == "US"
    assert voc.meta["qid"] == "758924"
    assert voc.meta["source"] == "ifixit_answers"
    # published_at 파싱
    assert voc.published_at == datetime(2022, 12, 10, 11, 42, 32, tzinfo=timezone.utc)


def test_parse_question_short_content_returns_none():
    c = IFixitCrawler()
    # 모든 meta 누락 → content 짧음
    voc = c._parse_question("999", "https://x", "<html></html>")
    assert voc is None


def test_parse_question_external_id_stable():
    c = IFixitCrawler()
    v1 = c._parse_question("758924", "u", ANSWER_HTML_SAMPLE)
    v2 = c._parse_question("758924", "u", ANSWER_HTML_SAMPLE)
    assert v1.external_id == v2.external_id
    assert len(v1.external_id) == 16
