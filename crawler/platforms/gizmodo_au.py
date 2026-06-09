"""
Gizmodo Australia 크롤러 — httpx + RSS (XML) + HTML 본문 보강

배경
  - https://gizmodo.com.au 는 2024~2025 사이 본사로 통합되며 .au 도메인이
    https://gizmodo.com (메인) 으로 301 리다이렉트되도록 변경됨.
    (tag/samsung/ 도 동일하게 메인 루트로 리다이렉트)
  - 따라서 실제 콘텐츠 수집은 본사 WordPress 사이트(gizmodo.com)에서 수행하되,
    플랫폼 코드/리전은 task 사양대로 'gizmodo_au' / 'AU' 로 등록.
  - 영문권 매체 — Australia/AU 사용자 트래픽이 합쳐진 글로벌 영문 풀에서
    Samsung/Galaxy 관련 글을 수집한다.

전략
  - WordPress 표준 태그 RSS 활용: /tag/<slug>/feed
      samsung, samsung-galaxy, galaxy, galaxy-fold, galaxy-watch
  - 각 피드는 보통 20건/요청. 다중 태그 병합 후 link 단위 중복 제거.
  - RSS description 은 lead 한 줄 → 상위 N 개는 HTML 본문(`div.entry-content`)
    파싱으로 보강하여 분석 정보 밀도 확보.
  - 댓글: 사이트가 `#respond` 만 노출(`slash:comments` 모두 0). 댓글 시스템이
    실질적으로 폐쇄되어 있어 본문 위주 수집.
  - 봇 차단 없음(Cloudflare 통과). UA rotation + Referer 만 일반 브라우저로 흉내.
  - Galaxy/Samsung 키워드 필터로 비-Galaxy 글 컷.
"""
import hashlib
import html as html_lib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

# gizmodo.com.au → gizmodo.com 으로 통합됨. 실제 RSS/HTML 은 본사 호스트에서 수집.
BASE_URL = "https://gizmodo.com"
FEED_URL = "{base}/tag/{slug}/feed"

# Samsung/Galaxy 관련 다중 태그 (중복은 link 단위로 제거)
GIZMODO_AU_FEEDS = [
    ("samsung",         "Samsung Tag"),
    ("samsung-galaxy",  "Samsung Galaxy Tag"),
    ("galaxy",          "Galaxy Tag"),
    ("galaxy-fold",     "Galaxy Fold Tag"),
    ("galaxy-watch",    "Galaxy Watch Tag"),
]

# RSS description 이 짧을 때 본문 보강 대상 건수 (상위 N)
HTML_ENRICH_LIMIT = 30
# 최종 처리 캡
MAX_POSTS = 150
# 본문 길이 제한 (longform 컷)
MAX_BODY_CHARS = 4000

NS = {
    "dc":      "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "slash":   "http://purl.org/rss/1.0/modules/slash/",
}

GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
]


class GizmodoAUCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "gizmodo_au", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "en-AU,en;q=0.9"
            client.headers["Accept-Encoding"] = "gzip, deflate"

            # 1) 다중 RSS 피드 수집
            for slug, tag_name in GIZMODO_AU_FEEDS:
                try:
                    posts = await self._fetch_feed(client, slug)
                    filtered = [p for p in posts if self._is_galaxy_related(p)]
                    items.extend(filtered)
                    logger.info(
                        f"  GizmodoAU RSS [{tag_name}]: {len(filtered)}/{len(posts)}건"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  GizmodoAU RSS [{tag_name}] 실패: {e}")

            # link 단위 중복 제거
            seen: set = set()
            unique: List[RawVOC] = []
            for it in items:
                if it.source_url in seen:
                    continue
                seen.add(it.source_url)
                unique.append(it)

            # 2) 최신순 정렬 → 본문이 짧은 항목 HTML 보강
            unique.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            enrich_targets = [v for v in unique if len(v.content) < 300][:HTML_ENRICH_LIMIT]
            logger.info(
                f"  GizmodoAU HTML 보강: {len(enrich_targets)}건 후보"
            )
            for voc in enrich_targets:
                try:
                    body = await self._fetch_article_body(client, voc.source_url)
                    if body and len(body) > len(voc.content):
                        voc.content = body
                    await self._random_delay()
                except Exception as e:
                    logger.debug(f"    article {voc.source_url} 보강 실패: {e}")

        result = unique[:MAX_POSTS]
        logger.info(
            f"GizmodoAU 수집 완료: {len(result)}건 (후보 {len(items)} → 고유 {len(unique)})"
        )
        return result

    # ---------- RSS 피드 ----------
    async def _fetch_feed(self, client: httpx.AsyncClient, slug: str) -> List[RawVOC]:
        url = FEED_URL.format(base=BASE_URL, slug=slug)
        resp = await client.get(
            url,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )
        resp.raise_for_status()
        return self._parse_feed(resp.text)

    def _parse_feed(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"GizmodoAU RSS 파싱 실패: {e}")
            return []

        results: List[RawVOC] = []
        for item in root.findall(".//item"):
            try:
                title = html_lib.unescape((item.findtext("title") or "").strip())
                link = (item.findtext("link") or "").strip()
                if not title or not link:
                    continue

                guid = (item.findtext("guid") or link).strip()

                # content:encoded 가 있으면 우선 (description 과 거의 동일하지만 안전망)
                ce_el = item.find("content:encoded", NS)
                body_html = (
                    (ce_el.text if ce_el is not None and ce_el.text else "")
                    or (item.findtext("description") or "")
                )
                desc = self._strip_html(body_html)

                pub_text = item.findtext("pubDate") or ""
                published_at = self._parse_rss_date(pub_text)

                creator_el = item.find("dc:creator", NS)
                author = (
                    creator_el.text.strip()
                    if creator_el is not None and creator_el.text
                    else None
                )

                # 댓글 수 (대부분 0)
                slash_el = item.find("slash:comments", NS)
                comments_count = 0
                if slash_el is not None and slash_el.text:
                    try:
                        comments_count = int(slash_el.text.strip())
                    except ValueError:
                        comments_count = 0

                # WP guid 는 ?p=<post_id> 형태 — post_id 가 가장 안정적
                post_id = self._post_id_from_guid(guid) or self._slug_from_url(link)
                external_id = hashlib.md5(
                    f"{link}#{post_id}".encode()
                ).hexdigest()[:16]

                content = f"{title}\n{desc}".strip() if desc else title

                results.append(RawVOC(
                    external_id=external_id,
                    content=content,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    comments_count=comments_count,
                    country_code="AU",
                    meta={"post_id": post_id, "guid": guid, "source": "rss"},
                ))
            except Exception as e:
                logger.debug(f"GizmodoAU item 파싱 실패: {e}")

        return results

    # ---------- HTML 본문 ----------
    async def _fetch_article_body(
        self, client: httpx.AsyncClient, url: str
    ) -> Optional[str]:
        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        # 제목 (og:title 우선)
        title_el = soup.find("meta", attrs={"property": "og:title"})
        title = title_el.get("content", "").strip() if title_el else ""
        if not title:
            h1 = soup.find("h1")
            title = h1.get_text(strip=True) if h1 else ""

        # 본문 — div.entry-content 내 p, li
        body_parts: List[str] = []
        body_root = soup.find("div", class_="entry-content")
        if body_root is not None:
            for el in body_root.find_all(["p", "li"]):
                txt = el.get_text(" ", strip=True)
                if not txt or len(txt) < 20:
                    continue
                tl = txt.lower()
                # 카피라이트/배너 boilerplate 컷
                if any(k in tl for k in (
                    "©2026 gizmodo",
                    "©2025 gizmodo",
                    "all rights reserved",
                    "subscribe to our newsletter",
                )):
                    continue
                body_parts.append(txt)
        body = "\n".join(body_parts).strip()

        # fallback: og:description
        if not body:
            desc_el = soup.find("meta", attrs={"property": "og:description"})
            body = desc_el.get("content", "").strip() if desc_el else ""

        if not title and not body:
            return None

        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS]

        return f"{title}\n{body}".strip() if body else title

    # ---------- helpers ----------
    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        return any(kw in text for kw in GALAXY_KEYWORDS)

    @staticmethod
    def _strip_html(s: str) -> str:
        if not s:
            return ""
        decoded = html_lib.unescape(s)
        no_tags = re.sub(r"<[^>]+>", " ", decoded)
        return re.sub(r"\s+", " ", no_tags).strip()

    @staticmethod
    def _post_id_from_guid(guid: str) -> Optional[str]:
        # WP guid 형식: https://gizmodo.com/?p=2000764790
        m = re.search(r"[?&]p=(\d+)", guid or "")
        return m.group(1) if m else None

    @staticmethod
    def _slug_from_url(url: str) -> str:
        # /samsung-foo-bar-2000764790  → 마지막 슬러그 사용
        m = re.search(r"gizmodo\.com/([a-z0-9][a-z0-9-]+)/?", url or "")
        return m.group(1) if m else hashlib.md5((url or "").encode()).hexdigest()[:12]

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 'Fri, 29 May 2026 12:00:25 +0000' → UTC datetime"""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
