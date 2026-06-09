"""
GSMchoice 크롤러 — Google News RSS 우회 + Samsung/Galaxy 키워드 필터

gsmchoice.com (영국 모바일 DB+뉴스, 영문) 은 Cloudflare 챌린지(JS challenge,
"Just a moment..." 페이지)로 보호되어 httpx/curl/Googlebot/Firefox UA 모두
HTTP 403 으로 차단된다. robots.txt, sitemap.xml, /en/catalogue/samsung,
/en/news/, /feed/ 모두 동일. Cloudflare Turnstile/JS-challenge 통과를 위해서는
Playwright + 실 브라우저가 필요하나, 안정 운영을 위해 우회 데이터 소스를 채택.

해결책: Google News RSS (news.google.com/rss/search?q=site:gsmchoice.com+<keyword>)
를 데이터 소스로 사용. 1 쿼리당 ~100건 인덱스 결과를 받아 dedupe + Galaxy 키워드
필터. 본문 fetch 는 여전히 Cloudflare 차단이므로 RSS title (영문 헤드라인, 예
"Samsung Galaxy A31 Dual SIM technical specifications - GSMchoice.com") 만으로
VOC content 구성. 본 사이트가 카탈로그 중심이라 title 만으로도 모델/스펙
시그널 충분.

전략
  - Google News RSS 다중 쿼리 (samsung, galaxy, galaxy s, galaxy fold) 병합.
    각 ~100건 → external_id 기준 dedupe → Galaxy/Samsung 키워드 정밀 필터.
  - title 에서 trailing " - GSMchoice.com" / " - GSMchoice" 시그니처 제거.
  - pubDate (Google News 가 GMT 로 정규화 제공) → UTC.
  - source_url: Google News 캡슐 URL 사용 (원본 gsmchoice.com URL 은 캡슐
    base64 안에 있고 직접 디코딩이 비안정). 캡슐 URL 도 안정적 식별자.
  - external_id: GN guid 의 md5[:16] (안정 unique). 댓글 없음 (catalogue 사이트).
  - 본문 fetch 시도하지 않음 (Cloudflare 차단 확정). title 단독 VOC.
  - 본문 길이 < 20 자 컷 보호 (sammobile 동일 규약).

회고
  - 카탈로그 사이트 특성상 댓글 없음 → 본문 단독 1 글 = 1 VOC.
  - Google News 가 영국 GSMchoice 도 잘 인덱싱 (en-GB ceid=GB:en) → 한국형
    커뮤니티/포럼처럼 댓글 풍부하지 않은 catalogue 사이트엔 적합한 폴백.
  - hwupgrade.it (IT) 패턴과 동일 — Cloudflare 차단 사이트 표준 해법으로
    재사용 가능.
"""
import hashlib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Optional
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

GN_RSS = (
    "https://news.google.com/rss/search?"
    "q=site:gsmchoice.com+{kw}&hl=en-GB&gl=GB&ceid=GB:en"
)

# Google News 쿼리 키워드 — Samsung/Galaxy 라인업 분산 검색
SEARCH_TERMS = ["samsung", "galaxy", "galaxy+s", "galaxy+fold"]

# 정책: LIST_PAGES=12 (다른 RSS 페이지네이션 크롤러와 동일 규약).
# GN 은 페이지네이션 없이 단일 응답 ~100건 이므로 SEARCH_TERMS 개수로 분산.
LIST_PAGES = 12
MAX_POSTS = 150

# title 끝의 source 시그니처 (두 변형 모두 제거)
TITLE_SUFFIX_RE = re.compile(r"\s*[-–]\s*GSMchoice(?:\.com)?\s*$", re.I)

# Galaxy/Samsung 키워드 필터 (영문)
# false positive 회피 — 단독 'tab/watch/ring/fold' 는 제외하고 galaxy 와의 조합만.
GALAXY_KEYWORD_RE = re.compile(
    r"\b("
    r"samsung|galaxy"
    r"|one ?ui|oneui|bixby|exynos|tizen|knox"
    r"|galaxy ?s\d{1,2}"
    r"|galaxy ?z ?fold|galaxy ?z ?flip|galaxy ?fold|galaxy ?flip"
    r"|galaxy ?(?:m|a|f|j|note)\d{1,2}"
    r"|galaxy ?buds|galaxy ?watch|galaxy ?tab|galaxy ?ring"
    r")\b",
    re.I,
)


class GSMchoiceCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "gsmchoice", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            client.headers["Accept-Language"] = "en-GB,en;q=0.9"
            client.headers["Accept"] = (
                "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
            )

            for kw in SEARCH_TERMS:
                try:
                    posts = await self._fetch_gn_feed(client, kw)
                    items.extend(posts)
                    logger.info(
                        f"  GSMchoice GN[{kw}]: {len(posts)}건"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  GSMchoice GN[{kw}] 실패: {e}")

        # dedupe by external_id (Google News guid 기반 안정 ID)
        seen: set = set()
        unique: List[RawVOC] = []
        for it in items:
            if it.external_id in seen:
                continue
            seen.add(it.external_id)
            unique.append(it)

        # Galaxy/Samsung 키워드 필터 (제목 기준)
        filtered = [v for v in unique if self._is_galaxy_related(v)]

        # 최신순 정렬 → 상위 MAX_POSTS
        filtered.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = filtered[:MAX_POSTS]
        logger.info(
            f"GSMchoice 수집 완료: {len(result)}건 "
            f"(GN 후보 {len(items)} → 고유 {len(unique)} → Galaxy {len(filtered)})"
        )
        return result

    # ---------- fetchers ----------

    async def _fetch_gn_feed(
        self, client: httpx.AsyncClient, keyword: str
    ) -> List[RawVOC]:
        url = GN_RSS.format(kw=keyword)
        resp = await client.get(url)
        resp.raise_for_status()
        return self._parse_gn_feed(resp.text)

    # ---------- parsers ----------

    def _parse_gn_feed(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"GSMchoice GN RSS 파싱 실패: {e}")
            return []

        results: List[RawVOC] = []
        for item in root.findall(".//item"):
            try:
                raw_title = (item.findtext("title") or "").strip()
                if not raw_title:
                    continue
                # trailing " - GSMchoice.com" 제거
                title = TITLE_SUFFIX_RE.sub("", raw_title).strip()
                if not title or len(title) < 20:
                    # 너무 짧은 제목 컷 (sammobile 동일 규약)
                    continue

                link = (item.findtext("link") or "").strip()
                if not link:
                    continue

                guid_raw = (item.findtext("guid") or link).strip()

                pub_text = item.findtext("pubDate") or ""
                published_at = self._parse_rss_date(pub_text)

                # source 메타 (Google News <source> 태그 — 발행 매체명)
                source_el = item.find("source")
                publisher = (source_el.text or "").strip() if source_el is not None else "GSMchoice"

                # external_id: GN guid 가 안정적 unique. md5[:16] 축약 — DB 일관.
                external_id = hashlib.md5(
                    f"gsmchoice#{guid_raw}".encode()
                ).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=external_id,
                    content=title,
                    source_url=link,
                    author_name=None,
                    published_at=published_at,
                    country_code="GB",
                    meta={
                        "guid": guid_raw,
                        "source": "google_news_rss",
                        "publisher": publisher,
                    },
                ))
            except Exception as e:
                logger.debug(f"GSMchoice item 파싱 실패: {e}")
        return results

    # ---------- helpers ----------

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = voc.content or ""
        if not text.strip():
            return False
        return bool(GALAXY_KEYWORD_RE.search(text))

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 'Tue, 26 Aug 2025 08:14:41 GMT' → UTC datetime.

        Google News 는 pubDate 를 항상 GMT 로 정규화 제공. 단순 parse 후 UTC 변환.
        naive 인 경우(예상 없음) UTC 가정.
        """
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
