# WordPress REST 뉴스 수집기 — 옛 기사까지 연도 슬라이싱으로 소급(after/before)
"""
WP News 크롤러 — WordPress REST API(/wp-json/wp/v2/posts) 로 삼성/갤럭시 옛 기사 소급.

배경
====
단일 RSS 뉴스 크롤러는 최신만 준다. WP REST 는 `after`/`before`(ISO) 날짜 필터를
지원해 **연도별로 과거 기사 전체**를 가져올 수 있다(HN/YouTube 연도 backfill 과 동형).
REST 를 노출하는 매체만 대상(실측: 9to5google·phandroid·sammobile). 나머지는 Cloudflare
등으로 REST 차단.

동작
====
- 매체별 /wp-json/wp/v2/posts?search=Samsung Galaxy&after=&before=&per_page=100&page=N
- date_gmt(ISO UTC) → published_at 정확. title+excerpt 를 content 로.
- env WPNEWS_AFTER/WPNEWS_BEFORE(ISO) 로 기간 지정(backfill). 미지정 시 최근 90일.
- 매체 구분은 meta.publisher (플랫폼은 단일 'wpnews').

플랫폼 코드: wpnews  (alembic 0023 platforms row)
"""
import hashlib
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC  # noqa: E402

logger = logging.getLogger(__name__)

# WP REST 를 노출하고 옛 기사를 주는 매체 (실측 확인). code 는 meta.publisher 용.
_SITES = [
    ("9to5Google", "https://9to5google.com"),
    ("Phandroid", "https://phandroid.com"),
    ("SamMobile", "https://www.sammobile.com"),
]
_QUERIES = ["Samsung Galaxy", "Galaxy Fold", "Galaxy Watch"]
_TAG_RE = re.compile(r"<[^>]+>")
_GALAXY = re.compile(r"samsung|galaxy|z\s?fold|z\s?flip", re.IGNORECASE)


class WPNewsCrawler(BaseCrawler):
    """WordPress REST 로 삼성 뉴스 옛 기사를 연도 슬라이싱 수집."""

    MIN_DELAY = 0.4
    MAX_DELAY = 1.0
    MAX_PAGES = 3          # 사이트·쿼리·연도당 최대 페이지(100/page). NLP 부하로 과하지 않게.
    PER_PAGE = 100

    def __init__(self, product_code: Optional[str] = None, job_id: Optional[int] = None):
        super().__init__("wpnews", product_code=product_code, job_id=job_id)
        self.after = os.getenv("WPNEWS_AFTER", "").strip()
        self.before = os.getenv("WPNEWS_BEFORE", "").strip()
        if not self.after and not self.before:
            # 기본: 최근 90일
            self.after = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT00:00:00")

    async def crawl(self) -> List[RawVOC]:
        seen: set[str] = set()
        out: List[RawVOC] = []
        async with self._make_httpx_client() as client:
            for pub, base in _SITES:
                for q in _QUERIES:
                    out += await self._fetch_site(client, pub, base, q, seen)
        window = f"[{self.after or '~'}~{self.before or '~'}]"
        logger.info("WP뉴스 수집 완료 %s — %d건", window, len(out))
        return out

    async def _fetch_site(self, client, pub, base, query, seen) -> List[RawVOC]:
        res: List[RawVOC] = []
        for page in range(1, self.MAX_PAGES + 1):
            params = {
                "search": query, "per_page": self.PER_PAGE, "page": page,
                "_fields": "id,date_gmt,link,title,excerpt",
                "orderby": "date", "order": "desc",
            }
            if self.after:
                params["after"] = self.after
            if self.before:
                params["before"] = self.before
            try:
                r = await client.get(f"{base}/wp-json/wp/v2/posts", params=params)
                if r.status_code in (400, 404):   # 페이지 초과 = 끝
                    break
                if r.status_code != 200:
                    logger.warning("WP %s '%s' p%d → %s", pub, query, page, r.status_code)
                    break
                posts = r.json()
            except (httpx.HTTPError, ValueError) as e:
                logger.warning("WP %s '%s' p%d 실패: %r", pub, query, page, e)
                break
            if not isinstance(posts, list) or not posts:
                break
            for po in posts:
                v = self._to_voc(po, pub)
                if v and v.external_id not in seen and _GALAXY.search(v.content or ""):
                    seen.add(v.external_id)
                    res.append(v)
            if len(posts) < self.PER_PAGE:
                break
            await self._random_delay()
        return res

    def _to_voc(self, po: dict, pub: str) -> Optional[RawVOC]:
        link = (po.get("link") or "").strip()
        if not link:
            return None
        title = _TAG_RE.sub("", (po.get("title") or {}).get("rendered", "")).strip()
        excerpt = _TAG_RE.sub("", (po.get("excerpt") or {}).get("rendered", "")).strip()
        content = (f"{title}\n{excerpt}".strip()) or title
        if not content:
            return None
        dt = None
        dg = po.get("date_gmt")
        if dg:
            try:
                dt = datetime.fromisoformat(dg.replace("Z", "")).replace(tzinfo=timezone.utc)
            except ValueError:
                dt = None
        return RawVOC(
            external_id=hashlib.md5(f"wpnews#{link}".encode()).hexdigest()[:16],
            content=content,
            source_url=link,
            author_name=pub,
            published_at=dt,
            meta={"source": "wp_rest", "publisher": pub},
        )
