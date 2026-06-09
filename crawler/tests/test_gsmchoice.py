"""
GSMchoice 크롤러 단위 테스트 — 외부 네트워크 없이 파서/필터/ID 안정성 검증.

실행: cd crawler && python -m pytest tests/test_gsmchoice.py -v
"""
import hashlib
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC
from platforms.gsmchoice import (
    GSMchoiceCrawler,
    GN_RSS,
    SEARCH_TERMS,
    MAX_POSTS,
    LIST_PAGES,
    TITLE_SUFFIX_RE,
    GALAXY_KEYWORD_RE,
)


# ------------------------------------------------------------
# Test 1: title 접미사 제거 — " - GSMchoice.com" / " - GSMchoice"
# ------------------------------------------------------------
def test_title_suffix_removed():
    assert TITLE_SUFFIX_RE.sub(
        "", "Samsung Galaxy A31 Dual SIM - GSMchoice.com"
    ).strip() == "Samsung Galaxy A31 Dual SIM"
    # 변형 형태
    assert TITLE_SUFFIX_RE.sub(
        "", "Samsung Galaxy S26 review - GSMchoice"
    ).strip() == "Samsung Galaxy S26 review"
    # 시그니처 없으면 그대로 (Note: leading/trailing whitespace 처리는 호출부에서)
    assert TITLE_SUFFIX_RE.sub(
        "", "Samsung Galaxy Fold 7"
    ) == "Samsung Galaxy Fold 7"


# ------------------------------------------------------------
# Test 2: Galaxy/Samsung 키워드 필터 — 정밀 매칭, false positive 방지
# ------------------------------------------------------------
def test_keyword_filter_matches_samsung_galaxy():
    c = GSMchoiceCrawler()
    samples = [
        "Samsung Galaxy A31 Dual SIM technical specifications",
        "Samsung Galaxy Note 7 Galaxy Note7 review",
        "Galaxy S25 Ultra release",
        "Galaxy Fold 7 hands on",
        "Galaxy Buds Pro 2026 announced",
        "One UI 8 beta program",
    ]
    for s in samples:
        v = RawVOC(external_id="x", content=s, source_url="https://news.google.com/rss/articles/x")
        assert c._is_galaxy_related(v), f"매칭되어야 함: {s}"


def test_keyword_filter_rejects_unrelated():
    c = GSMchoiceCrawler()
    # 단독 'tab'/'watch'/'fold'/'ring' 은 false positive 회피 — 매칭 안 됨
    rejects = [
        "iPhone 18 Pro Max technical specifications",
        "Apple Watch Ultra 3 review",
        "Pixel 11 Pro foldable phone",
        "OnePlus 14 Pro hands on review",
    ]
    for s in rejects:
        v = RawVOC(external_id="x", content=s, source_url="https://news.google.com/rss/articles/x")
        assert not c._is_galaxy_related(v), f"매칭되면 안 됨: {s}"


# ------------------------------------------------------------
# Test 3: RFC822 pubDate (GMT) → UTC 변환
# ------------------------------------------------------------
def test_parse_rss_date_gmt_to_utc():
    c = GSMchoiceCrawler()
    dt = c._parse_rss_date("Tue, 26 Aug 2025 08:14:41 GMT")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt == datetime(2025, 8, 26, 8, 14, 41, tzinfo=timezone.utc)


def test_parse_rss_date_with_offset_normalizes_to_utc():
    c = GSMchoiceCrawler()
    # +0100 → 1시간 빼서 UTC
    dt = c._parse_rss_date("Mon, 07 Apr 2025 11:04:18 +0100")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt == datetime(2025, 4, 7, 10, 4, 18, tzinfo=timezone.utc)


def test_parse_rss_date_invalid_returns_none():
    c = GSMchoiceCrawler()
    assert c._parse_rss_date("") is None
    assert c._parse_rss_date("not a date") is None


# ------------------------------------------------------------
# Test 4: external_id 안정성 — md5(guid)[:16], 입력 동일 → 출력 동일
# ------------------------------------------------------------
def test_external_id_stable_and_unique():
    guid = (
        "CBMickFVX3lxTE1RT2pwZDgzOGJwcVB3WDNudU8tMWlwOHF0bFEyR3pt"
        "UHp4S2dtdXZDUk1HaHphS0VzLTZjc0lGVUxVNXE3ejNSNmxhMFBGanBzVkZBU2M1"
    )
    a = hashlib.md5(f"gsmchoice#{guid}".encode()).hexdigest()[:16]
    b = hashlib.md5(f"gsmchoice#{guid}".encode()).hexdigest()[:16]
    assert a == b, "동일 guid → 동일 external_id (재크롤 중복 방지)"
    assert len(a) == 16, "DB 컬럼 일관성 — 16자 고정"

    # 다른 guid → 다른 id
    other = hashlib.md5(f"gsmchoice#{guid}_other".encode()).hexdigest()[:16]
    assert a != other


# ------------------------------------------------------------
# Test 5: GN RSS XML 파싱 — title 정제, suffix 제거, country_code=GB, meta
# ------------------------------------------------------------
SAMPLE_GN_RSS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
<channel>
<title>"site:gsmchoice.com samsung" - Google News</title>
<link>https://news.google.com/search?q=site:gsmchoice.com+samsung</link>
<item>
  <title>Samsung Galaxy A31 Dual SIM technical specifications - GSMchoice.com</title>
  <link>https://news.google.com/rss/articles/CBMickFVX3lxTE1Rsamplecapsule1?oc=5</link>
  <guid isPermaLink="false">CBMickFVX3lxTE1Rsamplecapsule1</guid>
  <pubDate>Tue, 26 Aug 2025 08:14:41 GMT</pubDate>
  <source url="https://www.gsmchoice.com">GSMchoice.com</source>
</item>
<item>
  <title>Samsung Galaxy S26 Ultra benchmark leak - GSMchoice.com</title>
  <link>https://news.google.com/rss/articles/CBMiW0FVX3lxTFA1dTNYsamplecapsule2?oc=5</link>
  <guid isPermaLink="false">CBMiW0FVX3lxTFA1dTNYsamplecapsule2</guid>
  <pubDate>Mon, 07 Apr 2025 11:04:18 +0100</pubDate>
</item>
<item>
  <title>iPhone 18 Pro review - GSMchoice.com</title>
  <link>https://news.google.com/rss/articles/CBMiOiPhonesamplecapsule3?oc=5</link>
  <guid isPermaLink="false">CBMiOiPhonesamplecapsule3</guid>
  <pubDate>Wed, 10 May 2025 00:00:00 GMT</pubDate>
</item>
<item>
  <title>Short</title>
  <link>https://news.google.com/rss/articles/CBMiShortsample?oc=5</link>
  <guid isPermaLink="false">CBMiShortsample</guid>
  <pubDate>Wed, 10 May 2025 00:00:00 GMT</pubDate>
</item>
</channel>
</rss>"""


def test_parse_gn_feed_extracts_items_with_metadata():
    c = GSMchoiceCrawler()
    items = c._parse_gn_feed(SAMPLE_GN_RSS)
    # 4 items in feed, all >= 20 chars after suffix removal except 'Short' & it gets dropped
    # Samsung A31, Samsung S26, iPhone 18 → 3 (Short 컷)
    assert len(items) == 3, f"Short 제목 컷 + 나머지 3건, got {len(items)}"

    # 1st item — Samsung A31
    a = items[0]
    assert a.content == "Samsung Galaxy A31 Dual SIM technical specifications"
    assert a.source_url.startswith("https://news.google.com/rss/articles/")
    assert a.country_code == "GB"
    assert a.published_at == datetime(2025, 8, 26, 8, 14, 41, tzinfo=timezone.utc)
    assert a.meta["source"] == "google_news_rss"
    assert a.meta["publisher"] == "GSMchoice.com"
    assert a.meta["guid"].startswith("CBM")

    # 2nd item — Samsung S26 (offset → UTC 변환)
    b = items[1]
    assert "Galaxy S26" in b.content
    assert b.published_at == datetime(2025, 4, 7, 10, 4, 18, tzinfo=timezone.utc)
    # publisher source 태그 없으면 기본값 "GSMchoice"
    assert b.meta["publisher"] == "GSMchoice"


def test_filter_pipeline_keeps_galaxy_drops_iphone():
    """파싱 → 키워드 필터 통합: iPhone 항목은 필터 단계에서 제외."""
    c = GSMchoiceCrawler()
    items = c._parse_gn_feed(SAMPLE_GN_RSS)
    filtered = [v for v in items if c._is_galaxy_related(v)]
    titles = [v.content for v in filtered]
    assert any("A31" in t for t in titles), "Samsung A31 유지"
    assert any("S26" in t for t in titles), "Samsung S26 유지"
    assert not any("iPhone" in t for t in titles), "iPhone 제외"


# ------------------------------------------------------------
# Test 6: 설정값/상수 sanity (LIST_PAGES=12, MAX_POSTS=150 규약 준수)
# ------------------------------------------------------------
def test_module_constants():
    assert LIST_PAGES == 12
    assert MAX_POSTS == 150
    assert "site:gsmchoice.com" in GN_RSS
    assert "hl=en-GB" in GN_RSS
    assert "gl=GB" in GN_RSS
    # 다중 쿼리 — 단일 GN 응답 100건 한계를 분산 검색으로 보완
    assert len(SEARCH_TERMS) >= 2
    assert "samsung" in SEARCH_TERMS
    assert "galaxy" in SEARCH_TERMS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
