"""
MacRumors 크롤러 — httpx + RSS + 본문 보강

forums.macrumors.com (XenForo) 의 Alternatives-to-iOS (Android) 서브포럼은
Cloudflare 챌린지(403 cf-mitigated)로 모든 UA에 대해 완전 차단된다.
대안으로 본 사이트의 RSS 피드(feeds.macrumors.com)는 200 OK 로 응답하며,
www.macrumors.com 의 본문 페이지(/YYYY/MM/DD/slug/)도 200 으로 접근 가능.

전략 (PhoneArena 패턴 참고)
  - feeds.macrumors.com 의 여러 채널을 순회하며 Samsung/Galaxy/Android 키워드로 필터
  - 매치된 기사는 www.macrumors.com 본문 페이지에서 <article> 전체 텍스트를 추가 수집
  - 댓글은 forums.macrumors.com 의존이라 Cloudflare 차단 → 수집 불가, 본문만
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

FEED_BASE = "https://feeds.macrumors.com"
SITE_BASE = "https://www.macrumors.com"

# MacRumors 가 공식 제공하는 RSS 채널 (모두 200 OK 확인됨)
MACRUMORS_FEEDS = [
    ("MacRumors-All",   "All Stories"),
    ("MacRumors-Front", "Front Page"),
    ("MacRumors-iOS",   "iOS"),
]

# 피드 자체 페이지네이션 지원 안 함 → 한 번에 ~20-50건. 본문 enrich 단계가 비용 큼.
MAX_POSTS = 150

# Samsung/Galaxy/Android 관련 글 필터 (영문 사이트, MacRumors 는 Apple 중심이라
# 비교/뉴스/딜 기사에서만 등장 → 키워드 적중률이 낮아 폭넓게 설정)
GALAXY_KEYWORDS = [
    "galaxy", "samsung", "android",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra", "buds", "one ui", "oneui",
    "exynos", "snapdragon", "pixel",  # 비교/안드로이드 생태계 토론
]


class MacRumorsCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "macrumors", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            # Cloudflare 우회 헤더 — RSS endpoint 는 관대.
            # 주의: httpx 가 brotli 디코더 없으면 'br' 응답을 풀지 못해 RSS 파싱 0건이 됨.
            client.headers["Accept-Encoding"] = "gzip, deflate"
            client.headers["Accept-Language"] = "en-US,en;q=0.9"

            # 1) RSS 피드들에서 후보 수집
            for feed_path, feed_name in MACRUMORS_FEEDS:
                try:
                    feed_items = await self._fetch_feed(client, feed_path)
                    filtered = [it for it in feed_items if self._is_galaxy_related(it)]
                    items.extend(filtered)
                    logger.info(
                        f"  MacRumors {feed_name}: {len(filtered)}/{len(feed_items)}건 (Samsung/Android)"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  MacRumors {feed_name} 피드 실패: {e}")

            # 2) link 단위 중복 제거 (여러 피드에 중복 등장)
            seen: set = set()
            unique: List[RawVOC] = []
            for it in items:
                if it.source_url in seen:
                    continue
                seen.add(it.source_url)
                unique.append(it)

            # 3) 최신순 → 상위 MAX_POSTS 만 본문 보강
            unique.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target = unique[:MAX_POSTS]
            logger.info(
                f"MacRumors 후보 {len(items)} → 고유 {len(unique)} → 본문 보강 {len(target)}건"
            )

            # 4) 본문 페이지에서 article 전체 텍스트 보강 (Cloudflare 우회 안 되면 RSS desc 만 유지)
            enriched: List[RawVOC] = []
            for it in target:
                await self._random_delay()
                try:
                    body = await self._fetch_article_body(client, it.source_url)
                    if body and len(body) > len(it.content):
                        it.content = body
                except Exception as e:
                    logger.debug(f"  MacRumors 본문 보강 실패 ({it.source_url}): {e}")
                enriched.append(it)

        logger.info(f"MacRumors 수집 완료: {len(enriched)}건")
        return enriched

    # ----- RSS 피드 -----
    async def _fetch_feed(self, client: httpx.AsyncClient, feed_path: str) -> List[RawVOC]:
        url = f"{FEED_BASE}/{feed_path}"
        resp = await client.get(url, headers={
            "Referer": SITE_BASE + "/",
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        })
        resp.raise_for_status()
        return self._parse_feed(resp.text)

    def _parse_feed(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"MacRumors RSS 파싱 실패: {e}")
            return []

        ns = {"dc": "http://purl.org/dc/elements/1.1/"}
        results: List[RawVOC] = []

        for item in root.findall(".//item"):
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title or not link:
                    continue

                desc_raw = item.findtext("description") or ""
                desc = html_lib.unescape(desc_raw)
                desc = re.sub(r"<[^>]+>", " ", desc)
                desc = re.sub(r"\s+", " ", desc).strip()

                pub_text = item.findtext("pubDate") or ""
                published_at = self._parse_rss_date(pub_text)

                creator_el = item.find("dc:creator", ns)
                author = (
                    creator_el.text.strip()
                    if creator_el is not None and creator_el.text
                    else None
                )

                cats = [c.text for c in item.findall("category") if c.text]
                # 키워드 매칭 시 카테고리도 함께 보도록 본문에 합쳐 보관
                combined = f"{title}\n{desc}".strip()

                # 안정 ID: 기사 URL 의 마지막 slug 부분 (재크롤 시 중복 방지)
                m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/([a-z0-9-]+)/?$", link)
                stable = m.group(4) if m else hashlib.md5(link.encode()).hexdigest()[:12]
                external_id = hashlib.md5(f"{link}#{stable}".encode()).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=external_id,
                    content=combined,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    country_code="US",
                    meta={"categories": cats, "slug": stable},
                ))
            except Exception as e:
                logger.debug(f"MacRumors item 파싱 실패: {e}")

        return results

    # ----- 본문 보강 -----
    async def _fetch_article_body(
        self, client: httpx.AsyncClient, article_url: str
    ) -> Optional[str]:
        resp = await client.get(article_url, headers={"Referer": SITE_BASE + "/"})
        # Cloudflare 차단 가능 — 403/503 시 None 반환 후 RSS desc 유지
        if resp.status_code >= 400:
            return None
        soup = BeautifulSoup(resp.text, "lxml")

        # 제목
        title = ""
        h1 = soup.select_one("h1") or soup.select_one("article h1")
        if h1:
            title = h1.get_text(strip=True)

        # 본문: MacRumors 는 <article> 단일 컨테이너 사용
        article_el = soup.select_one("article")
        if not article_el:
            return None

        # 스크립트/광고/관련글 위젯 제거
        for trash in article_el.select("script, style, .widget, .nextarticle, aside, .skim-block"):
            trash.decompose()

        body_text = article_el.get_text("\n", strip=True)
        body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()

        if not body_text:
            return None
        return f"{title}\n{body_text}".strip() if title else body_text

    # ----- 필터/유틸 -----
    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        cats = " ".join(voc.meta.get("categories") or []).lower()
        haystack = f"{text} {cats}"
        return any(kw in haystack for kw in GALAXY_KEYWORDS)

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 'Fri, 29 May 2026 10:26:49 PDT' → UTC datetime"""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
