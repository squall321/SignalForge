"""Track A — 죽은 사이트 복구 단위 테스트.

목적
====
2026-06-06 R28-harvest Track A 에서 식별/복구한 두 가지 회귀를
정적 검증으로 가드.

1. ``reddit_rss`` 가 ``crawler/tasks.py::_CRAWLER_SPECS`` 에 등록되어
   ``CRAWLER_MAP`` 로딩 시 ``RedditRSSCrawler`` 로 매핑된다.
2. ``reddit_rss`` 가 ``crawler/celery_app.py::beat_schedule`` 에
   ``crawl_platform`` task args 로 등재되어 있다.

테스트 의도
==========
beat_audit 모듈은 pure-function 으로 *집합 입력* 만 검증한다.
실제 운영에서는 reddit_rss 가 tasks.py 의 _CRAWLER_SPECS 누락 →
spec 으로는 안 잡혀 워커가 KeyError 없이 *조용히 무시* 했다.
정적 파싱으로 이런 누락이 다시 들어오지 못하도록 guard.

외부 의존성 (DB/HTTP/redis) 없음 — 텍스트 파싱만.
"""
from __future__ import annotations

import os
import sys
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CRAWLER = os.path.dirname(HERE)
sys.path.insert(0, CRAWLER)


def _read(rel: str) -> str:
    with open(os.path.join(CRAWLER, rel), "r", encoding="utf-8") as f:
        return f.read()


def test_reddit_rss_in_crawler_specs():
    """tasks.py 의 _CRAWLER_SPECS 에 reddit_rss → RedditRSSCrawler 매핑."""
    src = _read("tasks.py")
    # spec 형식: "reddit_rss":     ("platforms.reddit_rss",     "RedditRSSCrawler"),
    m = re.search(
        r'"reddit_rss"\s*:\s*\(\s*"platforms\.reddit_rss"\s*,\s*"RedditRSSCrawler"\s*\)',
        src,
    )
    assert m is not None, "reddit_rss spec 이 tasks.py 의 _CRAWLER_SPECS 에 없음"


def test_reddit_rss_in_beat_schedule():
    """celery_app.py 의 beat_schedule 에 reddit_rss args 로 등재."""
    src = _read("celery_app.py")
    # args 패턴: args":("reddit_rss",None,None)  또는 공백 변형
    m = re.search(
        r'args"?\s*:\s*\(\s*"reddit_rss"\s*,',
        src,
    )
    assert m is not None, "reddit_rss 가 beat_schedule 에 args 로 등록되지 않음"


def test_track_a_dead_sites_registered_in_beat():
    """Discovery 에서 식별한 beat 누락 9건 + reddit_rss 가 모두 beat 에 있는지."""
    src = _read("celery_app.py")
    expected = [
        "gigazine", "gsmchoice", "hipertextual", "inside_handy", "ithome",
        "mobile_review", "mysmartprice", "sammobile", "sammyfans",
        "reddit_rss",
    ]
    missing = [code for code in expected
               if not re.search(rf'args"?\s*:\s*\(\s*"{re.escape(code)}"\s*,', src)]
    assert not missing, f"beat_schedule 에 누락된 코드: {missing}"
