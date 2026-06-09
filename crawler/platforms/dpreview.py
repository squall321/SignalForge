"""
DPReview 크롤러 — httpx + RSS (XML)

dpreview.com 의 forums/ 와 모든 article 페이지는 Cloudflare 챌린지(`cf-mitigated: challenge`)
로 모든 UA에 대해 403 차단된다. 다만 본 사이트가 공개하는 RSS 피드는 200 OK 로
응답하며, description 에 기사 본문 HTML 이 통째로 들어있다(phonearena.py 와 동일 패턴).

전략
  - feeds/news.xml  : 모든 기사 (25건/요청, ?page=N 은 서버가 무시 → 단일 호출만)
  - feeds/reviews.xml: 카메라/스마트폰 리뷰 (50건/요청)
  - 두 피드 모두 description = 기사 풀 HTML → 태그 제거 후 본문으로 사용
  - 댓글은 forums.dpreview.com Cloudflare 차단 → 본문만 수집
  - DPReview 는 카메라 전문 사이트라 'Samsung/Galaxy' 빈도가 낮음 →
    스마트폰 카메라 비교(smartphone/phone/xiaomi/pixel/iphone/leica mobile) 글까지
    포함해 Galaxy 카메라 경쟁 컨텍스트 정보를 수집한다.
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

BASE_URL = "https://www.dpreview.com"

# 서버가 ?page= 파라미터를 무시하므로 단일 URL 만 사용 (확인 완료: page=2 ~ page=20 동일 결과)
DPREVIEW_FEEDS = [
    ("/feeds/news.xml",    "News"),     # 25건
    ("/feeds/reviews.xml", "Reviews"),  # 50건
]

# 최종 처리할 최대 글 수
MAX_POSTS = 150

NS = {
    "dc":      "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media":   "http://search.yahoo.com/mrss/",
    "a10":     "http://www.w3.org/2005/Atom",
}

# Galaxy/Samsung 직접 키워드 + 스마트폰 카메라 비교 컨텍스트 키워드.
# DPReview 는 카메라 전문이라 Samsung 직접 언급이 드물어 비교/생태계 글까지 폭넓게 수집.
GALAXY_KEYWORDS = [
    # Galaxy/Samsung 직접
    "galaxy", "samsung", "exynos", "one ui", "oneui",
    "s27", "s26", "s25", "s24", "s23",
    "fold", "flip", "ultra",
    # 스마트폰 카메라 비교 컨텍스트 (Galaxy 경쟁 정보)
    "smartphone", "iphone", "pixel", "xiaomi", "snapdragon", "android",
    "mobile photography", "phone camera", "phone photography",
]


class DPReviewCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "dpreview", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            # Cloudflare 우회 — RSS 엔드포인트는 관대하지만 'br' 디코더 누락 방지로 gzip/deflate 만 advertise.
            client.headers["Accept"] = "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
            client.headers["Accept-Encoding"] = "gzip, deflate"
            client.headers["Accept-Language"] = "en-US,en;q=0.9"

            for path, feed_name in DPREVIEW_FEEDS:
                try:
                    posts = await self._fetch_feed(client, path)
                    filtered = [p for p in posts if self._is_galaxy_related(p)]
                    items.extend(filtered)
                    logger.info(
                        f"  DPReview {feed_name}: {len(filtered)}/{len(posts)}건 (Galaxy/스마트폰 카메라)"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  DPReview {feed_name} 피드 실패: {e}")

        # link 단위 중복 제거 (두 피드 사이 겹침 가능)
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
            f"DPReview 수집 완료: {len(result)}건 (후보 {len(items)} → 고유 {len(unique)})"
        )
        return result

    async def _fetch_feed(
        self, client: httpx.AsyncClient, path: str
    ) -> List[RawVOC]:
        url = BASE_URL + path
        resp = await client.get(url, headers={"Referer": BASE_URL + "/"})
        resp.raise_for_status()
        return self._parse_feed(resp.text)

    def _parse_feed(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"DPReview RSS 파싱 실패: {e}")
            return []

        results: List[RawVOC] = []
        for item in root.findall(".//item"):
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title or not link:
                    continue

                guid = (item.findtext("guid") or link).strip()

                # description: 기사 풀 HTML (CDATA-디코딩 후 태그 제거)
                desc_raw = item.findtext("description") or ""
                desc = html_lib.unescape(desc_raw)
                # img/table 등 마크업 제거
                desc = re.sub(r"<[^>]+>", " ", desc)
                # 공백 정리
                desc = re.sub(r"\s+", " ", desc).strip()

                pub_text = item.findtext("pubDate") or ""
                published_at = self._parse_rss_date(pub_text)

                # author: dc:creator 가 비어있으면 None
                creator_el = item.find("dc:creator", NS)
                author = (
                    creator_el.text.strip()
                    if creator_el is not None and creator_el.text
                    else None
                )

                # article id 추출 (URL 패턴 /articles/<numeric_id>/<slug>)
                m = re.search(r"/(?:articles|news|reviews|opinion)/(\d+)/", link)
                article_id = m.group(1) if m else hashlib.md5(link.encode()).hexdigest()[:12]

                external_id = hashlib.md5(f"{link}#{article_id}".encode()).hexdigest()[:16]

                # 본문 길이 제한 (longform 기사 컷 — 약 4000자)
                if len(desc) > 4000:
                    desc = desc[:4000]

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
                logger.debug(f"DPReview item 파싱 실패: {e}")

        return results

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        return any(kw in text for kw in GALAXY_KEYWORDS)

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 'Sat, 30 May 2026 13:00:00 Z' → UTC datetime"""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
