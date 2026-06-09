"""
Stack Exchange (android.stackexchange.com) 크롤러 단위 테스트 — 네트워크 없이
mapping, ID 안정성, HTML 정제, 상수 sanity 검증.

실행: cd crawler && python -m pytest tests/test_stackexchange.py -v
"""
import hashlib
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC  # noqa: E402
from platforms.stackexchange import (  # noqa: E402
    StackExchangeCrawler,
    QUERY_TERMS,
    MAX_QUESTIONS,
    PAGESIZE,
    SE_BASE,
    SE_SITE,
    SE_QUESTION_URL,
)


# ------------------------------------------------------------
# Test 1: 모듈 상수 sanity — 외부 API 의존하므로 핵심 키만 검증
# ------------------------------------------------------------
def test_module_constants():
    assert SE_BASE.startswith("https://api.stackexchange.com")
    assert SE_SITE == "android"
    assert SE_QUESTION_URL.endswith("/questions")
    assert MAX_QUESTIONS >= 20  # 상세 수집 큐 깊이
    assert PAGESIZE >= 10 and PAGESIZE <= 100  # SE API 1~100 범위
    assert len(QUERY_TERMS) >= 3
    assert any("Galaxy" in t for t in QUERY_TERMS)


# ------------------------------------------------------------
# Test 2: _strip_html — HTML 태그 제거 + 공백 정규화
# ------------------------------------------------------------
def test_strip_html_removes_tags():
    html = "<p>Hello <strong>Galaxy</strong> users.</p>"
    out = StackExchangeCrawler._strip_html(html)
    assert "<" not in out and ">" not in out
    assert "Galaxy" in out
    assert "Hello" in out


def test_strip_html_empty_returns_empty():
    assert StackExchangeCrawler._strip_html("") == ""
    assert StackExchangeCrawler._strip_html(None) == ""


# ------------------------------------------------------------
# Test 3: _owner_name — owner.display_name 추출 + 누락 시 None
# ------------------------------------------------------------
def test_owner_name_extracted_when_present():
    assert StackExchangeCrawler._owner_name(
        {"owner": {"display_name": "Bob"}}
    ) == "Bob"


def test_owner_name_missing_returns_none():
    assert StackExchangeCrawler._owner_name({}) is None
    assert StackExchangeCrawler._owner_name({"owner": {}}) is None


# ------------------------------------------------------------
# Test 4: _ts_to_dt — Unix epoch → UTC datetime
# ------------------------------------------------------------
def test_ts_to_dt_unix_epoch_to_utc():
    # 1700000000 = 2023-11-14 22:13:20 UTC
    dt = StackExchangeCrawler._ts_to_dt(1700000000)
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_ts_to_dt_none_or_invalid():
    assert StackExchangeCrawler._ts_to_dt(None) is None
    assert StackExchangeCrawler._ts_to_dt(0) is None  # falsy guard
    assert StackExchangeCrawler._ts_to_dt("nope") is None


# ------------------------------------------------------------
# Test 5: _question_to_voc — 정상 매핑 (제목+본문 결합, country=US, meta)
# ------------------------------------------------------------
def test_question_to_voc_full_mapping():
    c = StackExchangeCrawler()
    item = {
        "question_id": 12345,
        "title": "Galaxy S25 battery drain after One UI 7 update",
        "body_markdown": "After updating to One UI 7 my S25 drains 30% in 6 hours.",
        "link": "https://android.stackexchange.com/questions/12345/x",
        "owner": {"display_name": "alice"},
        "creation_date": 1700000000,
        "score": 8,
        "answer_count": 3,
        "tags": ["samsung-galaxy", "battery"],
    }
    v = c._question_to_voc(item)
    assert v is not None
    assert "Galaxy S25" in v.content
    assert "One UI 7" in v.content
    assert v.source_url == "https://android.stackexchange.com/questions/12345/x"
    assert v.author_name == "alice"
    assert v.likes_count == 8
    assert v.comments_count == 3
    assert v.country_code == "US"
    assert v.meta["se_kind"] == "question"
    assert v.meta["qid"] == 12345
    assert "samsung-galaxy" in v.meta["tags"]
    # external_id 안정성
    expect = hashlib.md5(b"se_q_12345").hexdigest()[:16]
    assert v.external_id == expect
    assert len(v.external_id) == 16


def test_question_to_voc_missing_qid_returns_none():
    c = StackExchangeCrawler()
    assert c._question_to_voc({"title": "no qid"}) is None


def test_question_to_voc_only_title_no_body():
    """body 누락이면 title 만으로 content 구성"""
    c = StackExchangeCrawler()
    v = c._question_to_voc({
        "question_id": 9,
        "title": "Galaxy Fold hinge wobble",
    })
    assert v is not None
    assert v.content == "Galaxy Fold hinge wobble"
    # link 누락 → 기본 URL 합성
    assert v.source_url == f"{SE_QUESTION_URL}/9"


def test_question_to_voc_empty_content_returns_none():
    c = StackExchangeCrawler()
    assert c._question_to_voc({"question_id": 1, "title": "", "body_markdown": ""}) is None


# ------------------------------------------------------------
# Test 6: _answer_to_voc / _comment_to_voc — kind 구분 + anchor URL
# ------------------------------------------------------------
def test_answer_to_voc_anchor_url_and_kind():
    c = StackExchangeCrawler()
    v = c._answer_to_voc(
        {
            "answer_id": 999,
            "body_markdown": "Try a factory reset first.",
            "owner": {"display_name": "bob"},
            "creation_date": 1700000100,
            "score": 5,
        },
        qid=42,
    )
    assert v is not None
    assert v.content == "Try a factory reset first."
    assert v.source_url == f"{SE_QUESTION_URL}/42#999"
    assert v.meta["se_kind"] == "answer"
    assert v.meta["qid"] == 42 and v.meta["aid"] == 999
    assert v.country_code == "US"
    assert v.external_id == hashlib.md5(b"se_a_999").hexdigest()[:16]


def test_comment_to_voc_anchor_url_and_kind():
    c = StackExchangeCrawler()
    v = c._comment_to_voc(
        {"comment_id": 7, "body_markdown": "same here", "score": 2},
        qid=42,
    )
    assert v is not None
    assert v.source_url == f"{SE_QUESTION_URL}/42#comment7"
    assert v.meta["se_kind"] == "comment"
    assert v.meta["cid"] == 7
    assert v.external_id == hashlib.md5(b"se_c_7").hexdigest()[:16]


def test_answer_and_comment_drop_empty_body():
    c = StackExchangeCrawler()
    assert c._answer_to_voc({"answer_id": 1, "body_markdown": "   "}, qid=1) is None
    assert c._comment_to_voc({"comment_id": 1, "body_markdown": ""}, qid=1) is None


# ------------------------------------------------------------
# Test 7: HTML fallback — body_markdown 없으면 body(HTML) 태그 제거
# ------------------------------------------------------------
def test_question_uses_html_body_when_markdown_missing():
    c = StackExchangeCrawler()
    v = c._question_to_voc({
        "question_id": 100,
        "title": "Issue",
        "body": "<p>I see <em>random</em> reboots</p>",
    })
    assert v is not None
    assert "<" not in v.content and ">" not in v.content
    assert "random reboots" in v.content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
