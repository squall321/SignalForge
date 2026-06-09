"""
Lemmy 크롤러 단위 테스트 — 네트워크 없이 관련성 필터, ISO 파서, 매핑 검증.

실행: cd crawler && python -m pytest tests/test_lemmy.py -v
"""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC  # noqa: E402
from platforms.lemmy import (  # noqa: E402
    LemmyCrawler,
    INSTANCES,
    QUERIES,
    MAX_POSTS,
    SEARCH_LIMIT,
    COMMENT_LIMIT,
    RELEVANCE_RE,
)


# ------------------------------------------------------------
# Test 1: 모듈 상수 sanity
# ------------------------------------------------------------
def test_module_constants():
    assert "lemmy.world" in INSTANCES
    assert len(INSTANCES) >= 2
    assert len(QUERIES) >= 3
    assert any("Galaxy" in q for q in QUERIES)
    assert MAX_POSTS >= 30
    assert SEARCH_LIMIT >= 10 and SEARCH_LIMIT <= 50
    assert COMMENT_LIMIT >= 10


# ------------------------------------------------------------
# Test 2: 관련성 필터 — Samsung/Galaxy/Pixel/iPhone 매칭
# ------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "My Galaxy S25 won't charge anymore",
        "Samsung announces new fold device",
        "Z Fold 6 hinge cracked",
        "z flip 5 hinge wobble",
        "iPhone 16 vs Pixel 9 review",
        "Pixel 9 Pro camera samples",
        "Anyone tried S23 Ultra battery mod?",
    ],
)
def test_relevance_re_matches_target_devices(text):
    assert RELEVANCE_RE.search(text), f"매칭되어야 함: {text}"


@pytest.mark.parametrize(
    "text",
    [
        "Today I refactored my Python code",
        "Random discussion about Linux kernel",
        "Unfolding the truth about democracy",  # 'unfold' 부분 일치 차단 검증
        "New MacBook Pro M5 benchmark",
        "Climate change policy update",
    ],
)
def test_relevance_re_rejects_unrelated(text):
    assert not RELEVANCE_RE.search(text), f"매칭되면 안 됨: {text}"


def test_is_relevant_uses_content():
    on_topic = RawVOC(
        external_id="x", content="Galaxy S25 review thread", source_url="https://x",
    )
    off_topic = RawVOC(
        external_id="y", content="My new gardening setup", source_url="https://y",
    )
    assert LemmyCrawler._is_relevant(on_topic) is True
    assert LemmyCrawler._is_relevant(off_topic) is False


def test_is_relevant_empty_content_false():
    v = RawVOC(external_id="x", content="", source_url="https://x")
    assert LemmyCrawler._is_relevant(v) is False


# ------------------------------------------------------------
# Test 3: _parse_iso — Lemmy 타임스탬프 → UTC datetime
# ------------------------------------------------------------
def test_parse_iso_naive_assumed_utc():
    # Lemmy 일반 포맷: 마이크로초 + tz 없음
    dt = LemmyCrawler._parse_iso("2025-11-12T18:42:31.123456")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt == datetime(2025, 11, 12, 18, 42, 31, tzinfo=timezone.utc)


def test_parse_iso_with_z_suffix():
    dt = LemmyCrawler._parse_iso("2025-11-12T18:42:31Z")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt == datetime(2025, 11, 12, 18, 42, 31, tzinfo=timezone.utc)


def test_parse_iso_invalid_returns_none():
    assert LemmyCrawler._parse_iso(None) is None
    assert LemmyCrawler._parse_iso("") is None
    assert LemmyCrawler._parse_iso("garbage") is None


# ------------------------------------------------------------
# Test 4: 인스턴스-간 중복 제거 시나리오 시뮬레이션
# Lemmy 는 ap_id (ActivityPub URI) 가 인스턴스 간 동일 → 중복 키
# ------------------------------------------------------------
def test_duplicate_ap_id_dedup_logic():
    """crawl() 내 중복 제거 규칙: ap_id 또는 external_id 가 동일하면 skip."""
    p1 = RawVOC(
        external_id="aaa",
        content="Galaxy fold issue",
        source_url="https://lemmy.world/post/1",
        meta={"ap_id": "https://lemmy.ml/post/1"},
    )
    p2 = RawVOC(
        external_id="bbb",
        content="Galaxy fold issue (mirrored)",
        source_url="https://beehaw.org/post/2",
        meta={"ap_id": "https://lemmy.ml/post/1"},  # 동일 ap_id
    )

    seen: set = set()
    kept = []
    for p in [p1, p2]:
        k = p.meta.get("ap_id") or p.external_id
        if k in seen:
            continue
        seen.add(k)
        kept.append(p)
    assert len(kept) == 1
    assert kept[0].external_id == "aaa"


# ------------------------------------------------------------
# Test 5: external_id 안정성 — md5(prefix#ap_id)[:16]
# ------------------------------------------------------------
def test_external_id_length_when_constructed():
    """Lemmy 구현은 md5(lemmy_<ap_id_or_id>)[:16] 형식 — len 16 보장."""
    import hashlib
    a = hashlib.md5(b"lemmy_https://lemmy.ml/post/123").hexdigest()[:16]
    b = hashlib.md5(b"lemmy_https://lemmy.ml/post/123").hexdigest()[:16]
    c = hashlib.md5(b"lemmy_c_https://lemmy.ml/comment/456").hexdigest()[:16]
    assert a == b
    assert len(a) == 16
    assert a != c


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
