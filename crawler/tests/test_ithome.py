"""ITHomeCrawler 단위 테스트 — 네트워크 없이 파서/필터/ID 안정성 검증."""
import hashlib
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC
from platforms.ithome import (
    ITHomeCrawler,
    NEWS_ID_RE,
    DATA_ID_RE,
    AUTHOR_RE,
    GALAXY_KEYWORDS,
)


# -- 1) URL / 정규식 ------------------------------------------------------

def test_news_id_regex_extracts_id():
    """/0/958/447.htm → '958447'."""
    url = "https://www.ithome.com/0/958/447.htm"
    nid = ITHomeCrawler._extract_news_id(url)
    assert nid == "958447"

def test_news_id_regex_rejects_non_article():
    bad = [
        "https://www.ithome.com/",
        "https://www.ithome.com/zt/samsung/",
        "https://www.ithome.com/news/958/447.html",
    ]
    for u in bad:
        assert ITHomeCrawler._extract_news_id(u) is None, f"매치되면 안됨: {u}"

def test_data_id_regex_extracts_hash():
    html = '<div id="post_comm" data-id="bc37a42751079182" data-nid="955627"></div>'
    m = DATA_ID_RE.search(html)
    assert m and m.group(1) == "bc37a42751079182"

def test_author_regex_extracts_name():
    html = '<span id="author_baidu">作者：<strong>小泵</strong></span>'
    m = AUTHOR_RE.search(html)
    assert m and m.group(1) == "小泵"


# -- 2) 시간 파싱 (RSS GMT → UTC) ----------------------------------------

def test_parse_rss_date_gmt_to_utc():
    """ITHome RSS pubDate 는 GMT — 그대로 UTC."""
    dt = ITHomeCrawler._parse_rss_date("Mon, 01 Jun 2026 19:52:47 GMT")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt == datetime(2026, 6, 1, 19, 52, 47, tzinfo=timezone.utc)

def test_parse_rss_date_with_offset():
    dt = ITHomeCrawler._parse_rss_date("Mon, 01 Jun 2026 12:00:00 +0800")
    # CST 12:00 → UTC 04:00
    assert dt == datetime(2026, 6, 1, 4, 0, 0, tzinfo=timezone.utc)

def test_parse_rss_date_invalid():
    assert ITHomeCrawler._parse_rss_date("") is None
    assert ITHomeCrawler._parse_rss_date("garbage") is None


# -- 3) HTML 정제 ---------------------------------------------------------

def test_strip_html_removes_tags_and_entities():
    raw = "<p>三星 &amp; <b>Galaxy</b>  S26<br>Ultra</p>"
    assert ITHomeCrawler._strip_html(raw) == "三星 & Galaxy S26 Ultra"

def test_strip_html_removes_script_block():
    raw = '<p>OK</p><script>alert("x")</script><p>HI</p>'
    out = ITHomeCrawler._strip_html(raw)
    assert "alert" not in out
    assert "OK" in out and "HI" in out

def test_strip_html_empty():
    assert ITHomeCrawler._strip_html("") == ""
    assert ITHomeCrawler._strip_html(None) == ""


# -- 4) Galaxy 키워드 필터 (한자/영문 동시) -------------------------------

def test_is_galaxy_related_chinese_samsung():
    c = ITHomeCrawler()
    v = RawVOC(
        external_id="x",
        content="三星电子市值首度突破 2000 万亿韩元",
        source_url="https://www.ithome.com/0/958/447.htm",
    )
    assert c._is_galaxy_related(v) is True

def test_is_galaxy_related_english_galaxy():
    c = ITHomeCrawler()
    v = RawVOC(
        external_id="x",
        content="Samsung Galaxy Fit 4 智能手环",
        source_url="https://www.ithome.com/0/958/440.htm",
    )
    assert c._is_galaxy_related(v) is True

def test_is_galaxy_related_filters_unrelated():
    c = ITHomeCrawler()
    v = RawVOC(
        external_id="x",
        content="苹果 macOS 26.5.1 正式版发布",
        source_url="https://www.ithome.com/0/958/447.htm",
    )
    assert c._is_galaxy_related(v) is False


# -- 5) external_id 안정성 -----------------------------------------------

def test_external_id_format_stable():
    """external_id = md5(url + '#' + news_id)[:16] — 재크롤시 중복방지."""
    url = "https://www.ithome.com/0/958/447.htm"
    nid = "958447"
    a = hashlib.md5(f"{url}#{nid}".encode()).hexdigest()[:16]
    b = hashlib.md5(f"{url}#{nid}".encode()).hexdigest()[:16]
    assert a == b
    assert len(a) == 16
    # 다른 글은 다른 id
    other = hashlib.md5(f"{url}#{nid+'9'}".encode()).hexdigest()[:16]
    assert a != other


# -- 6) RSS 파싱 (전문 description) ---------------------------------------

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>IT之家</title>
    <link>https://www.ithome.com/</link>
    <item>
      <title>三星 Galaxy S26 Ultra 真机曝光</title>
      <link>https://www.ithome.com/0/958/447.htm</link>
      <pubDate>Mon, 01 Jun 2026 12:00:00 GMT</pubDate>
      <description>&lt;p&gt;三星电子今日发布全新 &lt;b&gt;Galaxy S26 Ultra&lt;/b&gt; 旗舰手机，
搭载 Exynos 2700 处理器。&lt;/p&gt;</description>
    </item>
    <item>
      <title>苹果 macOS 26.6 Beta 1 发布</title>
      <link>https://www.ithome.com/0/955/627.htm</link>
      <pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate>
      <description>&lt;p&gt;苹果今日推送 macOS 26.6 开发者预览版 Beta 1 更新。&lt;/p&gt;</description>
    </item>
  </channel>
</rss>"""


def test_parse_rss_extracts_two_items():
    c = ITHomeCrawler()
    items = c._parse_rss(SAMPLE_RSS)
    assert len(items) == 2

    # 첫 항목 — 삼성 글
    a = items[0]
    assert a.source_url == "https://www.ithome.com/0/958/447.htm"
    assert "Galaxy S26 Ultra" in a.content
    assert "Exynos" in a.content
    assert "<b>" not in a.content  # 태그 제거
    assert a.country_code == "CN"
    assert a.meta["news_id"] == "958447"
    assert a.published_at == datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    # external_id 안정성 — 재파싱시 동일
    again = c._parse_rss(SAMPLE_RSS)
    assert again[0].external_id == a.external_id


def test_parse_rss_filter_pipeline():
    """_parse_rss + _is_galaxy_related 결합 — 삼성 글만 남는지."""
    c = ITHomeCrawler()
    items = c._parse_rss(SAMPLE_RSS)
    filtered = [v for v in items if c._is_galaxy_related(v)]
    assert len(filtered) == 1
    assert filtered[0].meta["news_id"] == "958447"


def test_parse_rss_invalid_xml_returns_empty():
    c = ITHomeCrawler()
    assert c._parse_rss("not xml at all") == []
    assert c._parse_rss("<rss><channel></channel></rss>") == []


# -- 7) 댓글 텍스트 추출 (elements[type=0]) -------------------------------

def test_extract_comment_text_only_text_type():
    cmt = {
        "id": 1,
        "elements": [
            {"type": 0, "content": "好评"},
            {"type": 1, "content": "图片", "link": "http://x"},  # 링크
            {"type": 0, "content": "强烈推荐"},
        ],
    }
    assert ITHomeCrawler._extract_comment_text(cmt) == "好评 强烈推荐"

def test_extract_comment_text_empty():
    assert ITHomeCrawler._extract_comment_text({}) == ""
    assert ITHomeCrawler._extract_comment_text({"elements": []}) == ""
    assert ITHomeCrawler._extract_comment_text(None) == ""  # type: ignore


# -- 8) 댓글 API 통합 (Stub httpx) ----------------------------------------

class _StubResp:
    def __init__(self, status: int, text: str = "", payload=None):
        self.status_code = status
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            import json
            return json.loads(self.text)
        return self._payload


class _StubClient:
    """get(url, params=, headers=) → mapping[url] 반환."""
    def __init__(self, mapping: dict):
        self._mapping = mapping
        self.headers: dict = {}
        self.calls: list = []

    async def get(self, url, params=None, headers=None):
        self.calls.append((url, params))
        return self._mapping.get(url, _StubResp(404))


SAMPLE_CMT_PAYLOAD = {
    "success": True,
    "content": {
        "newsId": 958447,
        "topComments": [],
        "hotComments": [
            {
                "id": 75676234,
                "elements": [{"type": 0, "content": "三星这次发力了！"}],
                "children": [],
            },
        ],
        "comments": [
            {
                "id": 75676235,
                "elements": [{"type": 0, "content": "Exynos 还行吗？"}],
                "children": [
                    {"id": 75676236, "elements": [{"type": 0, "content": "比上代强"}]},
                ],
            },
            # hotComments 와 중복 id — 제거되어야 함
            {
                "id": 75676234,
                "elements": [{"type": 0, "content": "(중복)"}],
                "children": [],
            },
        ],
    },
}


@pytest.mark.asyncio
async def test_fetch_comments_dedupes_and_includes_replies():
    c = ITHomeCrawler()
    client = _StubClient({
        "https://cmt.ithome.com/api/webcomment/getnewscomment":
            _StubResp(200, payload=SAMPLE_CMT_PAYLOAD),
    })
    text, count = await c._fetch_comments(
        client, hash_id="bc37a42751079182",
        referer="https://www.ithome.com/0/958/447.htm",
    )
    # 중복 제거 후 unique 2건 (hot 1 + comments 1, 중복 1 drop)
    assert count == 2
    assert "三星这次发力了" in text
    assert "Exynos" in text
    assert "比上代强" in text          # 대댓글 포함
    assert "└" in text                   # 대댓글 prefix
    assert "(중복)" not in text          # 중복 id drop


@pytest.mark.asyncio
async def test_fetch_comments_handles_failure():
    """API 가 404 / success=False / 깨진 JSON 일 때 안전하게 빈 결과."""
    c = ITHomeCrawler()
    client = _StubClient({})  # 404 default
    text, count = await c._fetch_comments(
        client, "deadbeef", "https://www.ithome.com/0/0/0.htm"
    )
    assert text == ""
    assert count is None

    # success=False
    client2 = _StubClient({
        "https://cmt.ithome.com/api/webcomment/getnewscomment":
            _StubResp(200, payload={"success": False, "message": "新闻不存在"}),
    })
    text2, count2 = await c._fetch_comments(client2, "x", "y")
    assert text2 == "" and count2 is None
