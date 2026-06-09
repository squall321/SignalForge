"""
Telepolis 크롤러 — httpx + Samsung 태그 페이지 + 기사 JSON-LD

telepolis.pl (폴란드, 통신/모바일 전문 매체) 의 Samsung/Galaxy 관련 기사
본문 수집.

전략
  - 사이트 RSS (/rss) 는 200 응답하나 Samsung 태그별 RSS 는 없음. 태그 페이지
    /tag/samsung?page=N 는 HTTP 200 (Chrome UA). 페이지당 17개 teaser 출력.
  - 페이지 간 일부 중복(상단 고정 글)이 있어 set 으로 중복 제거. 페이지를
    12회 순회하면 페이지당 평균 ~13개 신규.
  - 기사 본문은 JSON-LD "@type":"Article" 블록에 headline / datePublished /
    author / articleBody 가 모두 포함 → 별도 본문 파싱 불필요.
  - 댓글은 Vue 컴포넌트 (CommentNotifications/Comments) 가 동적 로드. 정적
    HTML 에 댓글 본문이 들어있지 않고 /api/* 도 404 (CSRF 토큰 필요). 댓글
    수집은 단념하고 데이터 메타에 articleId 만 보존.
  - 시간: JSON-LD datePublished 는 ISO 8601 + tz (+02:00 CEST / +01:00 CET) 로
    응답. 그대로 parse 후 UTC 변환. 만약 tz 누락이면 폴란드 로컬 (CET=+1,
    CEST=+2, 3~10월 = DST) 가정.
  - 키워드 필터: 제목 / 본문 / 키워드 메타에 'samsung' 또는 'galaxy' 포함.
    페이지 listing 에는 Samsung 외 기사 (BYD, Ulefone 등) 도 섞이므로 본문
    paragraph 단계에서 다시 필터.
"""
import asyncio
import hashlib
import html as html_lib
import json
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

BASE_URL = "https://www.telepolis.pl"
TAG_URL = f"{BASE_URL}/tag/samsung"

# 태그 페이지 수 (페이지당 ~17건 teaser)
LIST_PAGES = 12
MAX_POSTS = 150

# 폴란드 표준시 — CET(+1)/CEST(+2). DST 는 3월 마지막 일~10월 마지막 일.
CET = timezone(timedelta(hours=1))
CEST = timezone(timedelta(hours=2))

# Galaxy / Samsung 키워드 — 폴란드어에서도 영문 표기 동일
GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
]


class TelepolisCrawler(BaseCrawler):
    MIN_DELAY = 1.2
    MAX_DELAY = 2.8

    def __init__(self, platform_code: str = "telepolis", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_urls: set = set()

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "pl-PL,pl;q=0.9,en;q=0.8"
            client.headers["Accept"] = (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            )

            # 1) 태그 페이지 순회 → 기사 URL 목록 수집
            for page in range(1, LIST_PAGES + 1):
                try:
                    urls = await self._fetch_list_page(client, page)
                    if not urls:
                        logger.info(f"  Telepolis page={page}: 0건 → 종료")
                        break
                    new_count = 0
                    for u in urls:
                        if u not in seen_urls:
                            seen_urls.add(u)
                            new_count += 1
                    logger.info(
                        f"  Telepolis list page={page}: {new_count} 신규 "
                        f"(누적 {len(seen_urls)})"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  Telepolis list page={page} 실패: {e}")

            # 2) 각 기사 페이지에서 JSON-LD 파싱
            url_list = list(seen_urls)
            for idx, url in enumerate(url_list):
                if len(items) >= MAX_POSTS:
                    break
                try:
                    voc = await self._fetch_article(client, url)
                    if voc and self._is_galaxy_related(voc):
                        items.append(voc)
                    if idx % 5 == 0:
                        await self._random_delay()
                except Exception as e:
                    logger.debug(f"  Telepolis 기사 실패 {url}: {e}")

        # 최신순 정렬
        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(
            f"Telepolis 수집 완료: {len(result)}건 "
            f"(URL 후보 {len(seen_urls)} / 갤럭시 매칭 {len(items)})"
        )
        return result

    # --- list ---

    async def _fetch_list_page(
        self, client: httpx.AsyncClient, page: int
    ) -> List[str]:
        url = TAG_URL if page == 1 else f"{TAG_URL}?page={page}"
        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        if resp.status_code != 200:
            logger.debug(f"Telepolis list page={page} HTTP {resp.status_code}")
            return []
        html = resp.text
        # teaser 링크: <a href="/tech/..." class="teaser ...">
        rel_urls = re.findall(
            r'<a[^>]+href="(/[^"]+)"[^>]*class="teaser[^"]*"',
            html,
        )
        absolute = []
        for u in rel_urls:
            if u.startswith("/tag/") or u.startswith("/?") or "#" in u:
                continue
            # 카테고리만 인 링크 (/tech/sprzet) 컷
            if u.count("/") < 3:
                continue
            absolute.append(BASE_URL + u)
        # 동일 페이지 내 중복 제거
        return list(dict.fromkeys(absolute))

    # --- article ---

    async def _fetch_article(
        self, client: httpx.AsyncClient, url: str
    ) -> Optional[RawVOC]:
        resp = await client.get(url, headers={"Referer": TAG_URL})
        if resp.status_code != 200:
            return None
        html = resp.text

        # JSON-LD 블록 추출
        meta = self._parse_jsonld(html)
        if not meta:
            return None

        title = (meta.get("headline") or "").strip()
        body = self._strip_html(meta.get("articleBody") or "")
        if not title and not body:
            return None

        # 본문 길이 컷
        if len(body) > 4000:
            body = body[:4000]
        full_content = f"{title}\n{body}".strip() if body else title
        if len(full_content) < 20:
            return None

        # 발행일
        published_at = self._parse_iso_date(meta.get("datePublished") or "")

        # 저자
        author = None
        a = meta.get("author")
        if isinstance(a, list) and a:
            author = (a[0].get("name") if isinstance(a[0], dict) else None)
        elif isinstance(a, dict):
            author = a.get("name")
        if author:
            author = author.strip() or None

        # articleId (Vue props 에서) — meta 보존용
        article_id = self._extract_article_id(html)

        # keywords meta (필터 보조)
        keywords = self._extract_meta_keywords(html)

        # external_id: URL + article_id 기반
        ext_seed = f"{url}#{article_id or hashlib.md5(url.encode()).hexdigest()[:8]}"
        external_id = hashlib.md5(ext_seed.encode()).hexdigest()[:16]

        # articleSection (카테고리) 메타
        section = (meta.get("articleSection") or "").strip()

        return RawVOC(
            external_id=external_id,
            content=full_content,
            source_url=url,
            author_name=author,
            published_at=published_at,
            country_code="PL",
            meta={
                "article_id": article_id,
                "section": section,
                "keywords": keywords,
                "source": "html+jsonld",
            },
        )

    # --- helpers ---

    @staticmethod
    def _parse_jsonld(html: str) -> Optional[dict]:
        """기사 HTML 에서 JSON-LD '@type':'Article' 블록 추출."""
        blocks = re.findall(
            r'<script type="application/ld\+json">(.*?)</script>',
            html, re.S,
        )
        for blk in blocks:
            try:
                data = json.loads(blk.strip())
            except json.JSONDecodeError:
                continue
            # 단일 또는 @graph 리스트
            candidates = []
            if isinstance(data, dict):
                if data.get("@graph"):
                    candidates = data["@graph"]
                else:
                    candidates = [data]
            elif isinstance(data, list):
                candidates = data
            for c in candidates:
                if not isinstance(c, dict):
                    continue
                t = c.get("@type")
                if t == "Article" or t == "NewsArticle" or (
                    isinstance(t, list) and any(x in ("Article", "NewsArticle") for x in t)
                ):
                    return c
        return None

    @staticmethod
    def _extract_article_id(html: str) -> Optional[str]:
        """Vue Comments props 에서 articleId 추출."""
        m = re.search(r'&quot;articleId&quot;:(\d+)', html)
        return m.group(1) if m else None

    @staticmethod
    def _extract_meta_keywords(html: str) -> List[str]:
        m = re.search(r'<meta\s+name="keywords"\s+content="([^"]*)"', html, re.I)
        if not m:
            return []
        raw = html_lib.unescape(m.group(1))
        return [k.strip() for k in raw.split(",") if k.strip()][:20]

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
        haystack = ((voc.content or "") + " " + " ".join(voc.meta.get("keywords") or [])).lower()
        if not haystack.strip():
            return False
        return any(kw in haystack for kw in GALAXY_KEYWORDS)

    @staticmethod
    def _parse_iso_date(text: str) -> Optional[datetime]:
        """ISO 8601 (datePublished). tz 없으면 폴란드 CET/CEST 가정."""
        if not text:
            return None
        try:
            # Python 3.11+ 는 +HH:MM 처리 가능
            dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            # 단순 DST: 3월 마지막 일~10월 마지막 일 = CEST
            month = dt.month
            tz = CEST if 3 <= month <= 10 else CET
            dt = dt.replace(tzinfo=tz)
        return dt.astimezone(timezone.utc)
