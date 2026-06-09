"""Misskey (fediverse) 크롤러 — 공개 notes/search API 사용.

배경
====
Misskey 는 일본 기원 분산 SNS 로 fediverse 의 mastodon 외 또 다른
주요 흐름.  영문권 Mastodon 이 cover 하지 못하는 한·일 사용자
voc 를 보완한다 (특히 Galaxy Fold/Z Flip 일본 사용자 후기 풍부).

- mastodon 과 함께 Twitter/X 무료 대안 보조 채널.
- misskey.io / misskey.design 등 다중 인스턴스를 fan-out (id 충돌 방지는
  hash(instance::id) 로 처리).

API
===
POST https://<instance>/api/notes/search
  body: {"query": "<keyword>", "limit": 30}
  헤더: Content-Type: application/json
  익명 GET 가 아닌 POST JSON 인 점이 mastodon 과 다르다.

응답 (요약):
  [
    {
      "id": "ana1ft...",
      "createdAt": "2026-06-09T10:05:14.339Z",
      "user": {"username": "kum4423", "name": "...", "host": null},
      "text": "Galaxy Fold3 を 65,000 円 で ...",
      "renoteCount": 0,
      "repliesCount": 0,
      "reactionCount": 5,
      ...
    },
    ...
  ]

text 필드는 평문 (mastodon 의 content HTML 과 달리 sanitize 불필요).

키 의존성
=========
없음.  익명 POST 로 작동.  UA 헤더만 SignalForge 식별자로 고정.
MX 키워드 필터 (is_mx_relevant) 가 후단에서 자동 적용된다.

플랫폼 코드: misskey  (DB platforms row 등록 필요).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC  # noqa: E402

logger = logging.getLogger(__name__)


# fan-out 인스턴스 — 활동량 상위 (200 응답 확인).  misskey.io = JP 대형,
# misskey.design = 디자인/IT 친화, misskey.systems = 영어 사용자 비중 높음.
INSTANCES: List[str] = [
    "misskey.io",
    "misskey.design",
    "misskey.systems",
]

# 검색 쿼리 — mastodon TAGS 와 정렬하되 공백 허용 (한·일 사용자가 영문 모델명
# 그대로 쓰는 패턴 + 일문 회사명 포함).
QUERIES: List[str] = [
    "galaxy",
    "samsung",
    "Galaxy Fold",
    "Galaxy S25",
    "Galaxy Z Flip",
    "pixel9",
    "iphone16",
]

USER_AGENT = "SignalForge/1.0 Misskey collector"
# instance/query 당 fetch 상한.  notes/search 의 권장 limit 은 10-30.
PER_QUERY_LIMIT = 30
# 전체 누적 상한 — NLP 단계 부하 방지.  mastodon 240, bluesky 와 동급.
MAX_POSTS = 240


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


def note_to_rawvoc(note: dict, instance: str, query: str) -> Optional[RawVOC]:
    """Misskey note 응답 → RawVOC.  필수 필드 결손 시 None."""
    nid = note.get("id")
    if not nid:
        return None
    text = (note.get("text") or "").strip()
    if not text:
        # cw (content warning) 만 있고 본문 없는 노트는 스킵.
        return None

    user = note.get("user") or {}
    username = user.get("username") or ""
    host = user.get("host")  # None = 같은 인스턴스 로컬 사용자
    display = user.get("name") or username or None

    # source_url 합성 — Misskey 는 응답에 url 필드가 없어 표준 패턴 사용.
    #   로컬 노트:  https://<instance>/notes/<id>
    #   리모트 노트(host != None):  마찬가지 (해당 인스턴스의 노트 사본 페이지).
    url = f"https://{instance}/notes/{nid}"

    published = _parse_iso(note.get("createdAt"))

    # external_id 는 instance+nid 조합으로 인스턴스 간 ID 충돌 방지.
    external_id = hashlib.md5(
        f"misskey::{instance}::{nid}".encode()
    ).hexdigest()[:16]

    return RawVOC(
        external_id=external_id,
        content=text,
        source_url=url,
        author_name=display,
        published_at=published,
        likes_count=int(note.get("reactionCount") or 0),
        comments_count=int(note.get("repliesCount") or 0),
        shares_count=int(note.get("renoteCount") or 0),
        country_code=None,  # misskey 응답에 지역 정보 없음
        meta={
            "instance": instance,
            "query": query,
            "note_id": nid,
            "user_host": host,
            "username": username,
        },
    )


# @lat: MisskeyCrawler — fediverse 보조 (mastodon 의 한·일 보완).
class MisskeyCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "misskey", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)
        self.stats = {
            "fetched": 0,
            "per_query": {},  # f"{instance}/{query}" → count
            "blocked": [],
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=20.0,
            follow_redirects=True,
        )

    async def crawl(self) -> List[RawVOC]:
        raw_vocs: List[RawVOC] = []
        seen_urls: set = set()

        async with self._client() as client:
            for instance in INSTANCES:
                for query in QUERIES:
                    key = f"{instance}/{query}"
                    try:
                        notes = await self._fetch_query(client, instance, query)
                        new_count = 0
                        for nt in notes:
                            voc = note_to_rawvoc(nt, instance, query)
                            if voc is None:
                                continue
                            if voc.source_url in seen_urls:
                                continue
                            seen_urls.add(voc.source_url)
                            raw_vocs.append(voc)
                            new_count += 1
                        self.stats["per_query"][key] = new_count
                        self.stats["fetched"] += len(notes)
                        logger.info(
                            f"  misskey {key}: {len(notes)}건 fetch / {new_count}건 신규"
                        )
                    except httpx.HTTPStatusError as e:
                        code = e.response.status_code if e.response is not None else 0
                        self.stats["blocked"].append(f"{key}:{code}")
                        logger.warning(f"  misskey {key} HTTP {code} — skip")
                    except Exception as e:
                        logger.warning(f"  misskey {key} 실패: {e}")
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
                f"misskey 수집 완료: {len(raw_vocs)}건 "
                f"(fetched {self.stats['fetched']} / mx_filter {before_n}→{len(raw_vocs)})"
            )
        except Exception as e:
            logger.warning(f"misskey mx_filter skip: {e}")
            logger.info(f"misskey 수집 완료: {len(raw_vocs)}건 (fetched {self.stats['fetched']})")
        return raw_vocs

    async def _fetch_query(
        self, client: httpx.AsyncClient, instance: str, query: str
    ) -> List[dict]:
        url = f"https://{instance}/api/notes/search"
        body = {"query": query, "limit": PER_QUERY_LIMIT}
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


# 단독 실행: python -m platforms.misskey
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    crawler = MisskeyCrawler()
    vocs = asyncio.run(crawler.crawl())
    print(f"\n=== misskey dry run ===")
    print(f"vocs: {len(vocs)}")
    print(f"stats: {crawler.stats}")
    if vocs:
        s = vocs[0]
        print(f"sample[0]: url={s.source_url}")
        print(f"           content={s.content[:120]}...")
