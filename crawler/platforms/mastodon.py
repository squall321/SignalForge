"""Mastodon (fediverse) 크롤러 — 공개 hashtag timeline API 사용.

배경
====
fediverse 의 Mastodon 은 분산 SNS 로, 인스턴스마다 public hashtag
timeline 을 인증 없이 조회할 수 있다.  rate limit 도 익명 클라이언트
기준 300 req/5분 (mastodon.social 기준)으로 관대하다.

- Bluesky 와 함께 X.com/Twitter 무료 대안 보조 채널.
- mastodon.social 외에도 fosstodon.org / mastodon.world 등 다중
  인스턴스를 fan-out 하여 같은 태그로 폭넓게 채집한다.

API
===
GET https://<instance>/api/v1/timelines/tag/<tag>?limit=40
응답 (요약):
  [
    {
      "id": "112345...",
      "created_at": "2026-06-09T10:00:00.000Z",
      "uri": "https://mastodon.social/users/foo/statuses/112345",
      "url": "https://mastodon.social/@foo/112345",
      "content": "<p>Just bought a Galaxy S25 ...</p>",
      "language": "en",
      "replies_count": 1,
      "reblogs_count": 2,
      "favourites_count": 5,
      "account": {"acct": "foo@mastodon.social", "display_name": "Foo"}
    },
    ...
  ]

본문은 HTML 이므로 reddit_rss._html_to_text 와 동일한 패턴으로 평문 변환.

키 의존성
=========
없음.  익명 GET 만으로 작동.  UA 헤더만 SignalForge 식별자로 고정.
MX 키워드 필터 (is_mx_relevant) 가 후단에서 자동 적용된다.

플랫폼 코드: mastodon  (DB platforms row 등록 필요).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC  # noqa: E402

logger = logging.getLogger(__name__)


# fan-out 인스턴스 — 영문권 활동량 기준 상위 3개 (galaxy/samsung 태그 보유 확인).
INSTANCES: List[str] = [
    "mastodon.social",
    "mastodon.world",
    "fosstodon.org",
]

# 추적 태그 — bluesky QUERY_TERMS 와 정렬 (단, 태그는 공백 없는 단일 토큰).
# "galaxys25" / "galaxyfold" 등 공식 태그 변형도 동시에 잡는다.
TAGS: List[str] = [
    "galaxy",
    "samsung",
    "galaxys25",
    "galaxyfold",
    "pixel9",
    "iphone16",
]

USER_AGENT = "SignalForge/1.0 Mastodon collector"
# instance/tag 당 fetch 상한 (mastodon API limit 최댓값 = 40).
PER_TAG_LIMIT = 40
# 전체 누적 상한 — NLP 단계 부하 방지.  bluesky 와 동급으로 보수.
MAX_POSTS = 240


def _html_to_text(html: str) -> str:
    """status.content HTML → 평문.  bs4 우선, 없으면 정규식 폴백."""
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup  # type: ignore
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        return re.sub(r"\s+", " ", text).strip()
    except ImportError:
        no_tags = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", no_tags).strip()


def _parse_iso(dt: Optional[str]) -> Optional[datetime]:
    """ISO8601 (Z 포함) → datetime(aware utc)."""
    if not dt:
        return None
    s = dt.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except ValueError:
        return None


def status_to_rawvoc(status: dict, instance: str, tag: str) -> Optional[RawVOC]:
    """Mastodon status 응답 → RawVOC.  필수 필드 결손 시 None."""
    sid = status.get("id")
    if not sid:
        return None
    raw_html = status.get("content") or ""
    text = _html_to_text(raw_html)
    if not text:
        return None

    url = status.get("url") or status.get("uri") or ""
    if not url:
        return None

    account = status.get("account") or {}
    acct = account.get("acct") or ""
    display = account.get("display_name") or acct or None

    published = _parse_iso(status.get("created_at"))

    # external_id 는 instance+sid 조합으로 인스턴스 간 ID 충돌 방지.
    external_id = hashlib.md5(
        f"mastodon::{instance}::{sid}".encode()
    ).hexdigest()[:16]

    return RawVOC(
        external_id=external_id,
        content=text,
        source_url=url,
        author_name=display,
        published_at=published,
        likes_count=int(status.get("favourites_count") or 0),
        comments_count=int(status.get("replies_count") or 0),
        shares_count=int(status.get("reblogs_count") or 0),
        country_code=None,  # mastodon 응답에 지역 정보 없음
        meta={
            "instance": instance,
            "tag": tag,
            "status_id": sid,
            "language": status.get("language"),
            "acct": acct,
        },
    )


# @lat: MastodonCrawler — [[crawler#Twitter Alternatives]] 참조.
class MastodonCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "mastodon", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)
        self.stats = {
            "fetched": 0,
            "per_tag": {},   # f"{instance}/{tag}" → count
            "blocked": [],
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=20.0,
            follow_redirects=True,
        )

    async def crawl(self) -> List[RawVOC]:
        raw_vocs: List[RawVOC] = []
        seen_urls: set = set()

        async with self._client() as client:
            for instance in INSTANCES:
                for tag in TAGS:
                    key = f"{instance}/{tag}"
                    try:
                        statuses = await self._fetch_tag(client, instance, tag)
                        new_count = 0
                        for st in statuses:
                            voc = status_to_rawvoc(st, instance, tag)
                            if voc is None:
                                continue
                            if voc.source_url in seen_urls:
                                continue
                            seen_urls.add(voc.source_url)
                            raw_vocs.append(voc)
                            new_count += 1
                        self.stats["per_tag"][key] = new_count
                        self.stats["fetched"] += len(statuses)
                        logger.info(
                            f"  mastodon {key}: {len(statuses)}건 fetch / {new_count}건 신규"
                        )
                    except httpx.HTTPStatusError as e:
                        code = e.response.status_code if e.response is not None else 0
                        self.stats["blocked"].append(f"{key}:{code}")
                        logger.warning(f"  mastodon {key} HTTP {code} — skip")
                    except Exception as e:
                        logger.warning(f"  mastodon {key} 실패: {e}")
                    await self._random_delay()

                    if len(raw_vocs) >= MAX_POSTS:
                        break
                if len(raw_vocs) >= MAX_POSTS:
                    break

        raw_vocs = raw_vocs[:MAX_POSTS]

        # MX 통합 키워드 영구 필터 (Data Clean 4 정책 — 다른 collector 와 동일).
        try:
            from nlp.mx_keywords import is_mx_relevant
            before_n = len(raw_vocs)
            raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
            logger.info(
                f"mastodon 수집 완료: {len(raw_vocs)}건 "
                f"(fetched {self.stats['fetched']} / mx_filter {before_n}→{len(raw_vocs)})"
            )
        except Exception as e:
            # nlp.mx_keywords import 실패는 치명 아님 — 필터 없이 진행.
            logger.warning(f"mastodon mx_filter skip: {e}")
            logger.info(f"mastodon 수집 완료: {len(raw_vocs)}건 (fetched {self.stats['fetched']})")
        return raw_vocs

    async def _fetch_tag(
        self, client: httpx.AsyncClient, instance: str, tag: str
    ) -> List[dict]:
        url = f"https://{instance}/api/v1/timelines/tag/{tag}"
        resp = await client.get(url, params={"limit": PER_TAG_LIMIT})
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


# 단독 실행: python -m platforms.mastodon
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    crawler = MastodonCrawler()
    vocs = asyncio.run(crawler.crawl())
    print(f"\n=== mastodon dry run ===")
    print(f"vocs: {len(vocs)}")
    print(f"stats: {crawler.stats}")
    if vocs:
        s = vocs[0]
        print(f"sample[0]: url={s.source_url}")
        print(f"           content={s.content[:120]}...")
