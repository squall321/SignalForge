"""SammyFansCrawler 단위 테스트 — 네트워크 없이 파서/필터/ID 안정성 검증."""
import hashlib
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC
from platforms.sammyfans import (
    SammyFansCrawler,
    GALAXY_KEYWORDS,
    POST_URL_RE,
    SAFARI_UA,
    FIREFOX_UA,
)


# -- 1) URL / GUID 파싱 ---------------------------------------------------

def test_post_url_regex_matches_dated_slug():
    url = "https://www.sammyfans.com/2026/06/01/one-ui-8-5-update-rolls-out/"
    m = POST_URL_RE.match(url)
    assert m
    assert m.group(1) == "2026" and m.group(2) == "06" and m.group(3) == "01"
    assert m.group(4) == "one-ui-8-5-update-rolls-out"

def test_post_url_regex_rejects_non_post():
    bad = [
        "https://www.sammyfans.com/feed/",
        "https://www.sammyfans.com/category/samsung/",
        "https://www.sammyfans.com/?p=150303",
    ]
    for u in bad:
        assert POST_URL_RE.match(u) is None, f"매치되면 안됨: {u}"

def test_extract_post_id_from_wp_guid():
    assert SammyFansCrawler._extract_post_id(
        "https://www.sammyfans.com/?p=150303"
    ) == "150303"
    assert SammyFansCrawler._extract_post_id("") is None
    assert SammyFansCrawler._extract_post_id("no-id-here") is None

def test_extract_comment_id_from_wp_guid():
    assert SammyFansCrawler._extract_comment_id(
        "https://www.sammyfans.com/2026/06/01/foo/#comment-12345"
    ) == "12345"
    assert SammyFansCrawler._extract_comment_id("") is None


# -- 2) HTML 정제 ---------------------------------------------------------

def test_strip_html_removes_tags_and_entities():
    raw = "<p>Samsung &amp; <b>Galaxy</b>  S26<br/>Ultra</p>"
    assert SammyFansCrawler._strip_html(raw) == "Samsung & Galaxy S26 Ultra"

def test_strip_html_removes_script_and_style():
    raw = '<p>OK</p><script>alert("x")</script><style>.a{}</style><p>HI</p>'
    out = SammyFansCrawler._strip_html(raw)
    assert "alert" not in out
    assert ".a{" not in out
    assert "OK" in out and "HI" in out

def test_strip_html_empty():
    assert SammyFansCrawler._strip_html("") == ""
    assert SammyFansCrawler._strip_html(None) == ""


# -- 3) 시간 파싱 (RFC822 → UTC) -----------------------------------------

def test_parse_rss_date_utc():
    c = SammyFansCrawler()
    dt = c._parse_rss_date("Mon, 01 Jun 2026 18:27:29 +0000")
    assert dt == datetime(2026, 6, 1, 18, 27, 29, tzinfo=timezone.utc)

def test_parse_rss_date_with_offset_normalized_to_utc():
    c = SammyFansCrawler()
    dt = c._parse_rss_date("Mon, 01 Jun 2026 20:27:29 +0200")
    assert dt == datetime(2026, 6, 1, 18, 27, 29, tzinfo=timezone.utc)

def test_parse_rss_date_invalid_returns_none():
    c = SammyFansCrawler()
    assert c._parse_rss_date("") is None
    assert c._parse_rss_date("not a date") is None


# -- 4) Galaxy 키워드 필터 -----------------------------------------------

def test_is_galaxy_related_hits_body_keyword():
    c = SammyFansCrawler()
    v = RawVOC(
        external_id="x",
        content="One UI 8.5 update rolls out for Galaxy M16",
        source_url="https://www.sammyfans.com/2026/06/01/foo/",
    )
    assert c._is_galaxy_related(v) is True

def test_is_galaxy_related_hits_category_when_body_lacks_kw():
    c = SammyFansCrawler()
    v = RawVOC(
        external_id="x",
        content="Apple Display update from LG might change things in display world.",
        source_url="https://www.sammyfans.com/2026/06/01/foo/",
        meta={"categories": ["Samsung Display"]},
    )
    assert c._is_galaxy_related(v) is True

def test_is_galaxy_related_filters_unrelated():
    c = SammyFansCrawler()
    v = RawVOC(
        external_id="x",
        content="iPhone 18 Pro review from Apple insiders, no other brands.",
        source_url="https://www.sammyfans.com/2026/06/01/foo/",
        meta={"categories": ["iPhone"]},
    )
    assert c._is_galaxy_related(v) is False


# -- 5) external_id 안정성 -----------------------------------------------

def test_external_id_post_format_stable():
    url = "https://www.sammyfans.com/2026/06/01/one-ui-8-5-update/"
    a = hashlib.md5(f"{url}#post".encode()).hexdigest()[:16]
    b = hashlib.md5(f"{url}#post".encode()).hexdigest()[:16]
    assert a == b
    assert len(a) == 16

def test_external_id_comment_format_distinct_per_id():
    url = "https://www.sammyfans.com/2026/06/01/foo/"
    a = hashlib.md5(f"{url}#c123".encode()).hexdigest()[:16]
    b = hashlib.md5(f"{url}#c124".encode()).hexdigest()[:16]
    assert a != b


# -- 6) RSS 파서 통합 -----------------------------------------------------

SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:content="http://purl.org/rss/1.0/modules/content/"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:slash="http://purl.org/rss/1.0/modules/slash/">
<channel>
  <title>Sammy Fans</title>
  <link>https://www.sammyfans.com/</link>
  <description>Samsung News</description>
  <item>
    <title>One UI 8.5 update rolls out for Galaxy M16</title>
    <link>https://www.sammyfans.com/2026/06/01/one-ui-8-5-update/</link>
    <dc:creator><![CDATA[Sheetal Malviya]]></dc:creator>
    <pubDate>Mon, 01 Jun 2026 15:09:01 +0000</pubDate>
    <category><![CDATA[One UI]]></category>
    <category><![CDATA[One UI 8.5]]></category>
    <guid isPermaLink="false">https://www.sammyfans.com/?p=150290</guid>
    <description><![CDATA[<p>Short excerpt with Galaxy keyword.</p>]]></description>
    <content:encoded><![CDATA[<p>Samsung has pushed Android 16-based One UI 8.5 update for Galaxy M16 5G.</p>]]></content:encoded>
    <slash:comments>3</slash:comments>
  </item>
  <item>
    <title>Apple Vision Pro 2 review</title>
    <link>https://www.sammyfans.com/2026/06/01/vision-pro-2/</link>
    <dc:creator><![CDATA[X]]></dc:creator>
    <pubDate>Mon, 01 Jun 2026 18:00:00 +0000</pubDate>
    <category><![CDATA[Apple]]></category>
    <guid isPermaLink="false">https://www.sammyfans.com/?p=150303</guid>
    <description><![CDATA[<p>Pure Apple Vision Pro story.</p>]]></description>
    <content:encoded><![CDATA[<p>Apple has refined the headset design and reduced weight.</p>]]></content:encoded>
  </item>
</channel></rss>"""


def test_parse_rss_extracts_items_and_metadata():
    c = SammyFansCrawler()
    items = c._parse_rss(SAMPLE_FEED)
    assert len(items) == 2
    galaxy = next(i for i in items if "One UI" in i.content)
    assert galaxy.author_name == "Sheetal Malviya"
    assert galaxy.published_at == datetime(2026, 6, 1, 15, 9, 1, tzinfo=timezone.utc)
    assert galaxy.comments_count == 3
    assert galaxy.meta["post_id"] == "150290"
    assert "One UI" in galaxy.meta["categories"]
    assert galaxy.meta["kind"] == "article"
    assert galaxy.country_code is None  # GLOBAL
    # external_id 는 link#post md5
    expected = hashlib.md5(
        f"{galaxy.source_url}#post".encode()
    ).hexdigest()[:16]
    assert galaxy.external_id == expected


def test_parse_rss_then_filter_keeps_only_galaxy_items():
    c = SammyFansCrawler()
    items = c._parse_rss(SAMPLE_FEED)
    filtered = [i for i in items if c._is_galaxy_related(i)]
    assert len(filtered) == 1
    assert "One UI" in filtered[0].content


def test_parse_rss_bad_xml_returns_empty():
    c = SammyFansCrawler()
    assert c._parse_rss("<not-xml") == []
    assert c._parse_rss("") == []


# -- 7) UA 폴백 체인 ------------------------------------------------------

class _StubResp:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text


class _StubClient:
    """UA 별로 다른 응답을 돌려주는 가짜 httpx 클라이언트."""
    def __init__(self, ua_responses: dict):
        # {ua_string: _StubResp}, 매치 없으면 403
        self.ua_responses = ua_responses
        self.headers: dict = {}
        self.calls: list = []

    async def get(self, url, headers=None):
        ua = (headers or {}).get("User-Agent", "")
        self.calls.append((url, ua))
        return self.ua_responses.get(ua, _StubResp(403))


@pytest.mark.asyncio
async def test_ua_fallback_uses_firefox_when_safari_403():
    c = SammyFansCrawler()
    client = _StubClient({FIREFOX_UA: _StubResp(200, SAMPLE_FEED)})
    text, ok = await c._get_with_ua_fallback(client, "https://www.sammyfans.com/feed/")
    assert ok is True
    assert "Sammy Fans" in text
    # 두 UA 모두 시도되어야 함
    uas_tried = [ua for _, ua in client.calls]
    assert SAFARI_UA in uas_tried
    assert FIREFOX_UA in uas_tried


@pytest.mark.asyncio
async def test_ua_fallback_returns_false_when_all_blocked():
    c = SammyFansCrawler()
    client = _StubClient({})  # 모든 UA 403
    text, ok = await c._get_with_ua_fallback(client, "https://www.sammyfans.com/feed/")
    assert ok is False
    assert text == ""


# -- 8) 댓글 파싱 ---------------------------------------------------------

SAMPLE_COMMENT_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:content="http://purl.org/rss/1.0/modules/content/"
  xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel>
  <title>Comments on: Foo</title>
  <link>https://www.sammyfans.com/2026/06/01/foo/</link>
  <description>x</description>
  <item>
    <title>By: Alice</title>
    <link>https://www.sammyfans.com/2026/06/01/foo/#comment-100</link>
    <dc:creator><![CDATA[Alice]]></dc:creator>
    <pubDate>Mon, 01 Jun 2026 19:00:00 +0000</pubDate>
    <guid isPermaLink="false">https://www.sammyfans.com/2026/06/01/foo/#comment-100</guid>
    <description><![CDATA[Great article about Galaxy phones!]]></description>
    <content:encoded><![CDATA[<p>Great article about Galaxy phones!</p>]]></content:encoded>
  </item>
</channel></rss>"""


@pytest.mark.asyncio
async def test_fetch_comments_parses_wp_comment_feed():
    c = SammyFansCrawler()
    client = _StubClient({
        SAFARI_UA: _StubResp(200, SAMPLE_COMMENT_FEED),
    })
    post_url = "https://www.sammyfans.com/2026/06/01/foo/"
    comments = await c._fetch_comments(client, post_url, "150000")
    assert len(comments) == 1
    cm = comments[0]
    assert cm.author_name == "Alice"
    assert "Galaxy phones" in cm.content
    assert cm.meta["kind"] == "comment"
    assert cm.meta["comment_id"] == "100"
    assert cm.published_at == datetime(2026, 6, 1, 19, 0, 0, tzinfo=timezone.utc)
    # external_id 안정성
    expected = hashlib.md5(f"{post_url}#c100".encode()).hexdigest()[:16]
    assert cm.external_id == expected


@pytest.mark.asyncio
async def test_fetch_comments_empty_channel_returns_empty():
    """대부분의 글은 댓글이 비어있는 빈 채널 RSS 를 돌려준다."""
    empty_feed = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Comments</title><link>x</link>
<description>x</description></channel></rss>"""
    c = SammyFansCrawler()
    client = _StubClient({SAFARI_UA: _StubResp(200, empty_feed)})
    out = await c._fetch_comments(
        client, "https://www.sammyfans.com/2026/06/01/foo/", "1"
    )
    assert out == []
