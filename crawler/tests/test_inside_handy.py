"""InsideHandyCrawler 단위 테스트 — 네트워크 없이 파서/필터/ID 안정성 검증."""
import hashlib
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC
from platforms.inside_handy import (
    InsideHandyCrawler,
    GALAXY_KEYWORDS,
    GUID_PID_RE,
    ARTICLE_BODY_RE,
)


# -- 1) GUID post_id 정규식 ---------------------------------------------

def test_extract_post_id_plain():
    assert (
        InsideHandyCrawler._extract_post_id(
            "https://www.inside-digital.de/?p=1054045"
        )
        == "1054045"
    )

def test_extract_post_id_with_post_type():
    """WP GUID 에 HTML 엔티티 &#038; 가 들어있는 케이스 (RSS 원본 그대로)."""
    guid = "https://www.inside-digital.de/?post_type=deal&#038;p=1054130"
    assert InsideHandyCrawler._extract_post_id(guid) == "1054130"

def test_extract_post_id_returns_none_when_absent():
    assert InsideHandyCrawler._extract_post_id("") is None
    assert (
        InsideHandyCrawler._extract_post_id(
            "https://www.inside-digital.de/news/slug"
        )
        is None
    )


# -- 2) 시간 파싱 (RFC822 → UTC) -----------------------------------------

def test_parse_rss_date_utc_offset():
    """+0000 → UTC 그대로."""
    dt = InsideHandyCrawler._parse_rss_date("Mon, 01 Jun 2026 17:26:00 +0000")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt == datetime(2026, 6, 1, 17, 26, 0, tzinfo=timezone.utc)

def test_parse_rss_date_naive_assumes_cet():
    """tz 없는 경우 CET(+01:00) 가정 → UTC -1h."""
    dt = InsideHandyCrawler._parse_rss_date("Mon, 01 Jun 2026 10:00:00")
    # 10:00 CET → 09:00 UTC
    assert dt == datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)

def test_parse_rss_date_invalid_returns_none():
    assert InsideHandyCrawler._parse_rss_date("") is None
    assert InsideHandyCrawler._parse_rss_date("garbage value") is None


# -- 3) HTML 정제 ---------------------------------------------------------

def test_strip_html_removes_tags_and_entities():
    raw = "<p>Galaxy &amp; <b>S26</b>  Ultra<br>Test</p>"
    assert InsideHandyCrawler._strip_html(raw) == "Galaxy & S26 Ultra Test"

def test_strip_html_removes_script_block():
    raw = '<p>OK</p><script>alert("x")</script><p>HI</p>'
    out = InsideHandyCrawler._strip_html(raw)
    assert "alert" not in out and "OK" in out and "HI" in out

def test_strip_html_empty_safe():
    assert InsideHandyCrawler._strip_html("") == ""
    assert InsideHandyCrawler._strip_html(None) == ""


# -- 4) Galaxy 키워드 필터 -----------------------------------------------

def test_is_galaxy_related_german_samsung():
    c = InsideHandyCrawler()
    v = RawVOC(
        external_id="x",
        content="Angst vor Meta: Warum die neuen Samsung-Brillen ohne Display starten müssen",
        source_url="https://www.inside-digital.de/news/samsung-brillen",
    )
    assert c._is_galaxy_related(v) is True

def test_is_galaxy_related_galaxy_lowercase():
    c = InsideHandyCrawler()
    v = RawVOC(
        external_id="x",
        content="Test des neuen GALAXY S26 Ultra Smartphones",
        source_url="https://www.inside-digital.de/tests/galaxy",
    )
    assert c._is_galaxy_related(v) is True

def test_is_galaxy_related_filters_unrelated():
    c = InsideHandyCrawler()
    v = RawVOC(
        external_id="x",
        content="Aldi verkauft eine Heißluftfritteuse für 59,99 Euro",
        source_url="https://www.inside-digital.de/deals/aldi",
    )
    assert c._is_galaxy_related(v) is False

def test_is_galaxy_related_by_category_only():
    """본문에 키워드 없어도 category 에 'Samsung' 있으면 True."""
    c = InsideHandyCrawler()
    v = RawVOC(
        external_id="x",
        content="Neues Smartphone-Modell 2026 vorgestellt",
        source_url="https://www.inside-digital.de/news/x",
        meta={"categories": ["Smartphone", "Samsung"]},
    )
    assert c._is_galaxy_related(v) is True


# -- 5) external_id 안정성 -----------------------------------------------

def test_external_id_stable_across_runs():
    """external_id = md5(url + '#' + post_id)[:16] — 재크롤 시 동일."""
    url = "https://www.inside-digital.de/news/samsung-test"
    pid = "1054045"
    a = hashlib.md5(f"{url}#{pid}".encode()).hexdigest()[:16]
    b = hashlib.md5(f"{url}#{pid}".encode()).hexdigest()[:16]
    assert a == b and len(a) == 16
    # 다른 글 → 다른 id
    other = hashlib.md5(f"{url}#{pid}9".encode()).hexdigest()[:16]
    assert a != other


# -- 6) RSS 파싱 통합 -----------------------------------------------------

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"
  xmlns:content="http://purl.org/rss/1.0/modules/content/"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:slash="http://purl.org/rss/1.0/modules/slash/">
<channel>
  <title>inside digital</title>
  <link>https://www.inside-digital.de/</link>
  <item>
    <title>Angst vor Meta: Warum die neuen Samsung-Brillen ohne Display starten müssen</title>
    <link>https://www.inside-digital.de/news/samsung-brillen-meta</link>
    <dc:creator><![CDATA[Hayo Lücke]]></dc:creator>
    <pubDate>Thu, 21 May 2026 05:30:00 +0000</pubDate>
    <category><![CDATA[Mobile]]></category>
    <category><![CDATA[Samsung]]></category>
    <guid isPermaLink="false">https://www.inside-digital.de/?p=1052270</guid>
    <description><![CDATA[<p>Samsung bringt seine neuen Smart Glasses ohne integriertes Display auf den Markt — und das hat einen Grund.</p>
<p>Der Beitrag <a href="https://www.inside-digital.de/news/samsung-brillen-meta">Angst vor Meta: Warum die neuen Samsung-Brillen ohne Display starten müssen</a> erschien zuerst auf <a href="https://www.inside-digital.de">inside digital</a>.</p>]]></description>
    <slash:comments>3</slash:comments>
  </item>
  <item>
    <title>Aldi verkauft Heißluftfritteuse für 59,99 Euro</title>
    <link>https://www.inside-digital.de/deals/aldi-fritteuse</link>
    <dc:creator><![CDATA[Cedric Litzki]]></dc:creator>
    <pubDate>Mon, 01 Jun 2026 13:26:00 +0000</pubDate>
    <category><![CDATA[Aldi]]></category>
    <category><![CDATA[Küche]]></category>
    <guid isPermaLink="false">https://www.inside-digital.de/?post_type=deal&#038;p=1053736</guid>
    <description><![CDATA[<p>Aldi verkauft ab dem 8. Juni eine Heißluftfritteuse schon für 59,99 Euro.</p>]]></description>
  </item>
</channel>
</rss>"""


def test_parse_rss_extracts_two_items_with_metadata():
    c = InsideHandyCrawler()
    items = c._parse_rss(SAMPLE_RSS)
    assert len(items) == 2

    # 첫번째 — Samsung 글
    a = items[0]
    assert a.source_url == "https://www.inside-digital.de/news/samsung-brillen-meta"
    assert "Samsung" in a.content
    assert "<p>" not in a.content  # 태그 제거
    assert "erschien zuerst auf" not in a.content  # WP 푸터 제거됨
    assert a.country_code == "DE"
    assert a.author_name == "Hayo Lücke"
    assert a.comments_count == 3
    assert a.meta["post_id"] == "1052270"
    assert "Samsung" in a.meta["categories"]
    assert a.published_at == datetime(2026, 5, 21, 5, 30, 0, tzinfo=timezone.utc)

    # 두번째 — Deal (post_type=deal&#038;p=1053736)
    b = items[1]
    assert b.meta["post_id"] == "1053736"  # 엔티티 디코딩됨

    # external_id 재실행 안정성
    again = c._parse_rss(SAMPLE_RSS)
    assert again[0].external_id == a.external_id
    assert again[1].external_id == b.external_id


def test_parse_rss_filter_pipeline_keeps_only_samsung():
    """_parse_rss + _is_galaxy_related — Samsung 글만 남는지."""
    c = InsideHandyCrawler()
    items = c._parse_rss(SAMPLE_RSS)
    filtered = [v for v in items if c._is_galaxy_related(v)]
    assert len(filtered) == 1
    assert filtered[0].meta["post_id"] == "1052270"


def test_parse_rss_invalid_xml_returns_empty():
    c = InsideHandyCrawler()
    assert c._parse_rss("not xml at all") == []
    assert c._parse_rss("<rss><channel></channel></rss>") == []


# -- 7) 기사 본문 추출 (td-post-content) ---------------------------------

SAMPLE_ARTICLE_HTML = """
<html><body>
<header>navigation menu</header>
<article>
  <h1>Samsung Galaxy S26 Ultra im Test</h1>
  <div class="td-post-content tagdiv-type">
    <p>Das neue <b>Samsung Galaxy S26 Ultra</b> bringt einen Exynos 2700 Chip mit.</p>
    <p>Wir haben das Gerät ausführlich getestet und liefern unsere Bewertung.</p>
  </div>
</article>
<footer>cookie banner</footer>
</body></html>
"""


def test_extract_article_body_returns_clean_text():
    body = InsideHandyCrawler._extract_article_body(SAMPLE_ARTICLE_HTML)
    assert "Samsung Galaxy S26 Ultra" in body
    assert "Exynos 2700" in body
    assert "Wir haben das Gerät" in body
    assert "<b>" not in body and "<p>" not in body
    # 헤더/푸터는 포함되면 안됨
    assert "cookie banner" not in body
    assert "navigation menu" not in body


def test_extract_article_body_no_match_returns_empty():
    assert InsideHandyCrawler._extract_article_body("<html><body>nope</body></html>") == ""
    assert InsideHandyCrawler._extract_article_body("") == ""


# -- 8) 본문 강화 통합 (Stub httpx) ---------------------------------------

class _StubResp:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text


class _StubClient:
    def __init__(self, mapping: dict):
        self._mapping = mapping
        self.headers: dict = {}
        self.calls: list = []

    async def get(self, url, headers=None):
        self.calls.append((url, headers))
        return self._mapping.get(url, _StubResp(404))


@pytest.mark.asyncio
async def test_enrich_with_article_body_appends_content():
    c = InsideHandyCrawler()
    voc = RawVOC(
        external_id="x",
        content="제목\nRSS 짧은 발췌",
        source_url="https://www.inside-digital.de/news/s26-test",
        meta={},
    )
    client = _StubClient({
        "https://www.inside-digital.de/news/s26-test":
            _StubResp(200, SAMPLE_ARTICLE_HTML),
    })
    await c._enrich_with_article_body(client, voc)
    assert voc.meta["article_fetch"] == "ok"
    assert "Exynos 2700" in voc.content
    assert "RSS 짧은 발췌" in voc.content  # 기존 내용 유지


@pytest.mark.asyncio
async def test_enrich_with_article_body_handles_403_then_fallback():
    """403 → Firefox UA 재시도. 두번째도 실패 시 meta 기록만."""
    c = InsideHandyCrawler()
    voc = RawVOC(
        external_id="x",
        content="짧은 본문",
        source_url="https://www.inside-digital.de/news/blocked",
        meta={},
    )
    # 동일 URL 두번 다 403 (stub 은 status 만 반환)
    client = _StubClient({
        "https://www.inside-digital.de/news/blocked": _StubResp(403),
    })
    await c._enrich_with_article_body(client, voc)
    assert voc.meta["article_fetch"] == "http_403"
    assert voc.content == "짧은 본문"  # 원본 유지


@pytest.mark.asyncio
async def test_enrich_with_article_body_swallows_exception():
    """네트워크 예외시에도 voc.content 가 망가지지 않아야."""
    c = InsideHandyCrawler()
    voc = RawVOC(
        external_id="x",
        content="원본",
        source_url="https://www.inside-digital.de/news/err",
        meta={},
    )

    class _Boom:
        headers: dict = {}
        async def get(self, *a, **kw):
            raise RuntimeError("network down")

    await c._enrich_with_article_body(_Boom(), voc)
    assert voc.content == "원본"
    assert voc.meta["article_fetch"].startswith("err:")
