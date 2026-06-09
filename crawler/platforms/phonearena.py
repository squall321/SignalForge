"""
PhoneArena 크롤러 — httpx + RSS (XML)

phonearena.com 의 본문/댓글 페이지는 Cloudflare 챌린지로 차단(403)되지만,
RSS 피드(/feed/news)는 동일한 UA로도 200 OK 로 응답한다.

전략
  - /feed/news?tag=samsung&page=N  +  /feed/news?tag=galaxy&page=N  로 Samsung/Galaxy
    관련 기사 메타 + 요약(description) 을 수집한다.
  - description 이 사실상 기자가 쓴 본문 요약이므로 이를 본문으로 사용.
  - 상세 페이지/댓글은 Cloudflare 로 접근 불가 → 본문(요약)만 수집.
    (LIST_PAGES 를 늘려 정보 밀도 보강)
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

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.phonearena.com"
FEED_URL = "{base}/feed/news?tag={tag}{page}"

# Samsung/Galaxy 관련 글을 모으는 두 채널 (중복은 link 단위로 제거)
PHONEARENA_TAGS = [
    ("samsung", "Samsung Tag"),
    ("galaxy",  "Galaxy Tag"),
]

# 페이지 수: 피드당 50건 × N 페이지 (LIST_PAGES=12 → 최대 600 후보)
LIST_PAGES = 12
# 최종 처리할 최대 글 수
MAX_POSTS = 150

NS = {"dc": "http://purl.org/dc/elements/1.1/"}

GALAXY_KEYWORDS = [
    "galaxy", "samsung", "s27", "s26", "s25", "s24", "s23", "s22",
    "fold", "flip", "ultra", "buds", "watch", "tab",
    "one ui", "oneui", "exynos", "snapdragon",
]


class PhoneArenaCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "phonearena", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            # PhoneArena Cloudflare 는 압축 해제까지 요구
            client.headers["Accept-Encoding"] = "gzip, deflate, br"
            client.headers["Accept"] = "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
            client.headers["Accept-Language"] = "en-US,en;q=0.9"

            for tag, tag_name in PHONEARENA_TAGS:
                for page in range(1, LIST_PAGES + 1):
                    try:
                        posts = await self._fetch_feed(client, tag, page)
                        # 명백한 비-Galaxy 글 필터 (samsung 태그여도 Apple/Google 글이 섞임)
                        filtered = [p for p in posts if self._is_galaxy_related(p)]
                        items.extend(filtered)
                        logger.info(
                            f"  PhoneArena {tag_name} p{page}: {len(filtered)}/{len(posts)}건"
                        )
                        await self._random_delay()
                    except Exception as e:
                        logger.warning(
                            f"  PhoneArena {tag_name} p{page} 실패: {e}"
                        )

        # link 단위 중복 제거 (두 태그 모두 등장하는 글이 많음)
        seen: set = set()
        unique: List[RawVOC] = []
        for it in items:
            if it.source_url in seen:
                continue
            seen.add(it.source_url)
            unique.append(it)

        # 최신순 정렬 → 상위 MAX_POSTS
        unique.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = unique[:MAX_POSTS]
        logger.info(
            f"PhoneArena 수집 완료: {len(result)}건 (후보 {len(items)} → 고유 {len(unique)})"
        )
        return result

    async def _fetch_feed(
        self, client: httpx.AsyncClient, tag: str, page: int
    ) -> List[RawVOC]:
        # page=1 은 페이지 파라미터 없는 정규 URL 사용 (301 우회)
        page_q = "" if page == 1 else f"&page={page}"
        url = FEED_URL.format(base=BASE_URL, tag=tag, page=page_q)
        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        resp.raise_for_status()
        return self._parse_feed(resp.text)

    def _parse_feed(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"PhoneArena RSS 파싱 실패: {e}")
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
                # CDATA + entity decode
                desc = html_lib.unescape(desc_raw).strip()
                # HTML 태그 제거 (RSS description 은 평문 위주지만 보강)
                desc = re.sub(r"<[^>]+>", "", desc).strip()

                pub_text = item.findtext("pubDate") or ""
                published_at = self._parse_rss_date(pub_text)

                creator_el = item.find("dc:creator", NS)
                author = creator_el.text.strip() if creator_el is not None and creator_el.text else None

                # article id 추출 (예: _id180732)
                m = re.search(r"_id(\d+)", link)
                article_id = m.group(1) if m else hashlib.md5(link.encode()).hexdigest()[:8]

                external_id = hashlib.md5(f"{link}#{article_id}".encode()).hexdigest()[:16]

                content = f"{title}\n{desc}".strip() if desc else title

                results.append(RawVOC(
                    external_id=external_id,
                    content=content,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    country_code="US",
                    meta={"article_id": article_id, "guid": guid},
                ))
            except Exception as e:
                logger.debug(f"PhoneArena item 파싱 실패: {e}")

        return results

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        return any(kw in text for kw in GALAXY_KEYWORDS)

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 형식 'Fri, 29 May 2026 02:53:39 -0500' → UTC datetime"""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
