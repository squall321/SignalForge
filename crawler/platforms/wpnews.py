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
# WP REST 기간 검색이 **실제로 과거를 내주는 것만** 넣는다. 2026-09-15 실측 —
#   Hipertextual·TechCabal·MySmartPrice  2022년 기사 정상 반환         → 채택
#   MobileSyrup   200 인데 after/before 를 무시하고 최신만 준다        → 제외
#   SamsungFans·Ausdroid·PhoneArena·XatakaMX  403                     → 제외
#   Tecnoblog     검색 0건                                            → 제외
# MobileSyrup 같은 경우가 위험하다 — 정상처럼 보이면서 최신 것만 다시 긁는다.
# 그래서 아래 _window_respected() 로 반환 날짜가 요청 창 안인지 확인한다.
_SITES = [
    ("9to5Google", "https://9to5google.com"),
    ("Phandroid", "https://phandroid.com"),
    ("SamMobile", "https://www.sammobile.com"),
    ("Hipertextual", "https://hipertextual.com"),
    ("TechCabal", "https://techcabal.com"),
    ("MySmartPrice", "https://www.mysmartprice.com"),
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

    def __init__(self, platform_code: Optional[str] = None,
                 product_code: Optional[str] = None, job_id: Optional[int] = None):
        # platform_code 는 tasks.crawl_platform 이 항상 넘긴다. 받지 않으면 TypeError 로
        # 매 실행 즉사한다(실측: 이 결함으로 5개 소스가 11~18일 무유입).
        super().__init__(platform_code or "wpnews", product_code=product_code, job_id=job_id)
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

            # **날짜 필터를 무시하는 사이트가 있다.** 200 과 데이터를 주면서
            # after/before 를 안 보는 것이다(실측 MobileSyrup). 그걸 못 걸러내면
            # 과거를 긁는 줄 알고 최신만 되풀이 수집한다. 창 밖이면 접는다.
            if not self._window_respected(posts):
                logger.warning(
                    "WP %s '%s' — 기간 필터가 무시된다(요청 %s~%s). 이 매체는 "
                    "역사 수집에 쓸 수 없다", pub, query, self.after or "~",
                    self.before or "~")
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

    def _window_respected(self, posts: list) -> bool:
        """받은 글의 날짜가 요청한 창 안인가.

        창을 지정하지 않았으면 항상 참. 하나라도 창 안이면 참으로 본다
        (경계 글이 섞일 수 있다). 전부 창 밖이면 필터가 무시된 것이다.
        """
        if not self.after and not self.before:
            return True
        lo = (self.after or "")[:10]
        hi = (self.before or "")[:10]
        seen_any = False
        for po in posts:
            d = (po.get("date_gmt") or po.get("date") or "")[:10]
            if not d:
                continue
            seen_any = True
            if (not lo or d >= lo) and (not hi or d <= hi):
                return True
        return not seen_any      # 날짜를 하나도 못 읽었으면 판단 보류

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
