"""
4PDA 크롤러 — httpx + RSS (windows-1251)

4pda.to 는 러시아 최대 모바일/IT 커뮤니티 + 뉴스 사이트. 본래 IPB(Invision) 포럼
(showforum=235 Samsung) 에서 댓글까지 풍부하지만, 사이트 전체가 Cloudflare 챌린지
(cf-mitigated: challenge) 로 403 응답한다 — Firefox UA 포함 모든 UA 차단.

유일하게 200 OK 로 응답하는 안정 엔드포인트는 메인 RSS `/feed/` (windows-1251).
페이지네이션·태그 RSS 모두 차단되어 1회 ~30건 한계. Cloudflare 가 본문 페이지에도
랜덤하게 challenge 를 내려 본문 보강은 비결정적 → RSS description (티저 텍스트) 만으로
키워드 매치 + VOC 구성. 댓글 RSS(`#comments`) 도 403 으로 차단되어 본문만 수집.

전략 (MacRumors / TheVerge 의 RSS-only 패턴 차용)
  - `/feed/` 한 채널만 fetch → RSS의 title + description (HTML 태그 제거)
  - 러시아어/영문 Samsung·Galaxy 키워드로 필터
  - 본문 페이지 enrich 시도 (성공 시 article body 추가, 실패해도 RSS 유지)
"""
import hashlib
import html as html_lib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Optional
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://4pda.to"
FEED_URL = f"{BASE_URL}/feed/"

# 러시아 표준시 (MSK = UTC+3, 연중 고정 — 2014년부터 DST 폐지)
MSK = timezone(timedelta(hours=3))

# 최종 처리 캡 (메인 RSS 1채널만 가용)
MAX_POSTS = 150

# Samsung/Galaxy 관련 키워드 (영문 + 러시아어 — 4PDA 는 러시아어 사이트)
# 클리엔 패턴 따라 경쟁 브랜드(iPhone/Pixel/Honor/Xiaomi/Vivo/OPPO)도 비교용으로 포함 —
# 한 채널 RSS 30건 한정이라 휴대폰 카테고리 글을 폭넓게 잡아 정보 밀도 확보.
GALAXY_KEYWORDS = [
    # 영문 — Galaxy 핵심
    "samsung", "galaxy",
    "s27", "s26", "s25", "s24", "s23", "s22",
    "fold", "flip", "ultra", "buds", "watch", "ring",
    "one ui", "oneui", "exynos", "bixby", "z fold", "z flip",
    # 러시아어 음역 (Cyrillic — кириллица)
    "самсунг", "галакси", "галактика",
    "фолд", "флип", "ультра", "буд",
    "уан юай", "ванюай", "эксинос", "бикс",
    # 휴대폰 비교 (러시아 모바일 커뮤니티 특성상 폰 카테고리 글 폭넓게)
    "iphone", "айфон", "pixel", "пиксел",
    "honor", "хонор", "xiaomi", "сяоми", "ксиаоми",
    "vivo", "oppo", "оппо", "redmi", "редми",
    "смартфон", "флагман", "андроид", "android",
]


class FourPDACrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "4pda", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        async with self._make_httpx_client() as client:
            # Firefox UA + Cloudflare 친화 헤더. brotli 디코더 없으니 gzip 만.
            client.headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
                "Gecko/20100101 Firefox/125.0"
            )
            client.headers["Accept-Language"] = "ru-RU,ru;q=0.9,en;q=0.8"
            client.headers["Accept-Encoding"] = "gzip, deflate"

            # 1) 메인 RSS 한 번만 (페이지네이션·태그 RSS 모두 403)
            try:
                items = await self._fetch_feed(client)
            except Exception as e:
                logger.warning(f"4PDA RSS 실패: {e}")
                return []

            filtered = [it for it in items if self._is_galaxy_related(it)]
            logger.info(f"  4PDA RSS: {len(filtered)}/{len(items)}건 (Samsung/Galaxy)")

            # 2) link 중복 제거
            seen: set = set()
            unique: List[RawVOC] = []
            for it in filtered:
                if it.source_url in seen:
                    continue
                seen.add(it.source_url)
                unique.append(it)

            # 3) 최신순 → 상위 MAX_POSTS
            unique.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target = unique[:MAX_POSTS]

            # 4) 본문 페이지 enrich 시도 — Cloudflare 가 자주 challenge 내려서 베스트에포트
            enriched: List[RawVOC] = []
            for it in target:
                await self._random_delay()
                try:
                    body = await self._fetch_article_body(client, it.source_url)
                    if body and len(body) > len(it.content):
                        it.content = body
                except Exception as e:
                    logger.debug(f"  4PDA 본문 보강 실패 ({it.source_url}): {e}")
                enriched.append(it)

        logger.info(f"4PDA 수집 완료: {len(enriched)}건")
        return enriched

    # ----- RSS 피드 -----
    async def _fetch_feed(self, client: httpx.AsyncClient) -> List[RawVOC]:
        resp = await client.get(
            FEED_URL,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )
        resp.raise_for_status()
        # 4PDA RSS 는 windows-1251 인코딩 — httpx 가 charset 헤더 보고 자동 디코딩하지만
        # 보장을 위해 raw bytes 를 강제로 windows-1251 으로 디코딩.
        try:
            xml_text = resp.content.decode("windows-1251", errors="replace")
        except Exception:
            xml_text = resp.text
        return self._parse_rss(xml_text)

    def _parse_rss(self, xml_text: str) -> List[RawVOC]:
        # ET 는 windows-1251 XML 선언을 처리 가능하나, 우리는 이미 str 로 디코딩했으니
        # XML 선언의 encoding 속성을 제거해 'Unicode strings ... not allowed' 회피.
        xml_text = re.sub(r'<\?xml[^>]*\?>', '<?xml version="1.0"?>', xml_text, count=1)
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"4PDA RSS 파싱 실패: {e}")
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

                # 안정 ID: URL 의 마지막 숫자 article id (재크롤 중복 방지)
                m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/(\d+)/", link)
                stable = m.group(4) if m else hashlib.md5(link.encode()).hexdigest()[:12]
                external_id = hashlib.md5(f"{link}#{stable}".encode()).hexdigest()[:16]

                combined = f"{title}\n{desc}".strip() if desc else title

                results.append(RawVOC(
                    external_id=external_id,
                    content=combined,
                    source_url=link,
                    author_name=author,
                    published_at=published_at,
                    country_code="RU",
                    meta={"article_id": stable, "source": "rss"},
                ))
            except Exception as e:
                logger.debug(f"4PDA item 파싱 실패: {e}")

        return results

    # ----- 본문 보강 (베스트에포트) -----
    async def _fetch_article_body(
        self, client: httpx.AsyncClient, article_url: str
    ) -> Optional[str]:
        resp = await client.get(article_url, headers={"Referer": BASE_URL + "/"})
        # Cloudflare challenge → 본문 무시 (RSS desc 유지)
        if resp.status_code >= 400:
            return None
        # 응답 헤더에 cf-mitigated: challenge 면 콘텐츠가 챌린지 페이지
        if "challenge" in (resp.headers.get("cf-mitigated") or "").lower():
            return None

        try:
            html_text = resp.content.decode("windows-1251", errors="replace")
        except Exception:
            html_text = resp.text
        soup = BeautifulSoup(html_text, "html.parser")

        # 제목
        title = ""
        og_t = soup.find("meta", attrs={"property": "og:title"})
        if og_t and og_t.get("content"):
            title = og_t["content"].strip()
        elif soup.title:
            title = soup.title.get_text(strip=True)

        # 본문: schema.org Article itemtype → itemprop="description" 우선,
        # 없으면 og:description, 또 없으면 None.
        body_text = ""
        art = soup.find("article", attrs={"itemtype": re.compile("Article")})
        if art:
            desc_el = art.find(attrs={"itemprop": "description"})
            if desc_el:
                body_text = desc_el.get_text(" ", strip=True)
        if not body_text:
            og_d = soup.find("meta", attrs={"property": "og:description"})
            if og_d and og_d.get("content"):
                body_text = og_d["content"].strip()

        if not body_text and not title:
            return None
        return f"{title}\n{body_text}".strip() if body_text else title

    # ----- 필터/유틸 -----
    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        if not text.strip():
            return False
        return any(kw in text for kw in GALAXY_KEYWORDS)

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 'Sat, 30 May 2026 19:45:00 +0000' → UTC. naive 일 경우 MSK 가정."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=MSK)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
