"""PhandroidCrawler 단위 테스트 — WordPress RSS 파싱, Galaxy 키워드 필터, ID 안정성."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC
from platforms.phandroid import PhandroidCrawler, GALAXY_KEYWORDS


# -- 1) Galaxy/Samsung 키워드 필터 ------------------------------------------

def test_galaxy_keyword_positive_basic():
    c = PhandroidCrawler()
    v = RawVOC(external_id="x", content="Samsung Galaxy S26 review", source_url="u")
    assert c._is_galaxy_related(v)


def test_galaxy_keyword_positive_fold():
    c = PhandroidCrawler()
    v = RawVOC(external_id="x", content="Z Fold 8 chipset leaked", source_url="u")
    assert c._is_galaxy_related(v)


def test_galaxy_keyword_positive_oneui():
    c = PhandroidCrawler()
    v = RawVOC(external_id="x", content="One UI 8 rollout schedule", source_url="u")
    assert c._is_galaxy_related(v)


def test_galaxy_keyword_negative_pixel():
    """Phandroid 일반 Android 사이트 — Pixel 단독 글은 차단되어야 한다."""
    c = PhandroidCrawler()
    v = RawVOC(external_id="x", content="Google Pixel Watch 4 Sale", source_url="u")
    assert not c._is_galaxy_related(v)


def test_galaxy_keyword_negative_iphone():
    c = PhandroidCrawler()
    v = RawVOC(external_id="x", content="iPhone 17 Pro Max benchmark", source_url="u")
    assert not c._is_galaxy_related(v)


def test_galaxy_keyword_negative_empty():
    c = PhandroidCrawler()
    v = RawVOC(external_id="x", content="", source_url="u")
    assert not c._is_galaxy_related(v)


def test_galaxy_keyword_category_match():
    """본문 미일치라도 RSS <category> 태그 'Samsung' 이면 통과."""
    c = PhandroidCrawler()
    v = RawVOC(
        external_id="x",
        content="Generic Android phone roundup",
        source_url="u",
        meta={"categories": ["Samsung", "Android"]},
    )
    assert c._is_galaxy_related(v)


# -- 2) RSS 날짜 파싱 (RFC822 +0000) ----------------------------------------

def test_parse_rss_date_utc():
    c = PhandroidCrawler()
    dt = c._parse_rss_date("Sat, 06 Jun 2026 20:43:36 +0000")
    assert dt == datetime(2026, 6, 6, 20, 43, 36, tzinfo=timezone.utc)


def test_parse_rss_date_naive_assumes_utc():
    c = PhandroidCrawler()
    dt = c._parse_rss_date("Sat, 06 Jun 2026 20:43:36")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.hour == 20


def test_parse_rss_date_empty_returns_none():
    c = PhandroidCrawler()
    assert c._parse_rss_date("") is None


# -- 3) WordPress GUID post_id 추출 -----------------------------------------

def test_extract_post_id_from_wp_guid():
    assert PhandroidCrawler._extract_post_id("https://phandroid.com/?p=361682") == "361682"


def test_extract_post_id_missing_returns_none():
    assert PhandroidCrawler._extract_post_id("") is None
    assert PhandroidCrawler._extract_post_id("https://phandroid.com/2026/06/06/foo/") is None


# -- 4) HTML 태그 정리 -------------------------------------------------------

def test_strip_html_removes_tags_and_scripts():
    s = "<p>Galaxy</p><script>evil()</script> <b>S26</b>"
    out = PhandroidCrawler._strip_html(s)
    assert out == "Galaxy S26"


def test_strip_html_decodes_entities():
    s = "Samsung&#8217;s Galaxy"
    out = PhandroidCrawler._strip_html(s)
    assert out == "Samsung’s Galaxy"


# -- 5) RSS 파싱 (end-to-end XML → RawVOC) ----------------------------------

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
    xmlns:content="http://purl.org/rss/1.0/modules/content/"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:slash="http://purl.org/rss/1.0/modules/slash/">
<channel>
  <title>Phandroid</title>
  <link>https://phandroid.com/</link>
  <description>Test</description>
  <item>
    <title>Samsung Galaxy Z Flip 8 Chipset Leaked</title>
    <link>https://phandroid.com/2026/06/06/galaxy-z-flip-8-chipset/</link>
    <dc:creator><![CDATA[Mike Viray]]></dc:creator>
    <pubDate>Sat, 06 Jun 2026 18:00:00 +0000</pubDate>
    <category><![CDATA[Samsung]]></category>
    <category><![CDATA[Galaxy Z Flip 8]]></category>
    <guid isPermaLink="false">https://phandroid.com/?p=361682</guid>
    <description><![CDATA[<p>Galaxy Z Flip 8 chipset rumor.</p>]]></description>
    <content:encoded><![CDATA[<p>The <b>Galaxy Z Flip 8</b> may ship with an Exynos chipset.</p>]]></content:encoded>
    <slash:comments>5</slash:comments>
  </item>
  <item>
    <title>Pixel Watch 4 41mm Sale</title>
    <link>https://phandroid.com/2026/06/06/pixel-watch-4-41mm-sale/</link>
    <dc:creator><![CDATA[Author B]]></dc:creator>
    <pubDate>Sat, 06 Jun 2026 17:00:00 +0000</pubDate>
    <category><![CDATA[Google]]></category>
    <guid isPermaLink="false">https://phandroid.com/?p=361681</guid>
    <description><![CDATA[<p>Watch sale.</p>]]></description>
    <content:encoded><![CDATA[<p>Pixel Watch 4 is on sale.</p>]]></content:encoded>
    <slash:comments>0</slash:comments>
  </item>
</channel>
</rss>
"""


def test_parse_rss_extracts_two_items():
    c = PhandroidCrawler()
    items = c._parse_rss(RSS_SAMPLE)
    # 파싱은 둘 다 통과 — 필터는 crawl() 에서 적용.
    assert len(items) == 2


def test_parse_rss_galaxy_item_fields():
    c = PhandroidCrawler()
    items = c._parse_rss(RSS_SAMPLE)
    galaxy = next(i for i in items if "Z Flip 8" in i.content)
    assert galaxy.author_name == "Mike Viray"
    assert galaxy.published_at == datetime(2026, 6, 6, 18, 0, 0, tzinfo=timezone.utc)
    assert galaxy.comments_count == 5
    assert galaxy.country_code == "US"
    assert galaxy.meta["post_id"] == "361682"
    assert "Samsung" in galaxy.meta["categories"]
    assert "Exynos" in galaxy.content  # content:encoded 본문 포함
    assert "<b>" not in galaxy.content  # HTML 제거


def test_parse_rss_external_id_stable():
    """동일 RSS → 동일 external_id (재크롤 중복 INSERT 방지)."""
    c = PhandroidCrawler()
    items1 = c._parse_rss(RSS_SAMPLE)
    items2 = c._parse_rss(RSS_SAMPLE)
    ids1 = sorted(it.external_id for it in items1)
    ids2 = sorted(it.external_id for it in items2)
    assert ids1 == ids2
    assert all(len(eid) == 16 for eid in ids1)


def test_parse_rss_filter_drops_non_galaxy():
    """crawl() 의 필터 단계 시뮬레이션 — Pixel 단독 글은 떨어진다."""
    c = PhandroidCrawler()
    items = c._parse_rss(RSS_SAMPLE)
    filtered = [it for it in items if c._is_galaxy_related(it)]
    assert len(filtered) == 1
    assert "Z Flip 8" in filtered[0].content


def test_parse_rss_malformed_returns_empty():
    c = PhandroidCrawler()
    assert c._parse_rss("<not xml") == []
