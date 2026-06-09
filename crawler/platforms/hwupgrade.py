"""
Hardware Upgrade 크롤러 — Google News RSS 우회 + Samsung/Galaxy 키워드 필터

hwupgrade.it 는 Cloudflare Turnstile 챌린지(JavaScript challenge, cf-mitigated:
challenge) 로 보호되어 httpx/curl/Playwright(headless) 모두 403 차단된다.
2026-05-31 6차 빌드도 같은 이유로 실패.

해결책: Google News RSS (news.google.com/rss/search?q=site:hwupgrade.it+<keyword>)
를 데이터 소스로 사용. Google News 가 Cloudflare 통과해 정상 인덱싱한 결과 중
Hardware Upgrade 기사만 추출. 본문 fetch 는 여전히 Cloudflare 가 차단하지만,
RSS 의 <title> 이 이탈리아어 평문 헤드라인(예: "Il nuovo Samsung Galaxy A27 è quasi
pronto al debutto: svelate tutte le specifiche - Hardware Upgrade") 으로 VOC
분석에 충분한 신호를 제공한다.

전략
  - Google News RSS 2개 쿼리 병합:
      site:hwupgrade.it samsung   (Samsung 일반)
      site:hwupgrade.it galaxy    (Galaxy 시리즈)
    각 ~100건 → dedupe → Galaxy/Samsung 키워드 필터
  - title 에서 trailing " - Hardware Upgrade" 시그니처 제거 후 VOC content
  - pubDate(GMT) → UTC (Google News 가 이미 GMT 로 정규화 제공)
  - source_url: Google News 캡슐 URL 사용 (원본 hwupgrade.it URL 은 캡슐 안
    base64 인코딩되어 있고 직접 디코딩이 비안정. 캡슐 URL 도 충분히 안정적 식별자)
  - external_id: GN guid (안정적 unique 식별자)
  - 댓글: 수집 불가 (Cloudflare 차단). 본문만 수집.

회고
  - 다른 IT 사이트들과 달리 hwupgrade.it 는 RSS 마저 Cloudflare 챌린지 페이지를
    반환. Forum 도 같은 인프라.
  - Playwright(Chromium headless) Cloudflare Turnstile 미통과. Firefox 미설치.
  - Google News 우회는 일관성 있는 대안 (gadgets360/xataka 가 Akamai RSS 통과
    한 사례와 비슷한 우회 전략).
"""
import hashlib
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

GN_RSS = (
    "https://news.google.com/rss/search?"
    "q=site:hwupgrade.it+{kw}&hl=it-IT&gl=IT&ceid=IT:it"
)

# Google News 쿼리 키워드 (Samsung/Galaxy 모두 시도)
SEARCH_TERMS = ["samsung", "galaxy"]

# 결과 캡 (LIST_PAGES=12 제약 없음 — GN 은 단일 페이지에 ~100건 제공)
MAX_POSTS = 150

# Galaxy/Samsung 키워드 필터 (이탈리아어/영어 공통)
# 일반어 단독(tab/watch/ring/fold) 은 false positive 회피 위해 제외.
GALAXY_KEYWORD_RE = re.compile(
    r"\b("
    r"samsung|galaxy"
    r"|one ?ui|oneui|bixby|exynos"
    r"|galaxy ?s\d{1,2}"
    r"|galaxy ?z ?fold|galaxy ?z ?flip|galaxy ?fold|galaxy ?flip"
    r"|galaxy ?(?:m|a|f|note)\d{1,2}"
    r"|galaxy ?buds|galaxy ?watch|galaxy ?tab|galaxy ?ring"
    r")\b",
    re.I,
)

# title 끝의 source 시그니처
TITLE_SUFFIX_RE = re.compile(r"\s*[-–]\s*Hardware Upgrade\s*$", re.I)


class HWUpgradeCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "hwupgrade", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            client.headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
            client.headers["Accept-Language"] = "it-IT,it;q=0.9,en;q=0.8"
            client.headers["Accept"] = (
                "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
            )

            for kw in SEARCH_TERMS:
                try:
                    posts = await self._fetch_gn_feed(client, kw)
                    items.extend(posts)
                    logger.info(
                        f"  HWUpgrade GN[{kw}]: {len(posts)}건"
                    )
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  HWUpgrade GN[{kw}] 실패: {e}")

        # dedupe by external_id (Google News guid)
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
            f"HWUpgrade 수집 완료: {len(result)}건 "
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
            logger.warning(f"HWUpgrade GN RSS 파싱 실패: {e}")
            return []

        results: List[RawVOC] = []
        for item in root.findall(".//item"):
            try:
                raw_title = (item.findtext("title") or "").strip()
                if not raw_title:
                    continue
                # trailing " - Hardware Upgrade" 제거
                title = TITLE_SUFFIX_RE.sub("", raw_title).strip()
                if not title:
                    continue

                link = (item.findtext("link") or "").strip()
                if not link:
                    continue

                guid_raw = (item.findtext("guid") or link).strip()

                pub_text = item.findtext("pubDate") or ""
                published_at = self._parse_rss_date(pub_text)

                # external_id: GN guid 가 안정적 (sha1-like base64).
                # md5 로 16자 축약 — DB 인덱스 일관.
                external_id = hashlib.md5(
                    f"hwupgrade#{guid_raw}".encode()
                ).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=external_id,
                    content=title,
                    source_url=link,
                    author_name=None,
                    published_at=published_at,
                    country_code="IT",
                    meta={
                        "guid": guid_raw,
                        "source": "google_news_rss",
                        "publisher": "Hardware Upgrade",
                    },
                ))
            except Exception as e:
                logger.debug(f"HWUpgrade item 파싱 실패: {e}")
        return results

    # ---------- helpers ----------

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = voc.content or ""
        if not text.strip():
            return False
        return bool(GALAXY_KEYWORD_RE.search(text))

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 'Sat, 30 May 2026 10:13:34 GMT' → UTC datetime.

        Google News 는 pubDate 를 항상 GMT 로 정규화 제공. 따라서 단순 parse 후
        UTC 변환만 수행. naive 인 경우(미발생 예상) Italy(CET/CEST)로 가정 후 UTC.
        """
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                # Italy CET(UTC+1) / CEST(UTC+2) 추정 폴백
                # 단순화: CET 고정 (Google News 는 항상 GMT 라 도달 거의 없음)
                from datetime import timedelta
                cet = timezone(timedelta(hours=1))
                dt = dt.replace(tzinfo=cet)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
