"""
Kaskus 크롤러 — httpx + JSON API (인도네시아 최대 종합 포럼)

www.kaskus.co.id 는 Next.js SPA 라 HTML 직접 파싱이 불가능하지만, 프런트가
사용하는 백엔드 JSON API 가 인증 없이 그대로 열려 있다.

  · GET /api/communities                                    — 전체 커뮤니티 목록(1600+)
  · GET /api/communities/<id>/threads?page=N               — 게시판 스레드 목록
  · GET /api/threads/<id>                                  — 스레드 메타 + first_post
  · GET /api/threads/<id>/posts?page=N&limit=20            — 답글(댓글) 목록

대상 커뮤니티 (전수 조사 후 selection)
  - 36  Handphone & Tablet         (총 11,195 스레드, 활성)
  - 577 Android                    (Android OS 전문)
  - 942 Droid Kaskus               (Android 기기 사용자 커뮤니티)

전략
  · 커뮤니티별 threads list 를 LIST_PAGES 페이지까지 페이징 → Samsung/Galaxy
    키워드 필터 (제목 + first_post content). 인니에서 "Samsung", "Galaxy"
    영문 그대로 사용. 모델명 S25/A07/Z Flip 등도 필터에 포함.
  · 필터 통과 후보 중 최신순(dateline desc) MAX_POSTS 건만 본문/댓글 보강.
  · 본문은 list response 의 content.text 가 ~500자로 절단돼 있으므로
    /api/threads/<id> 재호출 → first_post.content.text 사용.
  · 댓글은 /api/threads/<id>/posts 페이징(20개/page) 으로 전수 수집. 메타
    `total` 가 있어 페이지 수 계산 가능.

ID
  · 게시물: external_id = md5(thread_url)[:16]
  · 댓글:   external_id = md5(thread_url + "#c" + post_id)[:16]
    post_id 는 Kaskus 가 부여한 24자리 hex (ObjectId) 로 안정적.

시각
  · API 는 dateline = Unix epoch (UTC). datetime.fromtimestamp(t, tz=utc)
    로 직접 변환. WIB 시 변환은 불필요 (이미 UTC).

봇 차단
  · Mozilla UA 가 필수 (default httpx UA 는 403). USER_AGENTS 풀 사용.
  · Referer 헤더 필요 (api 요청에 Referer: https://www.kaskus.co.id/ 부여).
"""
import asyncio
import hashlib
import logging
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.kaskus.co.id"
API_BASE = "https://www.kaskus.co.id/api"

# Handphone & Tablet 위주, Android OS 포럼 보조. 942 는 사용자 커뮤니티(비공식)지만 활성.
KASKUS_COMMUNITIES = [
    (36,  "Handphone & Tablet"),
    (577, "Android"),
    (942, "Droid Kaskus"),
]

# 인도네시아도 Samsung / Galaxy 영문 표기 그대로. 모델명 변이 + One UI 등 키워드.
GALAXY_KEYWORDS = [
    "samsung", "galaxy",
    "s25", "s26", "s24", "s23",
    "z flip", "z fold", "zflip", "zfold",
    "tab s", "one ui", "oneui", "exynos",
    "a07", "a17", "a55", "a35",
]

# 커뮤니티당 페이지 (20스레드/페이지)
LIST_PAGES = 12
# 본문+댓글 보강 대상 스레드 상한
MAX_POSTS = 150
# 댓글 페이지 상한 (스레드당; 폭주 방지)
MAX_COMMENT_PAGES = 8


class KaskusCrawler(BaseCrawler):
    # nginx 단에서 IP-rate-limit (속도가 빠르면 인접 호출까지 403). 보수적으로 잡음.
    MIN_DELAY = 2.5
    MAX_DELAY = 5.0

    def __init__(self, platform_code: str = "kaskus", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        candidates: List[dict] = []
        seen_thread_ids: set = set()

        async with self._make_httpx_client() as client:
            client.headers["Accept"] = "application/json"
            client.headers["Accept-Language"] = "id-ID,id;q=0.9,en;q=0.6"
            client.headers["Referer"] = BASE_URL + "/"

            # 1) 커뮤니티별 스레드 리스트 페이징
            for cid, cname in KASKUS_COMMUNITIES:
                for page in range(1, LIST_PAGES + 1):
                    try:
                        threads = await self._fetch_threads_page(client, cid, page)
                        fresh = [
                            t for t in threads
                            if t["id"] not in seen_thread_ids
                               and self._is_galaxy_related(t)
                        ]
                        for t in fresh:
                            seen_thread_ids.add(t["id"])
                        candidates.extend(fresh)
                        logger.info(
                            f"  Kaskus {cname}(c{cid}) p{page}: "
                            f"{len(fresh)}/{len(threads)} (Galaxy match)"
                        )
                        if not threads:
                            break
                        await self._random_delay()
                    except Exception as e:
                        logger.warning(
                            f"  Kaskus {cname}(c{cid}) p{page} 실패: {e}"
                        )
                        break

            # 2) 최신순 정렬 후 상위 MAX_POSTS 만 상세 수집
            candidates.sort(
                key=lambda t: t.get("dateline") or 0, reverse=True
            )
            targets = candidates[:MAX_POSTS]
            logger.info(
                f"Kaskus 후보 {len(candidates)}건 중 상위 {len(targets)}건 상세 수집"
            )

            # 3) 본문 + 댓글 보강
            raw_vocs: List[RawVOC] = []
            for t in targets:
                await self._random_delay()
                try:
                    detail_vocs = await self._fetch_thread_detail(client, t)
                    raw_vocs.extend(detail_vocs)
                except Exception as e:
                    logger.warning(
                        f"  Kaskus 상세 실패 (thread {t.get('id')}): {e}"
                    )

        logger.info(
            f"Kaskus 수집 완료: {len(raw_vocs)}건 (스레드 {len(targets)}건)"
        )
        return raw_vocs

    # -------- helpers ----------------------------------------------------

    async def _fetch_threads_page(
        self, client: httpx.AsyncClient, community_id: int, page: int,
    ) -> List[dict]:
        url = f"{API_BASE}/communities/{community_id}/threads?page={page}"
        body = await self._get_json_with_retry(client, url)
        data = body.get("data", []) if isinstance(body, dict) else []
        return data or []

    async def _get_json_with_retry(
        self, client: httpx.AsyncClient, url: str, retries: int = 3,
    ) -> dict:
        """403 시 백오프 후 재시도. nginx rate-limit 회피.
        반환: response.json() (dict, top-level {data, meta} 구조).
        실패 시 빈 dict.
        """
        for attempt in range(retries):
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 403:
                    # 지수 백오프 (5s → 15s → 45s)
                    backoff = 5 * (3 ** attempt)
                    logger.warning(
                        f"  Kaskus 403 backoff {backoff}s: {url[-60:]}"
                    )
                    await asyncio.sleep(backoff)
                    # UA 회전
                    client.headers["User-Agent"] = self._random_ua()
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.RequestError as e:
                logger.debug(f"  Kaskus request error: {e}")
                await asyncio.sleep(2)
        return {}

    def _is_galaxy_related(self, thread: dict) -> bool:
        title = (thread.get("title") or "").lower()
        body  = ""
        c = thread.get("content")
        if isinstance(c, dict):
            body = (c.get("text") or "").lower()
        haystack = f"{title}\n{body}"
        return any(kw in haystack for kw in GALAXY_KEYWORDS)

    async def _fetch_thread_detail(
        self, client: httpx.AsyncClient, thread_list_item: dict,
    ) -> List[RawVOC]:
        thread_id = thread_list_item["id"]
        slug = thread_list_item.get("slug") or ""
        thread_url = f"{BASE_URL}/thread/{thread_id}/{slug}".rstrip("/")

        # 본문 풀텍스트 재조회 (rate-limit 백오프 포함)
        body = await self._get_json_with_retry(
            client, f"{API_BASE}/threads/{thread_id}"
        )
        td = body.get("data", {}) if isinstance(body, dict) else {}
        if not td:
            # 상세 조회 차단 시 list 데이터로 폴백 (본문은 절단되지만 0건 보다 낫다)
            td = thread_list_item

        title = td.get("title") or thread_list_item.get("title", "")
        first_post = td.get("first_post", {}) or {}
        body_text = ""
        c = first_post.get("content") or {}
        if isinstance(c, dict):
            body_text = c.get("text") or ""
        if not body_text:
            tc = td.get("content") or {}
            if isinstance(tc, dict):
                body_text = tc.get("text") or ""

        author = (first_post.get("user") or {}).get("display_name") or "anonymous"
        dateline = first_post.get("dateline") or td.get("dateline") or 0
        published_at = (
            datetime.fromtimestamp(int(dateline), tz=timezone.utc)
            if dateline else None
        )
        total_views = (td.get("meta") or {}).get("total_views") or 0
        total_replies = (td.get("meta") or {}).get("total_replies") or 0

        body_voc = RawVOC(
            external_id=hashlib.md5(thread_url.encode()).hexdigest()[:16],
            content=f"{title}\n{body_text}".strip(),
            source_url=thread_url,
            author_name=author,
            published_at=published_at,
            likes_count=int(total_views),  # views 를 likes 자리로 활용
            comments_count=int(total_replies),
            country_code="ID",
            meta={
                "thread_id": thread_id,
                "community_id": (td.get("community") or {}).get("id"),
            },
        )

        # 댓글 수집
        comment_vocs = await self._fetch_comments(
            client, thread_id, thread_url
        )

        logger.info(
            f"  Kaskus thread {thread_id[:10]}: "
            f"본문 {len(body_text)}자 + 댓글 {len(comment_vocs)}건"
        )
        return [body_voc] + comment_vocs

    async def _fetch_comments(
        self,
        client: httpx.AsyncClient,
        thread_id: str,
        thread_url: str,
    ) -> List[RawVOC]:
        out: List[RawVOC] = []
        page = 1
        while page <= MAX_COMMENT_PAGES:
            data = await self._get_json_with_retry(
                client,
                f"{API_BASE}/threads/{thread_id}/posts?page={page}&limit=20",
            )
            if not data:
                break

            posts = data.get("data", []) or []
            if not posts:
                break

            for p in posts:
                pid = p.get("id") or ""
                content = p.get("content") or {}
                text = content.get("text") if isinstance(content, dict) else ""
                text = (text or "").strip()
                if not text or len(text) < 3:
                    continue
                user = p.get("user") or {}
                cauthor = user.get("display_name") or "anonymous"
                cdate_epoch = p.get("dateline") or 0
                cpub = (
                    datetime.fromtimestamp(int(cdate_epoch), tz=timezone.utc)
                    if cdate_epoch else None
                )
                stable = pid or hashlib.md5(text.encode()).hexdigest()[:8]
                out.append(RawVOC(
                    external_id=hashlib.md5(
                        f"{thread_url}#c{stable}".encode()
                    ).hexdigest()[:16],
                    content=text,
                    source_url=thread_url,
                    author_name=cauthor,
                    published_at=cpub,
                    country_code="ID",
                ))

            total = (data.get("meta") or {}).get("total") or 0
            if page * 20 >= total:
                break
            page += 1
            await asyncio.sleep(0.4)

        return out
