"""
Reddit 크롤러 — 공식 OAuth (app-only client_credentials) 사용.

이전 구현은 old.reddit.com 의 비공식 .json 엔드포인트에 의존했으나
2026 년 봇 차단 강화로 403 Blocked 가 영구적이 되었다.
본 구현은 https://www.reddit.com/api/v1/access_token 으로 토큰을 받아
https://oauth.reddit.com 에서 listing + 댓글을 가져온다.

키 미입력 환경(.env 가 비어 있는 경우)에서는 crawl() 이 빈 리스트와
경고 로그를 반환한다. dry-run 으로 안전하게 종료.

필요한 .env:
    REDDIT_CLIENT_ID
    REDDIT_CLIENT_SECRET
    REDDIT_USER_AGENT   (예: "SignalForge/1.0 by /u/signalforge-bot")

가이드: docs/dashboard/REDDIT_OAUTH_GUIDE.md
"""
import base64
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

# 크롤링 대상 서브레딧 (Galaxy + 안드로이드 + 경쟁사 MX 통합)
# 2026-06-09 Data Grow R2 I3: 경쟁사 (Apple/Pixel) + 추가 Galaxy/일반
SUBREDDITS = [
    "samsung",
    "GalaxyS25",
    "GalaxyFold",
    "GalaxyWatch",
    "GalaxyBuds",
    "Android",
    "AndroidQuestions",
    "GalaxyFlip",
    "SamsungGalaxy",
    "oneui",
    "iphone",
    "GooglePixel",
    "smartphones",
]

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"
POST_BASE = "https://www.reddit.com"

# 전체 서브레딧 합산 상세 수집 상한
MAX_POSTS = 150
# 서브레딧당 listing fetch 건수
LIST_LIMIT = 30
# 댓글 fetch 상한
COMMENT_LIMIT = 50

# Reddit OAuth 토큰 TTL — 응답에 expires_in (s) 가 오지만 안전 마진 60 s.
_TOKEN_SAFETY_MARGIN = 60.0


def _has_reddit_keys() -> bool:
    """Reddit OAuth 키가 .env 에 채워져 있는지 확인."""
    cid = os.getenv("REDDIT_CLIENT_ID", "").strip()
    sec = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
    return bool(cid) and bool(sec)


def _user_agent() -> str:
    ua = os.getenv("REDDIT_USER_AGENT", "").strip()
    return ua or "SignalForge/1.0 by /u/signalforge-bot"


# 토큰 캐시 (모듈 전역) — (token, expires_epoch).
_token_cache: dict = {"token": None, "expires_at": 0.0}


async def _fetch_token(client: httpx.AsyncClient) -> Optional[str]:
    """app-only client_credentials 토큰 발급. 실패 시 None."""
    cid = os.getenv("REDDIT_CLIENT_ID", "").strip()
    sec = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
    if not cid or not sec:
        return None

    auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "User-Agent": _user_agent(),
    }
    data = {"grant_type": "client_credentials"}

    resp = await client.post(TOKEN_URL, headers=headers, data=data, timeout=20.0)
    resp.raise_for_status()
    payload = resp.json()
    token = payload.get("access_token")
    expires_in = float(payload.get("expires_in") or 3600.0)
    if not token:
        return None
    _token_cache["token"] = token
    _token_cache["expires_at"] = time.time() + expires_in - _TOKEN_SAFETY_MARGIN
    return token


async def get_reddit_token(client: httpx.AsyncClient) -> Optional[str]:
    """캐시된 토큰 반환. 만료/없음 시 재발급. 키 없으면 None."""
    if not _has_reddit_keys():
        return None
    now = time.time()
    token = _token_cache.get("token")
    if token and now < float(_token_cache.get("expires_at") or 0.0):
        return token
    return await _fetch_token(client)


def _reset_token_cache() -> None:
    """테스트용 — 토큰 캐시 초기화."""
    _token_cache["token"] = None
    _token_cache["expires_at"] = 0.0


# @lat: RedditCrawler — [[crawler#Reddit Crawler]] 참조.
class RedditCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.5

    def __init__(self, platform_code: str = "reddit", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        if not _has_reddit_keys():
            logger.warning(
                "Reddit OAuth 키 미설정 — REDDIT_CLIENT_ID/SECRET 가 비어 있어 수집을 skip 합니다. "
                "docs/dashboard/REDDIT_OAUTH_GUIDE.md 참조."
            )
            return []

        list_posts: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            token = await get_reddit_token(client)
            if not token:
                logger.warning("Reddit 토큰 발급 실패 — skip")
                return []

            # 1) 각 서브레딧 listing 수집
            for sub in SUBREDDITS:
                try:
                    posts = await self._fetch_subreddit_new(client, sub)
                    list_posts.extend(posts)
                    logger.info(f"  Reddit r/{sub}: {len(posts)}건 listing")
                except Exception as e:
                    logger.warning(f"  Reddit r/{sub} listing 실패: {e}")
                await self._random_delay()

            # 2) 최신순 정렬 후 MAX_POSTS 만 상세(댓글) 수집
            list_posts.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target_posts = list_posts[:MAX_POSTS]
            logger.info(
                f"Reddit listing {len(list_posts)}건 중 상위 {len(target_posts)}건 댓글 수집 시작"
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
                    logger.warning(f"  Reddit 댓글 수집 실패 ({post.source_url}): {e}")

        logger.info(
            f"Reddit 수집 완료: 포스트 {len(target_posts)}건 + 댓글 {comment_total}건 "
            f"= {len(raw_vocs)}건"
        )
        return raw_vocs

    async def _authed_get(
        self, client: httpx.AsyncClient, url: str, params: Optional[dict] = None
    ) -> dict:
        """OAuth 토큰을 자동 갱신하며 GET. 401 1회 재시도."""
        token = await get_reddit_token(client)
        if not token:
            raise RuntimeError("Reddit 토큰 없음")
        headers = {
            "Authorization": f"bearer {token}",
            "User-Agent": _user_agent(),
        }
        resp = await client.get(url, headers=headers, params=params or {}, timeout=30.0)
        if resp.status_code == 401:
            # 토큰 만료 — 캐시 비우고 재발급 후 1회 재시도
            _reset_token_cache()
            token = await get_reddit_token(client)
            if not token:
                raise RuntimeError("Reddit 토큰 재발급 실패")
            headers["Authorization"] = f"bearer {token}"
            resp = await client.get(url, headers=headers, params=params or {}, timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    async def _fetch_subreddit_new(
        self, client: httpx.AsyncClient, sub: str
    ) -> List[RawVOC]:
        url = f"{API_BASE}/r/{sub}/new"
        payload = await self._authed_get(client, url, params={"limit": LIST_LIMIT})

        results: List[RawVOC] = []
        children = (payload.get("data") or {}).get("children") or []
        for child in children:
            if child.get("kind") != "t3":
                continue
            d = child.get("data") or {}
            permalink = d.get("permalink") or ""
            if not permalink:
                continue
            post_url = f"{POST_BASE}{permalink}"
            title = d.get("title") or ""
            selftext = d.get("selftext") or ""
            content = f"{title}\n{selftext}".strip()
            if not content:
                continue

            created_utc = d.get("created_utc")
            published_at = (
                datetime.fromtimestamp(created_utc, tz=timezone.utc)
                if created_utc
                else None
            )

            author = d.get("author") or "[deleted]"

            results.append(RawVOC(
                external_id=hashlib.md5(post_url.encode()).hexdigest()[:16],
                content=content,
                source_url=post_url,
                author_name=author,
                published_at=published_at,
                likes_count=int(d.get("ups") or 0),
                comments_count=int(d.get("num_comments") or 0),
                country_code="US",
                meta={"permalink": permalink, "subreddit": sub},
            ))

        return results

    async def _fetch_comments(
        self, client: httpx.AsyncClient, post: RawVOC
    ) -> List[RawVOC]:
        permalink = post.meta.get("permalink") if post.meta else None
        if not permalink:
            return []
        # oauth.reddit.com 은 /comments/<id> 또는 permalink 양쪽 허용.
        url = f"{API_BASE}{permalink}"
        payload = await self._authed_get(
            client, url, params={"limit": COMMENT_LIMIT, "depth": 1}
        )

        if not isinstance(payload, list) or len(payload) < 2:
            return []

        comment_listing = payload[1] or {}
        children = (comment_listing.get("data") or {}).get("children") or []

        post_url = post.source_url
        out: List[RawVOC] = []
        for child in children:
            kind = child.get("kind")
            if kind == "more":
                continue
            if kind != "t1":
                continue
            cd = child.get("data") or {}
            body = cd.get("body") or ""
            if body in ("[deleted]", "[removed]") or not body.strip():
                continue

            cid = cd.get("id") or ""
            if not cid:
                continue

            created_utc = cd.get("created_utc")
            cdate = (
                datetime.fromtimestamp(created_utc, tz=timezone.utc)
                if created_utc
                else None
            )
            cauthor = cd.get("author") or "[deleted]"
            clikes = int(cd.get("ups") or 0)

            out.append(RawVOC(
                external_id=hashlib.md5(
                    f"{post_url}#c{cid}".encode()
                ).hexdigest()[:16],
                content=body,
                source_url=f"{post_url}{cid}/",
                author_name=cauthor,
                published_at=cdate,
                likes_count=clikes,
                country_code="US",
                meta={"parent_post": post_url, "subreddit": post.meta.get("subreddit")},
            ))

        return out
