"""
JagatReview 크롤러 — WP REST API (인도네시아 IT 리뷰 매체)

www.jagatreview.com 은 WordPress 사이트로 /wp-json/wp/v2/posts?search=<kw> 가
인증 없이 열려 있다. Cloudflare/봇 차단 없음 (Mozilla UA + Accept: application/json
만으로 200 OK). kaskus.co.id 가 nginx IP rate-limit 으로 사실상 수집 불가
(Stage 5C T3 Discovery: 0건/24h) 인 데 대한 ID country_code 보강 채널.

전략
  - WP REST posts?search=samsung&per_page=20 + search=galaxy&per_page=20
    두 쿼리 병합 → external_id(post.id) dedupe
  - Discovery 단계 확인: samsung 쿼리 = 20건, galaxy 쿼리 = 20건 (2026-06-08)
  - content: title + excerpt(HTML 태그 제거). 본문 전체는 1KB+ 라 excerpt 충분.
  - 댓글: WP REST /wp/v2/comments?post=<id> 가 제공되지만 jagatreview 는 비공개로
    설정돼 댓글 API 빈 결과. 기사 본문만 수집.
  - country_code="ID"
  - published_at: post.date_gmt → UTC
"""
import hashlib
import html
import os
import re
import sys
from datetime import datetime, timezone
from typing import List, Optional
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

WP_BASE = "https://www.jagatreview.com/wp-json/wp/v2"
SEARCH_TERMS = ["samsung", "galaxy"]
PER_PAGE = 20
MAX_POSTS = 80

# Samsung/Galaxy filter (인도네시아어/영어 공통)
GALAXY_KEYWORD_RE = re.compile(
    r"\b("
    r"samsung|galaxy"
    r"|one ?ui|oneui|bixby|exynos"
    r"|galaxy ?s\d{1,2}"
    r"|galaxy ?z ?fold|galaxy ?z ?flip|galaxy ?fold|galaxy ?flip"
    r"|galaxy ?(?:m|a|f|note)\d{1,2}"
    r"|galaxy ?buds|galaxy ?watch|galaxy ?tab|galaxy ?ring"
    r")\b",
    re.I,
)

# HTML 태그 제거용
TAG_RE = re.compile(r"<[^>]+>")


class JagatReviewCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.0

    def __init__(self, platform_code: str = "jagatreview", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_ids: set = set()

        async with self._make_httpx_client() as client:
            client.headers["Accept"] = "application/json"
            client.headers["Accept-Language"] = "id-ID,id;q=0.9,en;q=0.8"

            for kw in SEARCH_TERMS:
                try:
                    posts = await self._fetch_search(client, kw)
                    fresh = [p for p in posts if p.external_id not in seen_ids]
                    for p in fresh:
                        seen_ids.add(p.external_id)
                    items.extend(fresh)
                    logger.info(
                        f"  JagatReview WP[{kw}]: {len(fresh)}/{len(posts)} 신규"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  JagatReview WP[{kw}] 실패: {e}")

        # Galaxy/Samsung 필터 (제목+excerpt 기준)
        filtered = [v for v in items if self._is_galaxy_related(v)]
        filtered.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = filtered[:MAX_POSTS]
        logger.info(
            f"JagatReview 수집 완료: {len(result)}건 "
            f"(원시 {len(items)} → Galaxy {len(filtered)})"
        )
        return result

    # ---------- fetchers ----------

    async def _fetch_search(
        self, client: httpx.AsyncClient, keyword: str
    ) -> List[RawVOC]:
        url = f"{WP_BASE}/posts?search={keyword}&per_page={PER_PAGE}&_fields=id,date_gmt,link,title,excerpt"
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            return []
        return [self._to_voc(p) for p in data if p]

    # ---------- helpers ----------

    def _to_voc(self, post: dict) -> RawVOC:
        pid = post.get("id")
        link = (post.get("link") or "").strip()
        title_html = ((post.get("title") or {}).get("rendered") or "").strip()
        excerpt_html = ((post.get("excerpt") or {}).get("rendered") or "").strip()

        title = self._strip_html(title_html)
        excerpt = self._strip_html(excerpt_html)
        content = f"{title}\n{excerpt}".strip()

        date_gmt = (post.get("date_gmt") or "").strip()
        published_at: Optional[datetime] = None
        if date_gmt:
            try:
                # WP 는 ISO8601(naive GMT) 로 제공: "2026-06-08T08:00:13"
                dt = datetime.fromisoformat(date_gmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                published_at = dt
            except ValueError:
                pass

        external_id = hashlib.md5(
            f"jagatreview#{pid}".encode()
        ).hexdigest()[:16]

        return RawVOC(
            external_id=external_id,
            content=content,
            source_url=link,
            author_name=None,
            published_at=published_at,
            country_code="ID",
            meta={"post_id": pid, "source": "wp_rest_api"},
        )

    def _strip_html(self, text: str) -> str:
        if not text:
            return ""
        # HTML entity decode → 태그 제거 → whitespace 정규화
        decoded = html.unescape(text)
        no_tag = TAG_RE.sub(" ", decoded)
        return re.sub(r"\s+", " ", no_tag).strip()

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = voc.content or ""
        if not text.strip():
            return False
        return bool(GALAXY_KEYWORD_RE.search(text))
