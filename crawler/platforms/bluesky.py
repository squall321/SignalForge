"""
Bluesky 크롤러 — AT Protocol public API 사용 (Twitter 무료 대안 1순위).

Bluesky 는 X.com / Twitter 의 폐쇄적 API 정책에 대한 대안으로 채택됐다.
- 무료 계정 1개로 검색 가능 (rate limit 관대: 3000 req/5분).
- AT Protocol 표준이라 응답 스키마 안정적 (uri / cid 영구 식별자).
- Galaxy/Pixel/iPhone 토픽 활동 점진 증가 중 (2026 기준).

키 미입력 환경(.env 가 비어 있는 경우)에서는 crawl() 이 빈 리스트와
경고 로그를 반환한다. dry-run 으로 안전하게 종료.

필요한 .env:
    BLUESKY_HANDLE     (예: signalforge.bsky.social)
    BLUESKY_PASSWORD   (앱 패스워드 권장 — 계정 보호용)

가이드: docs/dashboard/TWITTER_ALTERNATIVES.md
"""
import hashlib
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)


SESSION_URL = "https://bsky.social/xrpc/com.atproto.server.createSession"
SEARCH_URL = "https://bsky.social/xrpc/app.bsky.feed.searchPosts"
POST_WEB_BASE = "https://bsky.app/profile"

# Twitter 크롤러와 HackerNews 와 동일한 검색어 — 비교 분석 용도.
QUERY_TERMS = [
    "Galaxy S25",
    "Samsung Galaxy",
    "Galaxy Z Fold",
    "Galaxy Watch",
    "Pixel 9",
    "iPhone 16 vs Galaxy",
]

# 검색어별 fetch 상한 (Bluesky searchPosts 의 limit 최댓값 = 100).
SEARCH_LIMIT = 25
# 전체 누적 상한 (중복 제거 후) — 너무 많이 받으면 NLP 단계 부하 증가.
MAX_POSTS = 150

# 토큰 TTL 안전 마진. Bluesky access JWT 는 약 2시간 유효.
_TOKEN_SAFETY_MARGIN = 120.0

# 모듈 전역 토큰 캐시 — (accessJwt, expires_epoch).
_token_cache: dict = {"token": None, "expires_at": 0.0}


def _has_bluesky_keys() -> bool:
    """Bluesky 인증 키가 .env 에 채워져 있는지 확인."""
    handle = os.getenv("BLUESKY_HANDLE", "").strip()
    pw = os.getenv("BLUESKY_PASSWORD", "").strip()
    return bool(handle) and bool(pw)


def _reset_token_cache() -> None:
    """테스트용 — 토큰 캐시 초기화."""
    _token_cache["token"] = None
    _token_cache["expires_at"] = 0.0


async def _create_session(client: httpx.AsyncClient) -> Optional[str]:
    """createSession 으로 accessJwt 발급. 실패 시 None."""
    handle = os.getenv("BLUESKY_HANDLE", "").strip()
    pw = os.getenv("BLUESKY_PASSWORD", "").strip()
    if not handle or not pw:
        return None

    payload = {"identifier": handle, "password": pw}
    resp = await client.post(SESSION_URL, json=payload, timeout=20.0)
    resp.raise_for_status()
    data = resp.json()
    token = data.get("accessJwt")
    if not token:
        return None

    # Bluesky 응답에는 명시적 expires_in 이 없어 보수적으로 1.5h 캐시.
    _token_cache["token"] = token
    _token_cache["expires_at"] = time.time() + 5400.0 - _TOKEN_SAFETY_MARGIN
    return token


async def get_bluesky_token(client: httpx.AsyncClient) -> Optional[str]:
    """캐시된 토큰 반환. 만료/없음 시 재발급. 키 없으면 None."""
    if not _has_bluesky_keys():
        return None
    now = time.time()
    token = _token_cache.get("token")
    if token and now < float(_token_cache.get("expires_at") or 0.0):
        return token
    return await _create_session(client)


# @lat: BlueskyCrawler — [[crawler#Twitter Alternatives]] 참조.
class BlueskyCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "bluesky", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        if not _has_bluesky_keys():
            logger.warning(
                "Bluesky 인증 키 미설정 — BLUESKY_HANDLE/PASSWORD 가 비어 있어 수집을 skip 합니다. "
                "docs/dashboard/TWITTER_ALTERNATIVES.md 참조."
            )
            return []

        raw_vocs: List[RawVOC] = []
        seen_uris: set = set()

        async with self._make_httpx_client() as client:
            token = await get_bluesky_token(client)
            if not token:
                logger.warning("Bluesky 세션 발급 실패 — skip")
                return []

            for q in QUERY_TERMS:
                try:
                    posts = await self._search_posts(client, q)
                    new_count = 0
                    for post in posts:
                        uri = post.get("uri")
                        if not uri or uri in seen_uris:
                            continue
                        seen_uris.add(uri)
                        voc = self._post_to_voc(post)
                        if voc:
                            raw_vocs.append(voc)
                            new_count += 1
                    logger.info(f"  Bluesky search '{q}': {len(posts)}건 fetch / {new_count}건 신규")
                except Exception as e:
                    logger.warning(f"  Bluesky search '{q}' 실패: {e}")
                await self._random_delay()

                if len(raw_vocs) >= MAX_POSTS:
                    break

        raw_vocs = raw_vocs[:MAX_POSTS]
        logger.info(f"Bluesky 수집 완료: {len(raw_vocs)}건")
        return raw_vocs

    async def _search_posts(
        self, client: httpx.AsyncClient, query: str
    ) -> List[dict]:
        """searchPosts XRPC — accessJwt 인증 후 GET."""
        token = await get_bluesky_token(client)
        if not token:
            raise RuntimeError("Bluesky 토큰 없음")
        headers = {"Authorization": f"Bearer {token}"}
        params = {"q": query, "limit": SEARCH_LIMIT}

        resp = await client.get(SEARCH_URL, headers=headers, params=params, timeout=30.0)
        if resp.status_code == 401:
            # accessJwt 만료 — 캐시 비우고 재발급 후 1회 재시도.
            _reset_token_cache()
            token = await get_bluesky_token(client)
            if not token:
                raise RuntimeError("Bluesky 토큰 재발급 실패")
            headers["Authorization"] = f"Bearer {token}"
            resp = await client.get(SEARCH_URL, headers=headers, params=params, timeout=30.0)
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("posts") or []

    def _post_to_voc(self, post: dict) -> Optional[RawVOC]:
        """Bluesky post 응답 → RawVOC.

        응답 스키마 (요약):
          {
            "uri": "at://did:plc:xxx/app.bsky.feed.post/abcd",
            "cid": "bafyr...",
            "author": {"did": ..., "handle": "user.bsky.social", "displayName": ...},
            "record": {"text": "...", "createdAt": "2026-06-03T12:34:56.000Z"},
            "replyCount": N, "repostCount": N, "likeCount": N, "indexedAt": "..."
          }
        """
        uri = post.get("uri")
        if not uri:
            return None
        record = post.get("record") or {}
        text = (record.get("text") or "").strip()
        if not text:
            return None

        author = post.get("author") or {}
        handle = author.get("handle") or ""
        display_name = author.get("displayName") or handle

        # uri (at://did:plc:.../app.bsky.feed.post/<rkey>) → 웹 URL
        # https://bsky.app/profile/<handle>/post/<rkey>
        rkey = uri.rsplit("/", 1)[-1] if "/" in uri else uri
        web_url = (
            f"{POST_WEB_BASE}/{handle}/post/{rkey}"
            if handle
            else uri  # fallback: at:// URI 그대로
        )

        # createdAt 우선, 없으면 indexedAt
        ts_str = record.get("createdAt") or post.get("indexedAt")
        published_at: Optional[datetime] = None
        if ts_str:
            try:
                published_at = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except Exception:
                published_at = None

        return RawVOC(
            external_id=hashlib.md5(f"bsky_{uri}".encode()).hexdigest()[:16],
            content=text,
            source_url=web_url,
            author_name=display_name or None,
            published_at=published_at,
            likes_count=int(post.get("likeCount") or 0),
            comments_count=int(post.get("replyCount") or 0),
            shares_count=int(post.get("repostCount") or 0),
            country_code=None,  # Bluesky 응답에 지역 정보 없음
            meta={
                "uri": uri,
                "cid": post.get("cid"),
                "handle": handle,
            },
        )
