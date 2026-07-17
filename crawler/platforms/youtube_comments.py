"""YouTube 댓글 수집기 — Galaxy 관련 영상(쇼츠 포함)의 시청자 댓글을 VOC 로 수집.

배경
====
YouTube 리뷰/개봉기 영상의 댓글은 실사용자 반응이 밀집한 고밀도 VOC 다.
공식 YouTube Data API v3 를 사용한다(스크래핑 X — ToS 준수·안정성).
YouTube 쇼츠도 결국 일반 video 이므로 같은 commentThreads API 로 처리된다.

수집 흐름
=========
1. search.list(type=video) 로 Galaxy 관련 질의별 최근 영상 ID 수집.
2. 영상마다 commentThreads.list 로 상위 댓글(top-level) → RawVOC fan-out.
3. RawVOC normalize 후 voc_records 저장(제품 매핑은 BaseCrawler.normalize 가
   댓글 본문에서 추론 — 미매핑 댓글도 일반 VOC 로 보존).

키 의존성
=========
YOUTUBE_API_KEY 필수. 미설정 시 0건 수집하고 graceful skip(reddit.py 패턴).
발급: Google Cloud Console → YouTube Data API v3 사용 설정 → API 키(무료).

quota (일 10,000 units 무료)
============================
search.list = 100 units, commentThreads.list = 1 unit.
기본값(질의 5 × 영상 5 = search 5회 500u + 영상 25 × 1u = 525u/run).
4h 주기(6회/일) 기준 ~3,150 u/일 — 무료 한도의 1/3.

런타임 override (env, 워커 재기동 없이)
=======================================
- YOUTUBE_QUERIES          콤마구분 검색어 (기본: Galaxy 최신 플래그십 세트)
- YOUTUBE_VIDEOS_PER_QUERY 질의당 영상 수 (기본 5)
- YOUTUBE_COMMENTS_PER_VIDEO 영상당 댓글 수 (기본 100, API 최대 100)
- YOUTUBE_REGION           relevanceLanguage/regionCode 힌트 (기본 없음)

플랫폼 코드: youtube  (alembic 0020 platforms row 사전 삽입)
"""
from __future__ import annotations

import hashlib
import logging
import os
import sys
from datetime import datetime
from typing import List, Optional

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC  # noqa: E402

logger = logging.getLogger(__name__)

_API = "https://www.googleapis.com/youtube/v3"

# 기본 검색어 — 전 제품군 시리즈 레벨(모델별이면 quota 폭발) + 경쟁사. 영/한 혼합.
# 시리즈 질의로 넓게 훑으면 댓글에서 구체 모델(S26/S24/폴드7…)이 언급되고 normalize 가 자동 매핑.
# env YOUTUBE_QUERIES(콤마구분) 로 override.
_DEFAULT_QUERIES: List[str] = [
    "Samsung Galaxy S review",
    "Samsung Galaxy Z Fold review",
    "Samsung Galaxy Z Flip review",
    "Samsung Galaxy A series review",
    "Samsung Galaxy Note review",
    "Samsung Galaxy Tab S review",
    "Samsung Galaxy Watch review",
    "Samsung Galaxy Buds review",
    "Samsung Galaxy Ring review",
    "Samsung Galaxy FE review",
    "Samsung Galaxy unboxing",
    "Samsung Galaxy comparison",
    "삼성 갤럭시 리뷰",
    "갤럭시 S 리뷰",
    "갤럭시 폴드 리뷰",
    "갤럭시 워치 리뷰",
    "iPhone vs Samsung Galaxy",
    "Pixel vs Samsung Galaxy",
]


def _queries() -> List[str]:
    raw = os.getenv("YOUTUBE_QUERIES", "").strip()
    if raw:
        return [q.strip() for q in raw.split(",") if q.strip()]
    return _DEFAULT_QUERIES


def _parse_published(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # RFC3339 'Z' → +00:00
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


class YouTubeCommentsCrawler(BaseCrawler):
    """YouTube Data API v3 로 Galaxy 관련 영상 댓글을 수집한다."""

    MIN_DELAY = 0.2
    MAX_DELAY = 0.6

    def __init__(self, product_code: Optional[str] = None, job_id: Optional[int] = None):
        super().__init__("youtube", product_code=product_code, job_id=job_id)
        self.api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
        self.videos_per_query = int(os.getenv("YOUTUBE_VIDEOS_PER_QUERY") or "5")
        self.comments_per_video = min(int(os.getenv("YOUTUBE_COMMENTS_PER_VIDEO") or "100"), 100)
        self.region = os.getenv("YOUTUBE_REGION", "").strip()
        # 영상 정렬 — relevance 가 '토론이 몰린 영상'을 준다(date 는 갓 업로드돼 댓글 0건이 많음).
        self.order = os.getenv("YOUTUBE_ORDER", "relevance").strip() or "relevance"
        # 기간 슬라이싱(모든 기간 backfill 용) — RFC3339. 지정하면 그 구간 영상만 검색.
        # 미지정이면 전 기간(YouTube 기본). 연도 backfill 은 youtube-backfill.sh 가 주입.
        self.published_after = os.getenv("YOUTUBE_PUBLISHED_AFTER", "").strip()
        self.published_before = os.getenv("YOUTUBE_PUBLISHED_BEFORE", "").strip()

    async def crawl(self) -> List[RawVOC]:
        if not self.api_key:
            self.logger.warning(
                "YOUTUBE_API_KEY 미설정 — YouTube 댓글 수집을 skip 합니다. "
                "Google Cloud Console 에서 YouTube Data API v3 키(무료)를 발급해 .env 에 설정하세요."
            )
            return []

        out: List[RawVOC] = []
        seen_videos: set[str] = set()
        async with self._make_httpx_client() as client:
            for q in _queries():
                for vid, vtitle in await self._search_videos(client, q):
                    if vid in seen_videos:
                        continue
                    seen_videos.add(vid)
                    out.extend(await self._fetch_comments(client, vid, vtitle, q))
                    await self._random_delay()
        window = ""
        if self.published_after or self.published_before:
            window = f" [기간 {self.published_after or '~'}~{self.published_before or '~'}]"
        self.logger.info("YouTube 수집 완료%s — 질의 %d · 영상 %d개 / 댓글 %d건",
                         window, len(_queries()), len(seen_videos), len(out))
        return out

    async def _search_videos(self, client: httpx.AsyncClient, query: str) -> List[tuple]:
        """search.list → [(videoId, title), ...] 최근 관련 영상."""
        params = {
            "part": "snippet", "q": query, "type": "video",
            "order": self.order, "maxResults": self.videos_per_query,
            "key": self.api_key,
        }
        if self.region:
            params["relevanceLanguage"] = self.region
        if self.published_after:
            params["publishedAfter"] = self.published_after
        if self.published_before:
            params["publishedBefore"] = self.published_before
        try:
            r = await client.get(f"{_API}/search", params=params)
            if r.status_code != 200:
                self.logger.warning("search.list %s → %s", query, r.status_code)
                return []
            items = (r.json() or {}).get("items", [])
        except (httpx.HTTPError, ValueError) as e:
            self.logger.warning("search.list 실패 (%s): %r", query, e)
            return []
        res = []
        for it in items:
            vid = (it.get("id") or {}).get("videoId")
            title = (it.get("snippet") or {}).get("title", "")
            if vid:
                res.append((vid, title))
        return res

    async def _fetch_comments(
        self, client: httpx.AsyncClient, video_id: str, video_title: str, query: str
    ) -> List[RawVOC]:
        """commentThreads.list → 영상의 top-level 댓글을 RawVOC 로."""
        params = {
            "part": "snippet", "videoId": video_id,
            "maxResults": self.comments_per_video, "order": "relevance",
            "textFormat": "plainText", "key": self.api_key,
        }
        try:
            r = await client.get(f"{_API}/commentThreads", params=params)
            if r.status_code == 403:
                # 댓글 비활성 영상 — 정상 skip.
                return []
            if r.status_code != 200:
                self.logger.warning("commentThreads %s → %s", video_id, r.status_code)
                return []
            items = (r.json() or {}).get("items", [])
        except (httpx.HTTPError, ValueError) as e:
            self.logger.warning("commentThreads 실패 (%s): %r", video_id, e)
            return []

        video_url = f"https://www.youtube.com/watch?v={video_id}"
        out: List[RawVOC] = []
        for it in items:
            cid = it.get("id") or ""
            sn = (((it.get("snippet") or {}).get("topLevelComment") or {}).get("snippet")) or {}
            text = (sn.get("textOriginal") or sn.get("textDisplay") or "").strip()
            if not cid or len(text) < 10:
                continue
            out.append(RawVOC(
                external_id=hashlib.md5(f"youtube:{cid}".encode()).hexdigest()[:16],
                content=text,
                source_url=f"{video_url}&lc={cid}",
                author_name=sn.get("authorDisplayName"),
                published_at=_parse_published(sn.get("publishedAt")),
                likes_count=int(sn.get("likeCount") or 0),
                comments_count=int(it.get("snippet", {}).get("totalReplyCount") or 0),
                meta={"video_id": video_id, "video_title": video_title, "query": query},
            ))
        return out
