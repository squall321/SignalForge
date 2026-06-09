"""
Lemmy 크롤러 — 공개 REST API v3 (인증 불필요)
여러 인스턴스(lemmy.world, beehaw.org)에서 Galaxy / 경쟁사 VOC 수집.

Search:   GET /api/v3/search?q=<q>&type_=Posts&sort=New&limit=30
Comments: GET /api/v3/comment/list?post_id=<id>&limit=30
"""
import hashlib
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import List

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

INSTANCES = [
    "lemmy.world",
    "beehaw.org",
    "sh.itjust.works",
    "lemmy.ml",
    "programming.dev",       # R4 K4 add — tech-focused, Galaxy/iPhone posts confirmed
    "discuss.tchncs.de",     # R4 K4 add — EU-based general, Samsung topics confirmed
    "lemmy.zip",             # R5 L4 add — federated activity confirmed (10건/7d via ap_id), large general instance
]

QUERIES = [
    "Galaxy S25",
    "Samsung Galaxy",
    "Z Fold",
    "iPhone 16",
    "Pixel 9",
]

MAX_POSTS = 60
SEARCH_LIMIT = 30
COMMENT_LIMIT = 30

# Lemmy 검색이 full-text 기반이라 약하게 매칭되는 결과가 많아
# 본문에 실제 키워드가 포함된 글만 채택 (정확도 향상).
# 부분 일치 차단 위해 단어경계 정규식 사용 (예: "unfolding"이 "fold"로 매칭되지 않도록).
RELEVANCE_RE = re.compile(
    r"\b(galaxy|samsung|z\s*fold|z\s*flip|iphone|pixel|s2[3-6]|fold\s*\d|flip\s*\d)\b",
    re.IGNORECASE,
)


# @lat: LemmyCrawler — [[crawler#Lemmy Crawler]] 참조.
class LemmyCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "lemmy", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        list_posts: List[RawVOC] = []
        seen_keys: set = set()

        async with self._make_httpx_client() as client:
            # 1) 각 인스턴스 × 쿼리 검색
            for instance in INSTANCES:
                for q in QUERIES:
                    try:
                        posts = await self._search_posts(client, instance, q)
                        # 인스턴스-간 중복 제거 (ap_id 기준) + 관련성 필터
                        new_posts = []
                        for p in posts:
                            k = p.meta.get("ap_id") or p.external_id
                            if k in seen_keys:
                                continue
                            if not self._is_relevant(p):
                                continue
                            seen_keys.add(k)
                            new_posts.append(p)
                        list_posts.extend(new_posts)
                        logger.info(
                            f"  Lemmy {instance} q='{q}': {len(new_posts)} kept / {len(posts)} hits"
                        )
                    except Exception as e:
                        logger.warning(f"  Lemmy {instance} q='{q}' 검색 실패: {e}")
                    await self._random_delay()

            # 2) 최신순으로 상위 MAX_POSTS 만 댓글 수집
            list_posts.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target_posts = list_posts[:MAX_POSTS]
            logger.info(
                f"Lemmy 검색 {len(list_posts)}건 중 상위 {len(target_posts)}건 댓글 수집 시작"
            )

            raw_vocs: List[RawVOC] = list(target_posts)
            comment_total = 0
            for post in target_posts:
                await self._random_delay()
                try:
                    comments = await self._fetch_comments(client, post)
                    raw_vocs.extend(comments)
                    comment_total += len(comments)
                except Exception as e:
                    logger.warning(f"  Lemmy 댓글 수집 실패 ({post.source_url}): {e}")

        logger.info(
            f"Lemmy 수집 완료: 포스트 {len(target_posts)}건 + 댓글 {comment_total}건 "
            f"= {len(raw_vocs)}건"
        )
        return raw_vocs

    async def _search_posts(
        self, client: httpx.AsyncClient, instance: str, query: str
    ) -> List[RawVOC]:
        url = f"https://{instance}/api/v3/search"
        params = {
            "q": query,
            "type_": "Posts",
            "sort": "New",
            "limit": SEARCH_LIMIT,
        }
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        payload = resp.json()

        results: List[RawVOC] = []
        for pv in payload.get("posts") or []:
            post = pv.get("post") or {}
            counts = pv.get("counts") or {}
            creator = pv.get("creator") or {}
            community = pv.get("community") or {}

            post_id = post.get("id")
            ap_id = post.get("ap_id") or ""
            if not post_id:
                continue

            title = post.get("name") or ""
            body = post.get("body") or ""
            content = f"{title}\n{body}".strip()
            if not content:
                continue

            post_url = ap_id or f"https://{instance}/post/{post_id}"

            published_at = self._parse_iso(post.get("published"))

            external_id = hashlib.md5(
                f"lemmy_{ap_id or post_id}".encode()
            ).hexdigest()[:16]

            results.append(RawVOC(
                external_id=external_id,
                content=content,
                source_url=post_url,
                author_name=creator.get("name") or creator.get("display_name") or "anon",
                published_at=published_at,
                likes_count=int(counts.get("score") or 0),
                comments_count=int(counts.get("comments") or 0),
                country_code="US",
                meta={
                    "instance": instance,
                    "post_id": post_id,
                    "ap_id": ap_id,
                    "community": community.get("name"),
                    "query": query,
                },
            ))

        return results

    async def _fetch_comments(
        self, client: httpx.AsyncClient, post: RawVOC
    ) -> List[RawVOC]:
        instance = post.meta.get("instance") if post.meta else None
        post_id = post.meta.get("post_id") if post.meta else None
        if not instance or not post_id:
            return []

        url = f"https://{instance}/api/v3/comment/list"
        params = {
            "post_id": post_id,
            "limit": COMMENT_LIMIT,
            "sort": "New",
            "type_": "All",
        }
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        payload = resp.json()

        out: List[RawVOC] = []
        for cv in payload.get("comments") or []:
            comment = cv.get("comment") or {}
            creator = cv.get("creator") or {}
            counts = cv.get("counts") or {}

            cid = comment.get("id")
            ap_id = comment.get("ap_id") or ""
            body = comment.get("content") or ""
            if not cid or not body.strip():
                continue
            if comment.get("deleted") or comment.get("removed"):
                continue

            external_id = hashlib.md5(
                f"lemmy_c_{ap_id or cid}".encode()
            ).hexdigest()[:16]

            published_at = self._parse_iso(comment.get("published"))

            source_url = ap_id or f"https://{instance}/comment/{cid}"

            out.append(RawVOC(
                external_id=external_id,
                content=body,
                source_url=source_url,
                author_name=creator.get("name") or creator.get("display_name") or "anon",
                published_at=published_at,
                likes_count=int(counts.get("score") or 0),
                country_code="US",
                meta={
                    "instance": instance,
                    "parent_post_id": post_id,
                    "comment_id": cid,
                },
            ))

        return out

    @staticmethod
    def _is_relevant(post: RawVOC) -> bool:
        return bool(RELEVANCE_RE.search(post.content or ""))

    @staticmethod
    def _parse_iso(text):
        if not text:
            return None
        try:
            # Lemmy: "2025-11-12T18:42:31.123456" (no tz) — assume UTC
            t = text.replace("Z", "")
            if "." in t:
                t = t.split(".")[0]
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None
