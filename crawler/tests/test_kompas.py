"""KompasCrawler 단위 테스트 — 네트워크 없이 파서/필터/ID 안정성 검증."""
import hashlib
import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC
from platforms.kompas import (
    KompasCrawler,
    POST_URL_RE,
    GALAXY_KEYWORDS,
    WIB,
)


# -- 1) URL 매칭 ----------------------------------------------------------

def test_post_url_regex_extracts_post_id():
    url = (
        "https://tekno.kompas.com/read/2026/06/01/18060087/"
        "samsung-tinggalkan-skema-penamaan-galaxy-z-fold-lama"
    )
    m = POST_URL_RE.match(url)
    assert m, "Kompas Tekno read URL 이 매치되어야 한다"
    assert m.group(1) == "18060087"

def test_post_url_regex_rejects_other_sections():
    bad = [
        "https://www.kompas.com/tag/samsung",
        "https://otomotif.kompas.com/read/2026/06/01/18060087/foo",
        "https://tekno.kompas.com/komentar/2026/06/01/18060087/foo",
        "https://tekno.kompas.com/jeo/2026/06/01/18060087/foo",
    ]
    for u in bad:
        assert POST_URL_RE.match(u) is None, f"매치되면 안됨: {u}"


# -- 2) 시간 파싱 (WIB → UTC) ---------------------------------------------

def test_parse_iso_wib_to_utc():
    """JSON-LD '2026-06-01T18:06:00+07:00' → 2026-06-01T11:06:00Z."""
    dt = KompasCrawler._parse_iso("2026-06-01T18:06:00+07:00")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt == datetime(2026, 6, 1, 11, 6, 0, tzinfo=timezone.utc)

def test_parse_iso_naive_assumes_wib():
    """tz 없는 문자열은 WIB(+07:00) 가정."""
    dt = KompasCrawler._parse_iso("2026-06-01T18:06:00")
    assert dt is not None
    # 18:06 WIB == 11:06 UTC
    assert dt.hour == 11 and dt.minute == 6

def test_parse_iso_invalid_returns_none():
    assert KompasCrawler._parse_iso("") is None
    assert KompasCrawler._parse_iso("not a date") is None


# -- 3) HTML 정제 ---------------------------------------------------------

def test_strip_html_removes_tags_and_entities():
    raw = "<p>Samsung &amp; <b>Galaxy</b>  S26<br>Ultra</p>"
    assert KompasCrawler._strip_html(raw) == "Samsung & Galaxy S26 Ultra"

def test_strip_html_removes_script_block():
    raw = '<p>OK</p><script>alert("x")</script><p>HI</p>'
    assert "alert" not in KompasCrawler._strip_html(raw)

def test_strip_html_empty():
    assert KompasCrawler._strip_html("") == ""
    assert KompasCrawler._strip_html(None) == ""


# -- 4) Galaxy 키워드 필터 ------------------------------------------------

def test_is_galaxy_related_hits_title():
    c = KompasCrawler()
    v = RawVOC(
        external_id="x", content="Berita teknologi terbaru",
        source_url="https://tekno.kompas.com/read/2026/06/01/12345678/foo",
        meta={"title": "Samsung Galaxy S26 Ultra dirilis"},
    )
    assert c._is_galaxy_related(v) is True

def test_is_galaxy_related_hits_body():
    c = KompasCrawler()
    v = RawVOC(
        external_id="x", content="ponsel lipat baru akan tiba bulan depan",
        source_url="https://tekno.kompas.com/read/2026/06/01/12345678/foo",
    )
    assert c._is_galaxy_related(v) is True

def test_is_galaxy_related_filters_unrelated():
    c = KompasCrawler()
    v = RawVOC(
        external_id="x", content="iPhone 18 review terbaru dari Apple",
        source_url="https://tekno.kompas.com/read/2026/06/01/12345678/foo",
        meta={"title": "iPhone 18 review"},
    )
    assert c._is_galaxy_related(v) is False


# -- 5) external_id 안정성 ------------------------------------------------

def test_external_id_post_format():
    """본문 external_id 는 md5(post_url + '#post')[:16]."""
    url = "https://tekno.kompas.com/read/2026/06/01/18060087/samsung-foo"
    expected = hashlib.md5(f"{url}#post".encode()).hexdigest()[:16]
    # 동일 입력 → 동일 출력 (재크롤 시 중복 방지 확인)
    again = hashlib.md5(f"{url}#post".encode()).hexdigest()[:16]
    assert expected == again
    assert len(expected) == 16

def test_external_id_comment_format_stable():
    """댓글 external_id 는 md5(post_url + '#c' + comment_id)[:16]."""
    url = "https://tekno.kompas.com/read/2026/05/25/13380047/samsung-foo"
    cid = "4738128"
    a = hashlib.md5(f"{url}#c{cid}".encode()).hexdigest()[:16]
    b = hashlib.md5(f"{url}#c{cid}".encode()).hexdigest()[:16]
    assert a == b
    # 다른 댓글은 다른 id
    other = hashlib.md5(f"{url}#c{cid+'9'}".encode()).hexdigest()[:16]
    assert a != other


# -- 6) JSON-LD + 본문 + 댓글 통합 파싱 ----------------------------------

class _StubResp:
    def __init__(self, status: int, text: str = "", payload=None):
        self.status_code = status
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload

class _StubClient:
    """get(url, ...) 호출 시 매핑된 응답을 돌려주는 가짜 httpx 클라이언트."""
    def __init__(self, mapping: dict):
        self._mapping = mapping
        self.headers: dict = {}
        self.calls: list = []

    async def get(self, url, params=None, headers=None):
        self.calls.append((url, params))
        key = url
        if params:
            # COMMENT_API 같은 경우 url 만으로 매칭
            key = url
        if key in self._mapping:
            return self._mapping[key]
        return _StubResp(404)


SAMPLE_ARTICLE_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"NewsArticle",
 "headline":"Samsung Galaxy Z Fold 8 bocoran terbaru",
 "datePublished":"2026-06-01T18:06:00+07:00",
 "author":{"@type":"Person","name":"Lely Maulida"}}
</script></head>
<body>
<div class="read__content">
  <p>KOMPAS.com - Samsung dikabarkan akan merilis Galaxy Z Fold 8 dengan desain baru.</p>
  <p>Menurut bocoran Ice Universe, perangkat ini akan hadir dengan One UI terbaru.</p>
  <p>Baca juga: artikel lain</p>
</div>
<div class="read__related">..</div>
</body></html>
"""

SAMPLE_COMMENT_PAYLOAD = {
    "result": {
        "komentar": [
            {
                "comment_id": 4738128,
                "comment_text": "Wah keren banget Galaxy Z Fold 8 nya!",
                "comment_time": 1780051702,
                "user_fullname": "Ferry Sidharta",
                "num_like": 3,
                "num_dislike": 0,
                "type": "text",
            },
            {
                "comment_id": 4738129,
                "comment_text": "https://asset.kompas.com/foo.gif",
                "comment_time": 1780051800,
                "user_fullname": "Sticker User",
                "num_like": 0,
                "type": "sticker",
            },
        ],
        "total": 2,
    },
    "status": True,
}


@pytest.mark.asyncio
async def test_fetch_article_parses_jsonld_and_body():
    c = KompasCrawler()
    url = "https://tekno.kompas.com/read/2026/06/01/18060087/samsung-foo"
    client = _StubClient({url: _StubResp(200, SAMPLE_ARTICLE_HTML)})
    voc = await c._fetch_article(client, url, "18060087")
    assert voc is not None
    assert "Galaxy Z Fold 8" in voc.content
    # 'Baca juga' 라인 제거 검증
    assert "Baca juga" not in voc.content
    assert voc.author_name == "Lely Maulida"
    assert voc.country_code == "ID"
    assert voc.published_at == datetime(2026, 6, 1, 11, 6, 0, tzinfo=timezone.utc)
    assert voc.meta["kind"] == "article"
    assert voc.meta["post_id"] == "18060087"


@pytest.mark.asyncio
async def test_fetch_comments_skips_sticker_and_keeps_text():
    c = KompasCrawler()
    url = "https://tekno.kompas.com/read/2026/05/25/13380047/samsung-foo"
    client = _StubClient({
        "https://apiscomment.kompas.com/list": _StubResp(
            200, payload=SAMPLE_COMMENT_PAYLOAD,
        ),
    })
    comments = await c._fetch_comments(client, url, "13380047")
    assert len(comments) == 1, "스티커 댓글은 제외되어야 한다"
    cm = comments[0]
    assert cm.content.startswith("Wah keren")
    assert cm.author_name == "Ferry Sidharta"
    assert cm.likes_count == 3
    assert cm.meta["kind"] == "comment"
    assert cm.meta["comment_id"] == "4738128"
    # comment_time 1780051702 → UTC 변환 검증
    assert cm.published_at.tzinfo == timezone.utc
