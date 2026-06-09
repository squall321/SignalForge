"""
Engadget 크롤러 — httpx + Sitemap + BeautifulSoup

전략
  1. /?getfeed=google (Google News sitemap, 최근 ~43건) + /post-sitemap1.xml (~500건)
     에서 article URL + lastmod 수집.
  2. 슬러그/제목에 Samsung/Galaxy 키워드를 만족하는 글만 필터.
  3. 본문 상세 페이지는 200 OK 로 접근 가능 (CloudFront, Cloudflare 없음).
     - 제목: <h1>
     - 본문: 두 번째 <div class="news-article"> (첫 번째는 dek/요약)
     - 발행: <meta property="article:published_time">
     - 저자: <meta name="author"> 또는 author byline
  4. 댓글: OpenWeb(Spot.IM) iframe 으로 JS 로딩됨 — httpx 로 접근 불가.
     → phonearena.py 와 마찬가지로 본문만 수집한다.

태그 페이지(/tag/samsung/) 는 301 → /tag/ 로 리다이렉트, RSS 카테고리 피드는 모두
메인 /feed/ 로 리다이렉트하므로 sitemap 이 유일한 대량 진입로.
"""
import hashlib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List, Optional
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.engadget.com"
SITEMAP_SOURCES = [
    ("google_news", f"{BASE_URL}/?getfeed=google"),
    ("post_index",  f"{BASE_URL}/post-sitemap1.xml"),
]

# 최대 상세 수집 글 수
MAX_POSTS = 150

NS = {
    "s": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "n": "http://www.google.com/schemas/sitemap-news/0.9",
}

# Samsung/Galaxy 정밀 필터 — "folders" 같은 false positive 회피용으로
# 단어경계(\b) + 명시적 토큰.
GALAXY_PATTERN = re.compile(
    r"(?:\b(?:samsung|galaxy|one-?ui|exynos|tizen|bixby|tab[- ]?s\d*|"
    r"s2[2-7](?:-?ultra)?|fold(?:-?\d)?|flip(?:-?\d)?|z-?fold|z-?flip|"
    r"galaxy-?watch|galaxy-?buds|galaxy-?ring)\b)",
    re.IGNORECASE,
)


class EngadgetCrawler(BaseCrawler):
    MIN_DELAY = 1.0
    MAX_DELAY = 2.5

    def __init__(self, platform_code: str = "engadget", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        async with self._make_httpx_client() as client:
            client.headers["Accept"] = (
                "application/xml, application/rss+xml, text/html;q=0.9, */*;q=0.8"
            )
            client.headers["Accept-Language"] = "en-US,en;q=0.9"
            client.headers["Referer"] = BASE_URL + "/"

            # 1) 모든 sitemap 에서 후보 URL 수집
            candidates: dict = {}  # url → (lastmod_str, title_from_sitemap)
            for src_name, url in SITEMAP_SOURCES:
                try:
                    entries = await self._fetch_sitemap(client, url)
                    for u, lm, t in entries:
                        if u not in candidates:
                            candidates[u] = (lm, t)
                    logger.info(f"  Engadget {src_name}: {len(entries)}건")
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  Engadget {src_name} 실패: {e}")

            # 2) Samsung/Galaxy 필터 (URL slug + 사용 가능 title)
            galaxy_urls: List[tuple] = []  # (url, lastmod_dt, sitemap_title)
            for url, (lm, t) in candidates.items():
                hay = f"{url} {t}"
                if GALAXY_PATTERN.search(hay):
                    galaxy_urls.append((url, self._parse_iso_date(lm), t))

            # 3) 최신순 정렬
            galaxy_urls.sort(
                key=lambda x: x[1] or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            targets = galaxy_urls[:MAX_POSTS]
            logger.info(
                f"Engadget 후보 {len(candidates)}건 → Galaxy 매칭 {len(galaxy_urls)}건 "
                f"→ 상위 {len(targets)}건 상세 수집"
            )

            raw_vocs: List[RawVOC] = []
            for url, _lm, _t in targets:
                await self._random_delay()
                try:
                    voc = await self._fetch_article(client, url)
                    if voc:
                        raw_vocs.append(voc)
                except Exception as e:
                    logger.warning(f"  Engadget 상세 실패 ({url}): {e}")

        logger.info(f"Engadget 수집 완료: {len(raw_vocs)}건")
        return raw_vocs

    async def _fetch_sitemap(
        self, client: httpx.AsyncClient, url: str
    ) -> List[tuple]:
        """sitemap XML → [(loc, lastmod_str, title_or_empty), ...]"""
        resp = await client.get(url)
        resp.raise_for_status()
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as e:
            logger.warning(f"Engadget sitemap 파싱 실패 ({url}): {e}")
            return []

        out: List[tuple] = []
        for u in root.findall("s:url", NS):
            loc_el = u.find("s:loc", NS)
            if loc_el is None or not loc_el.text:
                continue
            loc = loc_el.text.strip()
            # 상세글 URL 패턴: /숫자/slug/  — 홈 등 비-기사 제외
            if not re.match(r"^https?://www\.engadget\.com/\d{6,}/[^/]+/?$", loc):
                continue

            lm_el = u.find("s:lastmod", NS)
            lm = lm_el.text.strip() if lm_el is not None and lm_el.text else ""

            # Google news sitemap 은 news:title 보유
            title = ""
            t_el = u.find("n:news/n:title", NS)
            if t_el is not None and t_el.text:
                title = t_el.text.strip()
            # 또는 news:publication_date 가 lastmod 보다 정확
            pd_el = u.find("n:news/n:publication_date", NS)
            if pd_el is not None and pd_el.text:
                lm = pd_el.text.strip()

            out.append((loc, lm, title))
        return out

    async def _fetch_article(
        self, client: httpx.AsyncClient, url: str
    ) -> Optional[RawVOC]:
        resp = await client.get(url)
        if resp.status_code in (403, 451):
            logger.warning(f"  Engadget 차단 ({resp.status_code}) {url}")
            return None
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else ""

        # 본문: <div class="news-article"> 중 가장 긴 텍스트 보유 요소.
        # 첫 번째는 dek(요약 한 줄), 두 번째가 실제 본문이다.
        body_text = ""
        for na in soup.find_all("div", class_="news-article"):
            paras = [p.get_text(" ", strip=True) for p in na.find_all("p")]
            joined = "\n".join(p for p in paras if p)
            if len(joined) > len(body_text):
                body_text = joined

        # Fallback: <article class="news-post"> 전체에서 <p> 수집
        if len(body_text) < 100:
            art = soup.find("article", class_="news-post")
            if art:
                paras = [p.get_text(" ", strip=True) for p in art.find_all("p")]
                body_text = "\n".join(p for p in paras if p)

        content = f"{title}\n{body_text}".strip()
        if len(content) < 50:
            logger.debug(f"  Engadget 본문 빈약 ({url}): {len(content)}자")
            return None

        # 발행일
        published_at = None
        pt = soup.find("meta", property="article:published_time")
        if pt and pt.get("content"):
            published_at = self._parse_iso_date(pt["content"])

        # 저자
        author = None
        am = soup.find("meta", attrs={"name": "author"})
        if am and am.get("content"):
            author = am["content"].strip()
        if not author:
            byline = soup.find("a", href=lambda x: x and "/author/" in x)
            if byline:
                author = byline.get_text(strip=True)

        # external_id: URL 안의 article ID (예: /2183798/...) 우선, 없으면 url hash
        m = re.search(r"/(\d{6,})/", url)
        article_id = m.group(1) if m else hashlib.md5(url.encode()).hexdigest()[:8]
        external_id = hashlib.md5(f"{url}#{article_id}".encode()).hexdigest()[:16]

        logger.info(
            f"  Engadget 상세 {article_id}: 본문 {len(body_text)}자 "
            f"({title[:50]}...)"
        )
        return RawVOC(
            external_id=external_id,
            content=content,
            source_url=url,
            author_name=author,
            published_at=published_at,
            country_code="US",
            meta={"article_id": article_id},
        )

    def _parse_iso_date(self, text: str) -> Optional[datetime]:
        """ISO 8601 (예: '2026-05-28T14:45:35+00:00') → UTC datetime"""
        if not text:
            return None
        try:
            # Python 3.11+ fromisoformat 은 +00:00 정상 처리
            dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
