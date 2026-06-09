"""
Tom's Guide 크롤러 — httpx + RSS (XML) + HTML 본문 보강

tomsguide.com 의 본문 기사(저널리즘 콘텐츠)에서 Samsung/Galaxy 관련 VOC 수집.

전략
  - 다수 RSS 피드(/feeds/tag/<tag>) 를 병합:
      samsung-galaxy, samsung-phones, galaxy-s26, foldable-phones, android-phones
    각 피드 50건씩 → 약 250 후보 (중복은 link 기준 제거).
  - description(요약 lead)은 기자가 쓴 평문 → 본문으로 우선 사용.
  - 추가로 상위 후보 일부는 HTML(`#article-body`) 에서 본문 보강.
  - 댓글 시스템 없음(레거시 댓글 폐쇄) → 본문 위주 수집.
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

BASE_URL = "https://www.tomsguide.com"
FEED_URL = "{base}/feeds/tag/{tag}"

# Samsung/Galaxy 관련 다중 태그 피드 (중복은 link 단위로 제거)
TOMSGUIDE_FEEDS = [
    ("samsung-galaxy",  "Samsung Galaxy Tag"),
    ("samsung-phones",  "Samsung Phones Tag"),
    ("galaxy-s26",      "Galaxy S26 Tag"),
    ("foldable-phones", "Foldable Phones Tag"),
    ("android-phones",  "Android Phones Tag"),
]

# HTML 본문 보강 대상 (RSS description 이 짧을 때 본문에서 추가 수집)
# 상위 N개 신규 후보만 fetch 해 과한 부하 방지
HTML_ENRICH_LIMIT = 30

# 최종 처리 캡
MAX_POSTS = 150

NS = {"dc": "http://purl.org/dc/elements/1.1/"}

GALAXY_KEYWORDS = [
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    "buds", "watch", "tab", "ring",
    "one ui", "oneui", "exynos", "bixby",
]


class TomsGuideCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "tomsguide", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "en-US,en;q=0.9"
            client.headers["Accept-Encoding"] = "gzip, deflate"

            # 1) 다중 RSS 피드 수집
            for tag, tag_name in TOMSGUIDE_FEEDS:
                try:
                    posts = await self._fetch_feed(client, tag)
                    filtered = [p for p in posts if self._is_galaxy_related(p)]
                    items.extend(filtered)
                    logger.info(
                        f"  TomsGuide RSS [{tag_name}]: {len(filtered)}/{len(posts)}건"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  TomsGuide RSS [{tag_name}] 실패: {e}")

            # link 단위 중복 제거 (먼저 1차 dedup → 보강 대상 선정)
            seen: set = set()
            unique: List[RawVOC] = []
            for it in items:
                if it.source_url in seen:
                    continue
                seen.add(it.source_url)
                unique.append(it)

            # 2) HTML 본문 보강 — 신규 글 중 본문이 짧은 항목만 보강
            unique.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            enrich_targets = [v for v in unique if len(v.content) < 300][:HTML_ENRICH_LIMIT]
            logger.info(
                f"  TomsGuide HTML 보강: {len(enrich_targets)}건 후보"
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
            f"TomsGuide 수집 완료: {len(result)}건 (후보 {len(items)} → 고유 {len(unique)})"
        )
        return result

    async def _fetch_feed(self, client: httpx.AsyncClient, tag: str) -> List[RawVOC]:
        url = FEED_URL.format(base=BASE_URL, tag=tag)
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
            logger.warning(f"TomsGuide RSS 파싱 실패: {e}")
            return []

        results: List[RawVOC] = []
        for item in root.findall(".//item"):
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title or not link:
                    continue

                guid = (item.findtext("guid") or link).strip()
                desc_raw = item.findtext("description") or ""
                desc = html_lib.unescape(desc_raw).strip()
                desc = re.sub(r"<[^>]+>", " ", desc)
                desc = re.sub(r"\s+", " ", desc).strip()

                pub_text = item.findtext("pubDate") or ""
                published_at = self._parse_rss_date(pub_text)

                creator_el = item.find("dc:creator", NS)
                author = (
                    creator_el.text.strip()
                    if creator_el is not None and creator_el.text
                    else None
                )

                # guid 가 안정적 article id (Futurenet CMS 짧은 hash)
                article_id = guid if guid and not guid.startswith("http") else \
                    hashlib.md5(link.encode()).hexdigest()[:12]

                external_id = hashlib.md5(
                    f"{link}#{article_id}".encode()
                ).hexdigest()[:16]

                content = f"{title}\n{desc}".strip() if desc else title

                results.append(RawVOC(
                    external_id=external_id,
                    content=content,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    country_code="US",
                    meta={"article_id": article_id, "guid": guid, "source": "rss"},
                ))
            except Exception as e:
                logger.debug(f"TomsGuide item 파싱 실패: {e}")

        return results

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

        # 본문 — #article-body 내부 p 태그
        body_parts: List[str] = []
        body_root = soup.find("div", id="article-body")
        if body_root is not None:
            for el in body_root.find_all(["p", "li"]):
                txt = el.get_text(" ", strip=True)
                if txt:
                    body_parts.append(txt)
        body = "\n".join(body_parts).strip()

        # fallback: og:description
        if not body:
            desc_el = soup.find("meta", attrs={"property": "og:description"})
            body = desc_el.get("content", "").strip() if desc_el else ""

        if not title and not body:
            return None

        # 본문 길이 제한 (longform 컷, 약 4000자)
        if len(body) > 4000:
            body = body[:4000]

        return f"{title}\n{body}".strip() if body else title

    # --- helpers ---

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        return any(kw in text for kw in GALAXY_KEYWORDS)

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 형식 'Sat, 30 May 2026 13:30:00 +0000' → UTC datetime"""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
