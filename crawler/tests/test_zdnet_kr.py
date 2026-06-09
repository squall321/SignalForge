"""ZDNetKoreaCrawler 단위 테스트 — OG meta 파싱, 키워드 필터, ID 안정성."""
import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC
from platforms.zdnet_kr import (
    ZDNetKoreaCrawler,
    ARTICLE_ID_RE,
    GALAXY_KEYWORD_RE,
    OG_TITLE_RE,
    PUB_TIME_RE,
)


# -- 1) ARTICLE ID 추출 -----------------------------------------------------

def test_article_id_re_matches_view_no():
    html = '<a href="/view/?no=20260605100411">제목</a>'
    ids = ARTICLE_ID_RE.findall(html)
    assert ids == ["20260605100411"]


def test_article_id_re_multiple():
    html = (
        'view/?no=20260605100411 ... view/?no=20260605102138 ... '
        'view/?no=20260605105415'
    )
    ids = ARTICLE_ID_RE.findall(html)
    assert ids == [
        "20260605100411",
        "20260605102138",
        "20260605105415",
    ]


def test_article_id_re_no_match_on_short_id():
    html = 'view/?no=12345'
    ids = ARTICLE_ID_RE.findall(html)
    assert ids == []


# -- 2) Galaxy/Samsung 키워드 필터 (한글+영문) -----------------------------

def test_galaxy_kr_positive_korean():
    c = ZDNetKoreaCrawler()
    v = RawVOC(external_id="x", content="갤럭시 S26 울트라 출시", source_url="u")
    assert c._is_galaxy_related(v)


def test_galaxy_kr_positive_samsung():
    c = ZDNetKoreaCrawler()
    v = RawVOC(external_id="x", content="삼성전자 폴더블폰", source_url="u")
    assert c._is_galaxy_related(v)


def test_galaxy_kr_positive_english():
    c = ZDNetKoreaCrawler()
    v = RawVOC(external_id="x", content="Samsung Galaxy review", source_url="u")
    assert c._is_galaxy_related(v)


def test_galaxy_kr_negative_unrelated():
    c = ZDNetKoreaCrawler()
    v = RawVOC(external_id="x", content="애플 아이폰 17 출시 일정", source_url="u")
    assert not c._is_galaxy_related(v)


# -- 3) OG meta 추출 --------------------------------------------------------

ARTICLE_HTML_SAMPLE = """<!DOCTYPE html>
<html>
<head>
<meta property="og:title" content="삼성 갤럭시 S26 울트라 출시 - ZDNet Korea" />
<meta property="og:description" content="삼성전자가 갤럭시 S26 울트라를 공식 출시했다. 새로운 AI 기능과 향상된 카메라가 특징." />
<meta property="article:published_time" content="2026-06-05T10:32:15+09:00" />
<meta property="article:author" content="홍길동 기자" />
</head>
<body><p>본문</p></body>
</html>"""


def test_parse_article_extracts_og_meta():
    c = ZDNetKoreaCrawler()
    voc = c._parse_article(
        "20260605103215",
        "https://zdnet.co.kr/view/?no=20260605103215",
        ARTICLE_HTML_SAMPLE,
    )
    assert voc is not None
    assert "갤럭시 S26 울트라 출시" in voc.content
    # trailing site 시그니처 제거 확인
    assert "ZDNet Korea" not in voc.content
    # KST → UTC 변환 확인 (10:32 KST = 01:32 UTC)
    assert voc.published_at == datetime(2026, 6, 5, 1, 32, 15, tzinfo=timezone.utc)
    assert voc.author_name == "홍길동 기자"
    assert voc.country_code == "KR"
    assert voc.meta["article_no"] == "20260605103215"


def test_parse_article_external_id_stable():
    c = ZDNetKoreaCrawler()
    v1 = c._parse_article("20260605103215", "u", ARTICLE_HTML_SAMPLE)
    v2 = c._parse_article("20260605103215", "u", ARTICLE_HTML_SAMPLE)
    assert v1.external_id == v2.external_id
    assert len(v1.external_id) == 16


def test_parse_article_short_content_returns_none():
    """본문 짧으면 (< 20자) None."""
    c = ZDNetKoreaCrawler()
    short_html = '<meta property="og:title" content="짧음" />'
    voc = c._parse_article("20260605000000", "u", short_html)
    assert voc is None


def test_parse_article_missing_meta_returns_none():
    c = ZDNetKoreaCrawler()
    voc = c._parse_article("20260605000000", "u", "<html><body>no meta</body></html>")
    assert voc is None


# -- 4) ISO 시간 파싱 -------------------------------------------------------

def test_parse_iso_kst_to_utc():
    c = ZDNetKoreaCrawler()
    dt = c._parse_iso("2026-06-05T10:32:15+09:00")
    assert dt == datetime(2026, 6, 5, 1, 32, 15, tzinfo=timezone.utc)


def test_parse_iso_naive_assumes_kst():
    c = ZDNetKoreaCrawler()
    dt = c._parse_iso("2026-06-05T10:00:00")
    # naive → KST(+9) → UTC = 01:00
    assert dt == datetime(2026, 6, 5, 1, 0, 0, tzinfo=timezone.utc)


def test_parse_iso_invalid_returns_none():
    c = ZDNetKoreaCrawler()
    assert c._parse_iso("") is None
    assert c._parse_iso(None) is None
    assert c._parse_iso("nonsense") is None
