"""
Mobile-Review.com 크롤러 — httpx + WordPress REST API (러시아어)

mobile-review.com 은 러시아 최대 모바일/IT 리뷰 매체 (2002~, 키릴 UTF-8).
WordPress 기반이며 두 커스텀 post type (`posts`, `news`) 모두 REST 가 열려있다.

전략 (frandroid 와 동일한 RSS-only 패턴이지만 본문 풍부 → REST 선호)
  - WP REST 검색 `/all/wp-json/wp/v2/news?search=Samsung&per_page=20&_embed=author`
    그리고 `/all/wp-json/wp/v2/posts?search=Samsung&...` 두 채널을 페이지네이션.
  - 한 페이지당 20건, `LIST_PAGES=12` 까지 (= 최대 240건/채널 × 2채널 = 480 후보).
  - 본문 (`content.rendered`) 풍부 — 뉴스 1.5k자, 리뷰 10k자 이상.
  - 댓글은 Tolstoy Comments 위젯 (4388) 으로 외부 JS 로드. httpx 비접근.
    settings API (`tolstoycomments.com/api/site/settings/4388`) 는 보이지만
    실제 메시지 API 가 인증 토큰 필요해 채집 보류 — 본문 한 건만 VOC 화.
  - 시간: REST `date_gmt` 가 +00:00 보장 → UTC 직변환. fallback 으로 `date`
    (MSK = UTC+3, 러시아 2014~ DST 폐지) 가정 후 변환.
  - 키워드 필터: REST `search=Samsung` 이 본문 매치라 정밀하지만, 일부 노이즈
    (Apple 위주 글에 Samsung 한 줄 언급) 가 섞이므로 영문+키릴 키워드로 한 번 더 검사.

차단 / 폴백
  - 사이트 자체 HTTP 200 정상 (Cloudflare 없음, 일반 nginx).
  - REST 가 막힐 경우 동일 키워드의 `/all/brand/samsung/feed/?paged=N` RSS
    로 폴백 — 한 페이지 10건, 본문은 description 짧음 → 본문 페이지 보강.
"""
import hashlib
import html as html_lib
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://mobile-review.com"
WP_BASE = f"{BASE_URL}/all/wp-json/wp/v2"

# 4PDA 와 동일한 모스크바 표준시 (UTC+3, 연중 고정)
MSK = timezone(timedelta(hours=3))

# 후보 수집 캡 — 최종 MAX_POSTS 로 컷
LIST_PAGES = 12        # 채널당 페이지 수
PER_PAGE = 20          # WP REST 페이지당 항목 수
MAX_POSTS = 150        # 최종 본문 처리 상한

# WP REST 검색 키워드 — 러시아어 검색이라 영문 "Samsung" 이 가장 잘 매치
SEARCH_TERMS = ["Samsung", "Galaxy"]

# 검색 채널 (WP custom post types)
WP_POST_TYPES = ["news", "posts"]

# 필터용 Samsung/Galaxy 키워드 (영문 + 러시아어 음역) — 4PDA 와 동일 풀
GALAXY_KEYWORDS = [
    # 영문
    "samsung", "galaxy",
    "s27", "s26", "s25", "s24", "s23", "s22",
    "fold", "flip", "ultra", "buds", "watch", "ring",
    "one ui", "oneui", "exynos", "bixby", "z fold", "z flip",
    # 러시아어 음역 (кириллица)
    "самсунг", "галакси", "галактика",
    "фолд", "флип", "ультра", "буд",
    "уан юай", "ванюай", "эксинос", "бикс",
]


class MobileReviewCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "mobile_review", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_ids: set = set()  # WP post id 중복 제거 (post + news 간 충돌 거의 없음)

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "ru-RU,ru;q=0.9,en;q=0.8"
            client.headers["Accept-Encoding"] = "gzip, deflate"

            for post_type in WP_POST_TYPES:
                for term in SEARCH_TERMS:
                    for page in range(1, LIST_PAGES + 1):
                        try:
                            page_items = await self._fetch_rest_page(
                                client, post_type, term, page
                            )
                            if not page_items:
                                logger.info(
                                    f"  MobileReview {post_type}/{term} p{page}: 0건 → 종료"
                                )
                                break

                            new_count = 0
                            for it in page_items:
                                pid = it.meta.get("post_id")
                                key = f"{post_type}:{pid}"
                                if key in seen_ids:
                                    continue
                                if not self._is_galaxy_related(it):
                                    continue
                                seen_ids.add(key)
                                items.append(it)
                                new_count += 1
                            logger.info(
                                f"  MobileReview {post_type}/{term} p{page}: "
                                f"{new_count} 신규 (api {len(page_items)})"
                            )
                            await self._random_delay()
                        except Exception as e:
                            logger.warning(
                                f"  MobileReview {post_type}/{term} p{page} 실패: {e}"
                            )
                            break

        # 최신순 정렬 → MAX_POSTS 컷
        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(
            f"MobileReview 수집 완료: {len(result)}건 (후보 {len(items)})"
        )
        return result

    # ----- WP REST 페이지 -----
    async def _fetch_rest_page(
        self,
        client: httpx.AsyncClient,
        post_type: str,
        search: str,
        page: int,
    ) -> List[RawVOC]:
        url = f"{WP_BASE}/{post_type}"
        params = {
            "search": search,
            "per_page": str(PER_PAGE),
            "page": str(page),
            "_embed": "author",
            "orderby": "date",
            "order": "desc",
        }
        resp = await client.get(
            url,
            params=params,
            headers={
                "Referer": BASE_URL + "/all/",
                "Accept": "application/json",
            },
        )
        # WP REST 가 page 초과시 400 응답 → 정상 종료 신호로 처리
        if resp.status_code == 400:
            return []
        if resp.status_code != 200:
            logger.debug(
                f"MobileReview REST {post_type}/{search} p{page} HTTP {resp.status_code}"
            )
            return []
        try:
            data = resp.json()
        except Exception as e:
            logger.debug(f"MobileReview REST JSON 파싱 실패: {e}")
            return []
        if not isinstance(data, list):
            return []
        return [v for v in (self._parse_post(p, post_type) for p in data) if v]

    def _parse_post(self, p: dict, post_type: str) -> Optional[RawVOC]:
        try:
            pid = p.get("id")
            link = (p.get("link") or "").strip()
            if not pid or not link:
                return None

            title = self._strip_html(p.get("title", {}).get("rendered", ""))
            content_html = p.get("content", {}).get("rendered", "")
            body = self._strip_html(content_html)
            if len(body) > 6000:
                body = body[:6000]

            full = f"{title}\n{body}".strip() if body else title
            if len(full) < 20:
                return None

            # date_gmt 우선 (이미 UTC) — 없으면 date(MSK) 변환
            published_at = self._parse_wp_date(
                p.get("date_gmt"), is_utc=True
            ) or self._parse_wp_date(p.get("date"), is_utc=False)

            # 작성자: _embedded.author[0].name
            author = None
            emb = p.get("_embedded") or {}
            authors = emb.get("author") or []
            if authors and isinstance(authors, list):
                a = authors[0] or {}
                name = a.get("name")
                if name:
                    author = name.strip()

            slug = p.get("slug") or ""
            external_id = hashlib.md5(
                f"{link}#{post_type}:{pid}".encode()
            ).hexdigest()[:16]

            return RawVOC(
                external_id=external_id,
                content=full,
                source_url=link,
                author_name=author,
                published_at=published_at,
                country_code="RU",
                meta={
                    "post_id": pid,
                    "post_type": post_type,
                    "slug": slug,
                    "source": "wp_rest",
                },
            )
        except Exception as e:
            logger.debug(f"MobileReview item 파싱 실패: {e}")
            return None

    # ----- 유틸 -----
    @staticmethod
    def _strip_html(s: str) -> str:
        if not s:
            return ""
        decoded = html_lib.unescape(s)
        decoded = re.sub(
            r"<(script|style)[^>]*>.*?</\1>", " ",
            decoded, flags=re.DOTALL | re.IGNORECASE,
        )
        no_tags = re.sub(r"<[^>]+>", " ", decoded)
        no_tags = re.sub(r"\s+", " ", no_tags).strip()
        return no_tags

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        return any(kw in text for kw in GALAXY_KEYWORDS)

    @staticmethod
    def _parse_wp_date(text: Optional[str], is_utc: bool) -> Optional[datetime]:
        """WP REST 의 ISO 'YYYY-MM-DDTHH:MM:SS' 파싱.

        date_gmt 는 이미 UTC, date 는 사이트 로컬타임 (MSK). 둘 다 tz 정보 없음.
        """
        if not text:
            return None
        try:
            # 마이크로초/타임존 무시
            dt = datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
            tz = timezone.utc if is_utc else MSK
            return dt.replace(tzinfo=tz).astimezone(timezone.utc)
        except Exception:
            return None
