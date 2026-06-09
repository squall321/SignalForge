"""
Sanook Hitech 크롤러 — httpx + HTML/JSON-LD 기반

sanook.com/hitech (태국 최대 포털 Sanook 의 IT 섹션, 태국어) 의 Samsung /
Galaxy 관련 기사 본문 수집.

전략
  - /hitech/tag/samsung/ 태그 목록 페이지는 Chrome UA 로 정상 200.
    페이지네이션은 /hitech/tag/samsung/2/, /3/ … 형태.
  - 각 페이지에서 article ID 가 들어간 URL `/hitech/<NNNN>/` 추출.
  - 상세 페이지에는 JSON-LD NewsArticle 블록 (headline / datePublished /
    author) 및 <article class="EntryBody"> 본문 컨테이너가 있어 안정 파싱.
  - RSS 는 404 (Next.js 마이그 후 제거). 댓글은 Facebook plugin 으로 외부
    데이터라 채집 불가 → tecnoblog 와 동일하게 1 기사 = 1 VOC.
  - 시간: JSON-LD `2026-05-27T12:27:11+07:00` 형식이 ICT 명시되므로 그대로
    파싱 → UTC 변환. naive 가 들어오면 ICT (UTC+7) 가정.
  - Galaxy/Samsung 키워드 필터 — tag 페이지가 이미 samsung 으로 좁혀져
    있지만 본문 키워드도 한 번 더 확인.
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

BASE_URL = "https://www.sanook.com"
TAG_URL  = f"{BASE_URL}/hitech/tag/samsung/"

LIST_PAGES = 12          # /tag/samsung/1..12/
MAX_POSTS  = 150

# 태국 표준시 (ICT, UTC+7). 태국은 DST 없음.
ICT = timezone(timedelta(hours=7))

GALAXY_KEYWORDS = [
    # 핵심 브랜드 — ASCII / 태국어
    "galaxy", "samsung", "ซัมซุง", "กาแล็คซี่", "กาแล็กซี่",
    # 모델 코드 (제품군)
    "s27", "s26", "s25", "s24", "s23",
    "z fold", "z flip",
    "galaxy fold", "galaxy flip", "galaxy ultra",
    "galaxy buds", "galaxy watch", "galaxy tab", "galaxy ring",
    "one ui", "oneui", "exynos", "bixby",
]

# 상세 페이지에서 article 식별용
POST_URL_RE = re.compile(r"https?://www\.sanook\.com/hitech/(\d{5,8})/")
JSONLD_RE   = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL,
)
ARTICLE_RE  = re.compile(
    r'<article\b[^>]*class="[^"]*EntryBody[^"]*"[^>]*>(.*?)</article>',
    re.DOTALL | re.IGNORECASE,
)


class SanookCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "sanook", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_ids: set = set()

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "th,en;q=0.8"
            client.headers["Accept-Encoding"] = "gzip, deflate"
            client.headers["Accept"] = (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            )

            # 1) 목록 페이지 순회 → article URL 수집
            urls: list[str] = []
            for page in range(1, LIST_PAGES + 1):
                list_url = TAG_URL if page == 1 else f"{TAG_URL}{page}/"
                try:
                    found = await self._fetch_list_page(client, list_url)
                    new = [u for u in found if u not in urls]
                    urls.extend(new)
                    logger.info(
                        f"  Sanook list page={page}: {len(new)} 신규 URL "
                        f"(누적 {len(urls)})"
                    )
                    if not new:
                        # 페이지네이션 끝 또는 캐시 중복
                        break
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  Sanook list page={page} 실패: {e}")

            # 2) 상세 페이지 → RawVOC
            for url in urls[: MAX_POSTS * 2]:  # 본문 필터링 컷 여유
                pid_match = POST_URL_RE.search(url)
                if not pid_match:
                    continue
                post_id = pid_match.group(1)
                if post_id in seen_ids:
                    continue
                seen_ids.add(post_id)
                try:
                    voc = await self._fetch_article(client, url, post_id)
                    if voc and self._is_galaxy_related(voc):
                        items.append(voc)
                    if len(items) >= MAX_POSTS:
                        break
                    await self._random_delay()
                except Exception as e:
                    logger.debug(f"  Sanook article {url} 실패: {e}")

        # 최신순 정렬
        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(f"Sanook 수집 완료: {len(result)}건 (후보 {len(items)})")
        return result

    async def _fetch_list_page(
        self, client: httpx.AsyncClient, url: str
    ) -> List[str]:
        resp = await client.get(url, headers={"Referer": BASE_URL + "/hitech/"})
        if resp.status_code != 200:
            logger.debug(f"Sanook list HTTP {resp.status_code} {url}")
            return []
        html = resp.text
        urls = []
        seen_local: set = set()
        for m in POST_URL_RE.finditer(html):
            u = f"https://www.sanook.com/hitech/{m.group(1)}/"
            if u in seen_local:
                continue
            seen_local.add(u)
            urls.append(u)
        return urls

    async def _fetch_article(
        self, client: httpx.AsyncClient, url: str, post_id: str
    ) -> Optional[RawVOC]:
        resp = await client.get(url, headers={"Referer": TAG_URL})
        if resp.status_code != 200:
            return None
        html = resp.text

        # JSON-LD NewsArticle 파싱
        title, published_at, author, description = "", None, None, ""
        for block in JSONLD_RE.findall(html):
            try:
                d = json.loads(block.strip())
            except Exception:
                continue
            objs = d if isinstance(d, list) else [d]
            for obj in objs:
                if not isinstance(obj, dict):
                    continue
                t = obj.get("@type")
                if t == "NewsArticle" or t == "Article":
                    title = (obj.get("headline") or "").strip()
                    description = (obj.get("description") or "").strip()
                    dp = obj.get("datePublished")
                    if dp:
                        published_at = self._parse_iso(dp)
                    a = obj.get("author") or {}
                    if isinstance(a, dict):
                        author = (a.get("name") or "").strip() or None
                    elif isinstance(a, list) and a:
                        author = (a[0].get("name") or "").strip() or None
                    break
            if title:
                break

        # 본문 — <article class="EntryBody"> 내부 <p> 모음
        body = ""
        m = ARTICLE_RE.search(html)
        if m:
            inner = m.group(1)
            paras = re.findall(r"<p[^>]*>(.*?)</p>", inner, re.DOTALL)
            cleaned = []
            for p in paras:
                t = self._strip_html(p)
                # 공유/툴바 라인 제외
                if not t:
                    continue
                if len(t) < 12:
                    continue
                if any(skip in t for skip in (
                    "แชร์เรื่องนี้", "คัดลอกลิงก์", "Line Twitter Facebook",
                )):
                    continue
                cleaned.append(t)
            body = " ".join(cleaned)

        # 본문 없으면 description 으로라도
        if not body and description:
            body = description

        if len(body) > 4000:
            body = body[:4000]

        full_content = f"{title}\n{body}".strip() if body else title
        if len(full_content) < 20:
            return None

        external_id = hashlib.md5(f"sanook:{post_id}".encode()).hexdigest()[:16]

        return RawVOC(
            external_id=external_id,
            content=full_content,
            source_url=url,
            author_name=author,
            published_at=published_at,
            country_code="TH",
            meta={
                "post_id": post_id,
                "title": title,
                "source": "html",
            },
        )

    # --- helpers ---

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
    def _parse_iso(text: str) -> Optional[datetime]:
        """JSON-LD ISO8601 (예: '2026-05-27T12:27:11+07:00') → UTC.
        naive 일 경우 ICT (UTC+7) 가정."""
        if not text:
            return None
        try:
            # Python 3.11+ 의 fromisoformat 은 'Z' 도 지원하나 안전하게 치환
            t = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ICT)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    async def _main():
        c = SanookCrawler()
        vocs = await c.crawl()
        print(f"\n수집: {len(vocs)}건")
        for v in vocs[:5]:
            print(f"- [{v.external_id}] {v.author_name} @ {v.published_at}")
            print(f"  {v.source_url}")
            print(f"  {v.content[:120]}…")
    asyncio.run(_main())
