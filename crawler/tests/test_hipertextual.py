"""HipertextualCrawler 단위 테스트 — 네트워크 없이 파서/필터/ID 안정성 검증."""
import hashlib
import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC
from platforms.hipertextual import (
    HipertextualCrawler,
    GALAXY_KEYWORDS,
    NEGATIVE_HINTS,
    CEST,
)


# -- 1) HTML 정제 (스페인어 entity + WP block) ---------------------------

def test_strip_html_removes_tags_and_entities():
    raw = '<p class="wp-block-paragraph">Samsung &amp; <strong>Galaxy</strong>  S26<br>Ultra</p>'
    assert HipertextualCrawler._strip_html(raw) == "Samsung & Galaxy S26 Ultra"

def test_strip_html_removes_script_block():
    raw = '<p>OK</p><script>alert("x")</script><p>HI</p>'
    out = HipertextualCrawler._strip_html(raw)
    assert "alert" not in out
    assert "OK" in out and "HI" in out

def test_strip_html_handles_spanish_entities():
    raw = "&iquest;Tienes uno de estos Samsung Galaxy ic&oacute;nicos?"
    out = HipertextualCrawler._strip_html(raw)
    # html.unescape 가 ¿ ó 처리
    assert "Samsung Galaxy" in out
    assert "iquest" not in out

def test_strip_html_empty():
    assert HipertextualCrawler._strip_html("") == ""
    assert HipertextualCrawler._strip_html(None) == ""


# -- 2) 시간 파싱 (WP date_gmt UTC + RSS RFC822) -------------------------

def test_parse_wp_dt_naive_utc():
    """date_gmt '2026-06-01T19:18:56' → UTC 그대로."""
    dt = HipertextualCrawler._parse_wp_dt("2026-06-01T19:18:56", naive_is_utc=True)
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt == datetime(2026, 6, 1, 19, 18, 56, tzinfo=timezone.utc)

def test_parse_wp_dt_naive_cest_to_utc():
    """date '2026-06-01T18:00:00' (CEST naive) → 16:00 UTC."""
    dt = HipertextualCrawler._parse_wp_dt("2026-06-01T18:00:00", naive_is_utc=False)
    assert dt is not None
    # CEST = UTC+2, 18:00 CEST → 16:00 UTC
    assert dt == datetime(2026, 6, 1, 16, 0, 0, tzinfo=timezone.utc)

def test_parse_wp_dt_invalid_returns_none():
    assert HipertextualCrawler._parse_wp_dt(None, naive_is_utc=True) is None
    assert HipertextualCrawler._parse_wp_dt("", naive_is_utc=True) is None
    assert HipertextualCrawler._parse_wp_dt("not a date", naive_is_utc=True) is None

def test_parse_rss_date_with_explicit_offset():
    """'Mon, 01 Jun 2026 19:18:56 +0000' → UTC."""
    dt = HipertextualCrawler._parse_rss_date("Mon, 01 Jun 2026 19:18:56 +0000")
    assert dt is not None
    assert dt == datetime(2026, 6, 1, 19, 18, 56, tzinfo=timezone.utc)

def test_parse_rss_date_naive_assumes_cest():
    """tz 없는 RSS 문자열은 CEST 가정 → -2h."""
    dt = HipertextualCrawler._parse_rss_date("Mon, 01 Jun 2026 18:00:00")
    assert dt is not None
    # 18:00 CEST == 16:00 UTC
    assert dt.hour == 16 and dt.minute == 0


# -- 3) Galaxy 키워드 필터 (오탐 컷 포함) --------------------------------

def test_is_galaxy_related_hits_samsung():
    c = HipertextualCrawler()
    v = RawVOC(
        external_id="x",
        content="Samsung anuncia los nuevos Galaxy S26 con One UI 8.5",
        source_url="https://hipertextual.com/mobile/foo",
    )
    assert c._is_galaxy_related(v) is True

def test_is_galaxy_related_hits_via_rss_category():
    """본문에 키워드가 없어도 RSS category 에 Samsung 이 있으면 통과."""
    c = HipertextualCrawler()
    v = RawVOC(
        external_id="x",
        content="Un teléfono plegable revolucionario llegará pronto al mercado",
        source_url="https://hipertextual.com/mobile/foo",
        meta={"categories_rss": ["Samsung", "Móviles"]},
    )
    assert c._is_galaxy_related(v) is True

def test_is_galaxy_related_filters_unrelated_iphone():
    c = HipertextualCrawler()
    v = RawVOC(
        external_id="x",
        content="iPhone 18 review: lo nuevo de Apple este año",
        source_url="https://hipertextual.com/apple/foo",
        meta={"categories_rss": ["Apple"]},
    )
    assert c._is_galaxy_related(v) is False

def test_is_galaxy_related_negative_hint_super_mario_galaxy():
    """'Super Mario Galaxy' 는 Samsung 키워드 없으면 컷."""
    c = HipertextualCrawler()
    v = RawVOC(
        external_id="x",
        content="Super Mario Galaxy: La película llega con muchas sorpresas",
        source_url="https://hipertextual.com/cine/foo",
    )
    assert c._is_galaxy_related(v) is False

def test_is_galaxy_related_negative_hint_passes_if_samsung_present():
    """그러나 'Samsung' 도 있으면 통과 (드문 케이스)."""
    c = HipertextualCrawler()
    v = RawVOC(
        external_id="x",
        content="Super Mario Galaxy aparece en el nuevo Samsung Galaxy Tab",
        source_url="https://hipertextual.com/foo",
    )
    assert c._is_galaxy_related(v) is True


# -- 4) external_id 안정성 -----------------------------------------------

def test_external_id_post_format_stable():
    """본문 external_id 는 md5(link + '#post')[:16] — 재크롤 시 동일."""
    link = "https://hipertextual.com/mobile/galaxy-s26-review"
    a = hashlib.md5(f"{link}#post".encode("utf-8")).hexdigest()[:16]
    b = hashlib.md5(f"{link}#post".encode("utf-8")).hexdigest()[:16]
    assert a == b
    assert len(a) == 16

def test_external_id_different_for_different_links():
    a = hashlib.md5(
        "https://hipertextual.com/mobile/foo#post".encode()
    ).hexdigest()[:16]
    b = hashlib.md5(
        "https://hipertextual.com/mobile/bar#post".encode()
    ).hexdigest()[:16]
    assert a != b


# -- 5) WP REST 포스트 → RawVOC 파싱 ------------------------------------

def test_parse_post_minimal_fields():
    """date_gmt 우선, RSS aux 에서 저자 보강."""
    c = HipertextualCrawler()
    link = "https://hipertextual.com/mobile/samsung-galaxy-s26"
    post = {
        "id": 1890451,
        "date_gmt": "2026-06-01T19:18:56",
        "date": "2026-06-01T21:18:56",
        "link": link,
        "title": {"rendered": "Samsung Galaxy S26 review"},
        "content": {"rendered": "<p>El nuevo <b>Galaxy S26</b> de Samsung llega con grandes mejoras.</p>"},
        "excerpt": {"rendered": ""},
        "categories": [11378],
        "comment_status": "closed",
    }
    rss_aux = {
        link: ("Luis Miranda", ["Móviles", "Samsung"], "Mon, 01 Jun 2026 19:18:56 +0000"),
    }
    voc = c._parse_post(post, rss_aux)
    assert voc is not None
    assert voc.source_url == link
    assert "Galaxy S26" in voc.content
    assert "Samsung" in voc.content
    assert voc.author_name == "Luis Miranda"
    assert voc.country_code == "ES"
    # date_gmt naive → UTC
    assert voc.published_at == datetime(2026, 6, 1, 19, 18, 56, tzinfo=timezone.utc)
    assert voc.meta["post_id"] == 1890451
    assert "Samsung" in voc.meta["categories_rss"]
    assert voc.meta["source"] == "wp_rest"
    # external_id 안정성
    expected = hashlib.md5(f"{link}#post".encode()).hexdigest()[:16]
    assert voc.external_id == expected

def test_parse_post_no_rss_aux_no_author():
    """RSS 에 없는 글이면 author=None 이지만 정상 파싱."""
    c = HipertextualCrawler()
    link = "https://hipertextual.com/mobile/old-galaxy-fold"
    post = {
        "id": 100,
        "date_gmt": "2026-05-01T10:00:00",
        "link": link,
        "title": {"rendered": "Galaxy Fold 5 sigue siendo relevante"},
        "content": {"rendered": "<p>Aunque ya tiene un año, el Galaxy Fold 5 de Samsung sigue siendo una buena opción para muchos usuarios.</p>"},
    }
    voc = c._parse_post(post, {})
    assert voc is not None
    assert voc.author_name is None
    assert voc.meta["categories_rss"] == []

def test_parse_post_too_short_returns_none():
    """20자 미만 본문은 컷."""
    c = HipertextualCrawler()
    post = {
        "id": 1,
        "date_gmt": "2026-06-01T00:00:00",
        "link": "https://hipertextual.com/x",
        "title": {"rendered": "X"},
        "content": {"rendered": ""},
        "excerpt": {"rendered": ""},
    }
    assert c._parse_post(post, {}) is None

def test_parse_post_missing_id_returns_none():
    c = HipertextualCrawler()
    post = {"link": "https://hipertextual.com/x"}
    assert c._parse_post(post, {}) is None


# -- 6) RSS 인덱스 파서 --------------------------------------------------

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
<channel>
  <title>Hipertextual</title>
  <item>
    <title>Samsung Galaxy S26 review</title>
    <link>https://hipertextual.com/mobile/samsung-galaxy-s26</link>
    <dc:creator>Luis Miranda</dc:creator>
    <pubDate>Mon, 01 Jun 2026 19:18:56 +0000</pubDate>
    <category>Móviles</category>
    <category>Samsung</category>
  </item>
  <item>
    <title>iPhone 18 filtración</title>
    <link>https://hipertextual.com/apple/iphone-18</link>
    <dc:creator>Gabriel Erard</dc:creator>
    <pubDate>Mon, 01 Jun 2026 17:00:00 +0000</pubDate>
    <category>Apple</category>
  </item>
</channel>
</rss>
"""

def test_parse_rss_index_extracts_creator_and_categories():
    idx = HipertextualCrawler._parse_rss_index(SAMPLE_RSS)
    assert len(idx) == 2
    link = "https://hipertextual.com/mobile/samsung-galaxy-s26"
    assert link in idx
    creator, cats, pub = idx[link]
    assert creator == "Luis Miranda"
    assert "Samsung" in cats and "Móviles" in cats
    assert "Jun 2026" in pub

def test_parse_rss_index_malformed_returns_empty():
    """잘못된 XML 은 빈 dict 반환 (raise 없음)."""
    assert HipertextualCrawler._parse_rss_index("<not xml>>") == {}


# -- 7) RSS-only 폴백 ----------------------------------------------------

def test_rss_to_voc_uses_slug_as_content():
    c = HipertextualCrawler()
    link = "https://hipertextual.com/mobile/samsung-galaxy-s26-review"
    voc = c._rss_to_voc(
        link, "Luis Miranda", ["Samsung", "Móviles"],
        "Mon, 01 Jun 2026 19:18:56 +0000",
    )
    assert voc is not None
    # slug 가 content 가 됨
    assert "samsung galaxy s26 review" in voc.content
    assert voc.author_name == "Luis Miranda"
    assert voc.country_code == "ES"
    assert voc.meta["source"] == "rss_fallback"
    # 키워드 매칭도 통과해야 함 (slug 에 samsung 포함)
    assert c._is_galaxy_related(voc) is True
    # external_id 가 _parse_post 와 동일 포맷 → 충돌 방지
    expected = hashlib.md5(f"{link}#post".encode()).hexdigest()[:16]
    assert voc.external_id == expected


# -- 8) crawler 초기화/정합성 --------------------------------------------

def test_crawler_initialization():
    c = HipertextualCrawler()
    assert c.platform_code == "hipertextual"
    assert c.MIN_DELAY == 1.5
    assert c.MAX_DELAY == 3.0

def test_galaxy_keywords_contain_core_terms():
    assert "samsung" in GALAXY_KEYWORDS
    assert "galaxy" in GALAXY_KEYWORDS
    assert "one ui" in GALAXY_KEYWORDS
