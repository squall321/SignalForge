"""IFixitCrawler 단위 테스트 — News RSS, Answers 검색 API, 키워드 필터."""
import asyncio
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


def test_target_keyword_positive_iphone():
    # iPhone 은 경쟁사 결함 커버리지 대상으로 편입됨(필터가 apple/iphone 포함)
    c = IFixitCrawler()
    v = RawVOC(external_id="x", content="iPhone 17 Pro repair guide", source_url="u")
    assert c._is_galaxy_related(v)


def test_galaxy_keyword_negative():
    c = IFixitCrawler()
    v = RawVOC(external_id="x", content="Dell XPS 15 laptop hinge replacement", source_url="u")
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

# -- 5) Answers 검색 API → RawVOC (상세페이지 fetch 제거 후의 정식 경로) ------

SEARCH_JSON_SAMPLE = {
    "totalResults": 2,
    "moreResults": False,
    "results": [
        {
            "dataType": "question",
            "postid": 962295,
            "title": "Samsung Galaxy Z Fold8 hinge gap after 3 months",
            "raw_text": "Dust got into the hinge and the screen has a line now.",
            "url": "https://www.ifixit.com/Answers/View/962295/Fold8-hinge",
            "username": "tester",
            "date": 1783044214,
            "answer_count": 3,
        },
        {   # question 이 아닌 항목은 제외돼야 함
            "dataType": "guide",
            "postid": 111,
            "title": "Some guide",
            "raw_text": "x" * 50,
            "url": "https://www.ifixit.com/Guide/111",
            "date": 1783044214,
        },
    ],
}


class _FakeSearchResp:
    status_code = 200

    def json(self):
        return SEARCH_JSON_SAMPLE


class _FakeSearchClient:
    def __init__(self):
        self.calls = 0

    async def get(self, url, **kw):
        self.calls += 1
        return _FakeSearchResp()


def test_search_answers_builds_voc_from_search_result():
    """검색 결과에 title·raw_text·date·url 이 있어 상세 fetch 없이 RawVOC 생성."""
    c = IFixitCrawler()
    seen = set()
    out = asyncio.run(c._search_answers(_FakeSearchClient(), "Galaxy Fold", seen))
    assert len(out) == 1, "question 이 아닌 dataType 은 제외돼야 함"
    v = out[0]
    assert "hinge gap" in v.content
    assert "Dust got into the hinge" in v.content
    assert v.source_url.endswith("/962295/Fold8-hinge")
    assert v.author_name == "tester"
    assert v.comments_count == 3
    assert v.country_code == "US"
    assert v.meta["source"] == "ifixit_answers"
    assert v.published_at == datetime.fromtimestamp(1783044214, tz=timezone.utc)


def test_search_answers_dedups_by_qid():
    c = IFixitCrawler()
    seen = set()
    first = asyncio.run(c._search_answers(_FakeSearchClient(), "Galaxy Fold", seen))
    second = asyncio.run(c._search_answers(_FakeSearchClient(), "Galaxy Fold", seen))
    assert len(first) == 1 and second == [], "이미 본 qid 는 재수집하지 않아야 함"


def test_search_answers_external_id_stable():
    c = IFixitCrawler()
    a = asyncio.run(c._search_answers(_FakeSearchClient(), "q", set()))[0]
    b = asyncio.run(c._search_answers(_FakeSearchClient(), "q", set()))[0]
    assert a.external_id == b.external_id
    assert len(a.external_id) == 16
