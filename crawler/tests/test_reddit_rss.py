"""Reddit RSS 크롤러 단위 테스트.

3가지 케이스 검증:
  1) parse_post_feed — 정상 Atom feed 에서 t3_ 만 파싱, content_text 추출
  2) parse_comment_feed — t1_ 만 파싱, [deleted]/[removed] 제외
  3) crawl() — mock RSS 응답으로 post + comment RawVOC 통합 흐름 검증

OAuth 키 없이 동작해야 하므로 환경 의존 없음.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms import reddit_rss as rrss  # noqa: E402
from platforms.reddit_rss import (  # noqa: E402
    RedditRSSCrawler,
    parse_comment_feed,
    parse_post_feed,
)


# ---------------------------------------------------------------------------
# Fixtures: 작은 가짜 Atom feed 바이트
# ---------------------------------------------------------------------------

POST_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>r/samsung new</title>
  <entry>
    <id>t3_abc123</id>
    <title>Galaxy S25 Ultra battery drain after June update</title>
    <author><name>/u/testuser</name></author>
    <published>2026-06-01T12:00:00+00:00</published>
    <updated>2026-06-01T12:05:00+00:00</updated>
    <link href="https://www.reddit.com/r/samsung/comments/abc123/galaxy_s25_ultra/" />
    <content type="html">&lt;table&gt;&lt;tr&gt;&lt;td&gt;&lt;a href=&quot;x&quot;&gt;test&lt;/a&gt; submitted by /u/testuser &lt;a&gt;[link]&lt;/a&gt; &lt;a&gt;[comments]&lt;/a&gt;&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;</content>
  </entry>
  <entry>
    <id>t3_def456</id>
    <title>Bixby finally became smart</title>
    <author><name>/u/another</name></author>
    <published>2026-06-02T08:30:00+00:00</published>
    <updated>2026-06-02T08:30:00+00:00</updated>
    <link href="https://www.reddit.com/r/samsung/comments/def456/bixby/" />
    <content type="html">&lt;div&gt;Some body text here&lt;/div&gt;</content>
  </entry>
  <entry>
    <id>not_t3_skip</id>
    <title>Should be skipped</title>
    <link href="https://example.com/" />
    <content type="html">no</content>
  </entry>
</feed>
"""

COMMENT_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Galaxy S25 Ultra battery drain</title>
  <entry>
    <id>t3_abc123</id>
    <title>OP self entry - skip</title>
    <author><name>/u/testuser</name></author>
    <link href="https://www.reddit.com/r/samsung/comments/abc123/" />
    <content type="html">post body</content>
  </entry>
  <entry>
    <id>t1_cmt_keep_1</id>
    <title>/u/commenter1 on battery drain</title>
    <author><name>/u/commenter1</name></author>
    <updated>2026-06-01T13:00:00+00:00</updated>
    <link href="https://www.reddit.com/r/samsung/comments/abc123/_/cmt_keep_1/" />
    <content type="html">&lt;div class=&quot;md&quot;&gt;&lt;p&gt;Mine drops 20% per hour idle.&lt;/p&gt;&lt;/div&gt;</content>
  </entry>
  <entry>
    <id>t1_cmt_deleted</id>
    <title>deleted</title>
    <author><name>[deleted]</name></author>
    <updated>2026-06-01T14:00:00+00:00</updated>
    <link href="https://www.reddit.com/r/samsung/comments/abc123/_/cmt_deleted/" />
    <content type="html">[deleted]</content>
  </entry>
  <entry>
    <id>t1_cmt_keep_2</id>
    <title>/u/commenter2 on battery drain</title>
    <author><name>/u/commenter2</name></author>
    <updated>2026-06-01T15:00:00+00:00</updated>
    <link href="https://www.reddit.com/r/samsung/comments/abc123/_/cmt_keep_2/" />
    <content type="html">&lt;div class=&quot;md&quot;&gt;&lt;p&gt;Same on S24 Ultra here.&lt;/p&gt;&lt;/div&gt;</content>
  </entry>
</feed>
"""


# ---------------------------------------------------------------------------
# 1) parse_post_feed — t3_ 만, content_text 추출
# ---------------------------------------------------------------------------

def test_parse_post_feed_extracts_t3_only():
    posts = parse_post_feed(POST_FEED, "samsung")

    # t3_ 두 개, not_t3_skip 은 제외
    assert len(posts) == 2

    p1 = posts[0]
    assert p1.reddit_id == "t3_abc123"
    assert p1.post_id_raw == "abc123"
    assert p1.subreddit == "samsung"
    assert p1.author == "/u/testuser"
    assert p1.permalink.endswith("/comments/abc123/galaxy_s25_ultra/")
    # 표 마크업 잡음 (submitted by / [link] / [comments]) 가 제거됨
    assert "[link]" not in p1.content_text
    assert "[comments]" not in p1.content_text
    assert "submitted by" not in p1.content_text.lower()
    # title 은 content 에 포함
    assert "Galaxy S25 Ultra battery drain" in p1.content_text
    # published parsing
    assert p1.published is not None
    assert p1.published.year == 2026

    p2 = posts[1]
    assert p2.reddit_id == "t3_def456"
    assert "Bixby" in p2.content_text
    assert "Some body text here" in p2.content_text  # div 본문 보존


# ---------------------------------------------------------------------------
# 2) parse_comment_feed — t1_ 만, [deleted] 제외
# ---------------------------------------------------------------------------

def test_parse_comment_feed_filters_t3_and_deleted():
    parent_url = "https://www.reddit.com/r/samsung/comments/abc123/"
    comments = parse_comment_feed(COMMENT_FEED, parent_url, "samsung")

    # t1 3개 중 [deleted] 제외 → 2개
    assert len(comments) == 2

    c1 = comments[0]
    assert c1.author_name == "/u/commenter1"
    assert "Mine drops 20%" in c1.content
    assert c1.meta["parent_post"] == parent_url
    assert c1.meta["subreddit"] == "samsung"
    assert c1.meta["kind"] == "comment"
    assert c1.meta["reddit_id"] == "t1_cmt_keep_1"
    assert c1.country_code == "US"
    # external_id 는 reddit_id 기반 안정값 (md5 16자)
    assert len(c1.external_id) == 16

    c2 = comments[1]
    assert "Same on S24 Ultra" in c2.content


# ---------------------------------------------------------------------------
# 3) crawl() — mock RSS 응답으로 통합 흐름
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, status: int, body: bytes):
        self.status_code = status
        self.content = body
        self._body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            req = httpx.Request("GET", "https://www.reddit.com/")
            raise httpx.HTTPStatusError(
                f"status {self.status_code}", request=req,
                response=httpx.Response(self.status_code),
            )

    def json(self):
        import json
        return json.loads(self._body.decode())


def test_crawl_integrates_rss_posts_and_comments(monkeypatch):
    # 단일 서브레딧만 사용 + 딜레이 제거
    monkeypatch.setattr(rrss, "SUBREDDITS", ["samsung"])
    monkeypatch.setattr(rrss, "MAX_POSTS_FOR_COMMENTS", 5)

    async def _no_delay(self):
        return None

    monkeypatch.setattr(RedditRSSCrawler, "_random_delay", _no_delay)

    seen_urls = []

    async def fake_get(url, **kwargs):
        seen_urls.append(url)
        if url.endswith("/r/samsung/new.rss"):
            return _FakeResp(200, POST_FEED)
        if "/r/samsung/comments/" in url and url.endswith(".rss"):
            return _FakeResp(200, COMMENT_FEED)
        return _FakeResp(404, b"")

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=fake_get)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    crawler = RedditRSSCrawler()
    monkeypatch.setattr(crawler, "_client", lambda: fake_client)

    result = asyncio.run(crawler.crawl())

    # 2 posts + 2 valid comments per post (2 posts × 2 = 4) = 6
    assert len(result) == 6

    # 첫 항목은 post 자체
    posts = [v for v in result if v.meta.get("kind") == "post"]
    comments = [v for v in result if v.meta.get("kind") == "comment"]
    assert len(posts) == 2
    assert len(comments) == 4

    # 모든 post 의 subreddit = samsung
    assert all(p.meta["subreddit"] == "samsung" for p in posts)

    # 통계 갱신
    assert crawler.stats["rss_posts"] == 2
    assert crawler.stats["rss_comments"] == 4

    # subreddit RSS + 각 post 댓글 RSS 요청 흔적
    assert any(u.endswith("/r/samsung/new.rss") for u in seen_urls)
    assert any(
        u.endswith("/r/samsung/comments/abc123.rss") for u in seen_urls
    )
