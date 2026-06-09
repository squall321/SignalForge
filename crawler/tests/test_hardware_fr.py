"""HardwareFRCrawler 단위 테스트 — listing → thread URL 추출, post 파싱, 시간 변환, 키워드 필터."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC
from platforms.hardware_fr import (
    HardwareFRCrawler,
    THREAD_LINK_RE,
    GALAXY_KEYWORDS,
)


# -- 1) listing → Galaxy 스레드 URL 추출 ------------------------------------

LIST_SAMPLE = """
<table>
<tr><td>Galaxy S26/S26+/S26Ultra [T.U.]
<a href="/hfr/gsmgpspda/telephone-android/galaxy-s26-s26ultra-sujet_35363_1.htm">1</a>
<a href="/hfr/gsmgpspda/telephone-android/galaxy-s26-s26ultra-sujet_35363_39.htm">39</a>
</td></tr>
<tr><td>Xperia 1 IX
<a href="/hfr/gsmgpspda/telephone-android/sony-xperia-sujet_35393_1.htm">1</a>
<a href="/hfr/gsmgpspda/telephone-android/sony-xperia-sujet_35393_2.htm">2</a>
</td></tr>
<tr><td>Samsung Galaxy fold / flip [T.U.]
<a href="/hfr/gsmgpspda/telephone-android/samsung-galaxy-fold-flip-sujet_30815_1.htm">1</a>
<a href="/hfr/gsmgpspda/telephone-android/samsung-galaxy-fold-flip-sujet_30815_246.htm">246</a>
</td></tr>
</table>
"""


def test_thread_link_re_captures_topic_and_page():
    matches = THREAD_LINK_RE.findall(LIST_SAMPLE)
    assert len(matches) == 6
    # tuple = (full_path, topic_id, page)
    topic_ids = sorted({m[1] for m in matches})
    assert topic_ids == ["30815", "35363", "35393"]


def test_extract_galaxy_threads_filters_non_samsung():
    """Galaxy/Samsung 키워드 없는 thread(예: Sony Xperia)는 후보에서 제외."""
    threads = HardwareFRCrawler._extract_galaxy_threads(LIST_SAMPLE)
    topic_ids = {t[1] for t in threads}
    assert "35363" in topic_ids  # Galaxy S26
    assert "30815" in topic_ids  # Samsung fold/flip
    assert "35393" not in topic_ids  # Sony Xperia (filtered)


def test_extract_galaxy_threads_picks_highest_page():
    """같은 thread 의 두 링크 중 page 가 큰 쪽이 last_page 로 선택됨."""
    threads = HardwareFRCrawler._extract_galaxy_threads(LIST_SAMPLE)
    by_id = {t[1]: t for t in threads}
    assert by_id["35363"][2] == "39"
    assert by_id["30815"][2] == "246"


# -- 2) thread page URL 페이지 교체 ------------------------------------------

def test_thread_page_url_replaces_page_segment():
    url = "https://forum.hardware.fr/hfr/gsmgpspda/telephone-android/galaxy-s26-s26ultra-sujet_35363_1.htm"
    out = HardwareFRCrawler._thread_page_url(url, 39)
    assert out.endswith("galaxy-s26-s26ultra-sujet_35363_39.htm")


# -- 3) 'Posté le DD-MM-YYYY à HH:MM:SS' → UTC 변환 --------------------------

def test_parse_posted_date_cest_to_utc():
    """6월 = CEST(UTC+2). 18:04 CEST → 16:04 UTC."""
    body = 'Posté le 01-06-2026&nbsp;à&nbsp;18:04:00&nbsp;'
    dt = HardwareFRCrawler._parse_posted_date(body)
    assert dt == datetime(2026, 6, 1, 16, 4, 0, tzinfo=timezone.utc)


def test_parse_posted_date_accent_variations():
    """'Poste' / 'Posté' 둘 다 매치."""
    dt1 = HardwareFRCrawler._parse_posted_date(
        "Poste le 02-06-2026 a 06:42:08"
    )
    assert dt1 == datetime(2026, 6, 2, 4, 42, 8, tzinfo=timezone.utc)


def test_parse_posted_date_invalid_returns_none():
    assert HardwareFRCrawler._parse_posted_date("") is None
    assert HardwareFRCrawler._parse_posted_date("no date here") is None


# -- 4) HTML title → '… - Page : N - <cat> - FORUM HardWare.fr' 정리 -----------

def test_extract_title_strips_trailing_metadata():
    html = (
        "<html><head>"
        "<title>Galaxy S26/S26+/S26Ultra [T.U.] - Page : 39 - "
        "Téléphone Android - Technologies Mobiles - FORUM HardWare.fr</title>"
        "</head></html>"
    )
    title = HardwareFRCrawler._extract_title(html)
    assert title == "Galaxy S26/S26+/S26Ultra [T.U.]"


def test_extract_title_handles_simple_form():
    html = "<title>Samsung Galaxy review - FORUM HardWare.fr</title>"
    assert HardwareFRCrawler._extract_title(html) == "Samsung Galaxy review"


# -- 5) thread page 본문 파싱 (한 게시글) ------------------------------------

THREAD_PAGE_SAMPLE = """
<html><head>
<title>Galaxy S26/S26+/S26Ultra [T.U.] - Page : 39 - Téléphone Android - FORUM HardWare.fr</title>
</head><body>
<table class="messagetable">
<tr>
  <td class="messCase1">
    <a name="t2785846"></a>
    <div class="right"><a href="#t2785846">link</a></div>
    <div><b class="s2">sardhaukar</b></div>
  </td>
  <td class="messCase2">
    <div class="toolbar"><div class="left">
      Posté le 01-06-2026&nbsp;à&nbsp;18:24:15&nbsp;
    </div></div>
    <div id="para2785846">
      <p>Mon Galaxy S26 Ultra a un excellent appareil photo, surtout le mode nuit.
      <br/>Comparé au S25 Ultra c'est un vrai bond.</p>
    </div>
  </td>
</tr>
</table>
</body></html>
"""


def test_parse_thread_page_extracts_one_voc():
    c = HardwareFRCrawler()
    title = c._extract_title(THREAD_PAGE_SAMPLE)
    vocs = c._parse_thread_page(
        THREAD_PAGE_SAMPLE,
        page_url="https://forum.hardware.fr/hfr/gsmgpspda/telephone-android/galaxy-s26-s26ultra-sujet_35363_39.htm",
        topic_id="35363",
        title=title,
    )
    assert len(vocs) == 1
    v = vocs[0]
    assert v.author_name == "sardhaukar"
    assert "Galaxy S26 Ultra" in v.content
    assert "appareil photo" in v.content
    # CEST 18:24:15 → UTC 16:24:15
    assert v.published_at == datetime(2026, 6, 1, 16, 24, 15, tzinfo=timezone.utc)
    assert v.country_code == "FR"
    # external_id 안정성 (16 hex)
    assert len(v.external_id) == 16
    assert v.meta["topic_id"] == "35363"
    assert v.meta["msg_id"] == "2785846"
    # source_url 에 anchor 가 포함되어야 함
    assert v.source_url.endswith("#t2785846")


def test_parse_thread_page_short_body_filtered():
    """본문 20자 미만은 skip."""
    short_html = """
    <table>
      <td class="messCase1"><b class="s2">u</b></td>
      <td class="messCase2">
        <div class="toolbar">Posté le 01-06-2026 à 18:00:00</div>
        <div id="para1">ok</div>
      </td>
    </table>
    """
    c = HardwareFRCrawler()
    vocs = c._parse_thread_page(short_html, page_url="u", topic_id="1", title="t")
    assert vocs == []


# -- 6) 키워드 필터 ----------------------------------------------------------

def test_is_galaxy_related_positive_via_content():
    c = HardwareFRCrawler()
    v = RawVOC(external_id="x", content="Mon Galaxy Fold 6 est superbe", source_url="u")
    assert c._is_galaxy_related(v, None)


def test_is_galaxy_related_positive_via_title():
    """본문에 Samsung 이 없어도 thread title 에 있으면 통과."""
    c = HardwareFRCrawler()
    v = RawVOC(external_id="x", content="J'ai testé hier soir.", source_url="u")
    assert c._is_galaxy_related(v, "Samsung Galaxy S26 [T.U.]")


def test_is_galaxy_related_negative_unrelated():
    c = HardwareFRCrawler()
    v = RawVOC(external_id="x", content="Pixel 9 Pro est génial", source_url="u")
    assert not c._is_galaxy_related(v, "Google Pixel")
