"""
ResetEra 크롤러 — Google News RSS 우회 (영문 게이밍·일반 IT 포럼)

www.resetera.com 은 Cloudflare Turnstile challenge (cf-mitigated: challenge)
로 보호되어 httpx/curl 모두 403 차단. NotebookCheck / hwupgrade 와 동일 패턴.

전략 (hwupgrade / notebookcheck 패턴 재사용)
  - Google News RSS 3개 쿼리 병합:
      site:resetera.com samsung
      site:resetera.com galaxy
      site:resetera.com galaxy fold     (제품 라인 강화)
  - title 에서 trailing " - ResetEra" 시그니처 제거 후 VOC content
  - external_id: Google News guid → md5 16자
  - country_code="US" — ResetEra 본사 미국, 영문 글로벌 포럼

회고
  - ResetEra 는 게이밍 커뮤니티지만 OT|Tech 섹션에 Samsung Galaxy 스레드가
    꾸준히 올라옴 (Fold/Flip 출시·번들 딜 등). Reddit Galaxy 보완.
  - 본문 fetch 없음 (Cloudflare). title 단독으로 VOC 분석.
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
    "q=site:resetera.com+{kw}&hl=en-US&gl=US&ceid=US:en"
)

# Google News 쿼리 키워드
SEARCH_TERMS = ["samsung", "galaxy", "galaxy+fold"]

# 결과 캡
MAX_POSTS = 150

# Galaxy/Samsung 키워드 필터 (영문)
# 주의: ResetEra 는 게이밍 포럼이라 "Mario Galaxy", "Guardians of the Galaxy",
# "Galaxy Watch (gaming)" 같은 *비-삼성 Galaxy* 가 다수 매칭됨.
# → 단독 "galaxy" 는 금지. 반드시 Samsung 컨텍스트 또는 제품 라인
#   (S\d{1,2}, Z Fold/Flip, Buds, A/M/F/Note 시리즈) 으로 한정.
# - "samsung" 단독은 OK (대부분 삼성 디바이스 토픽)
# - "galaxy" 단독은 NG (Mario Galaxy 등 오탐)
GALAXY_KEYWORD_RE = re.compile(
    r"\b("
    r"samsung"
    r"|one ?ui|oneui|bixby|exynos"
    r"|galaxy ?s\d{1,2}"
    r"|galaxy ?z ?fold|galaxy ?z ?flip|galaxy ?fold|galaxy ?flip"
    r"|galaxy ?(?:m|a|f|note)\d{1,2}"
    r"|galaxy ?buds|galaxy ?watch|galaxy ?tab|galaxy ?ring"
    r")\b",
    re.I,
)

# title 끝의 source 시그니처 (Google News 가 붙임): " - ResetEra"
TITLE_SUFFIX_RE = re.compile(r"\s*[-–]\s*ResetEra\s*$", re.I)


class ReseteraCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "resetera", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            client.headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
            client.headers["Accept-Language"] = "en-US,en;q=0.9"
            client.headers["Accept"] = (
                "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
            )

            for kw in SEARCH_TERMS:
                try:
                    posts = await self._fetch_gn_feed(client, kw)
                    items.extend(posts)
                    logger.info(f"  ResetEra GN[{kw}]: {len(posts)}건")
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  ResetEra GN[{kw}] 실패: {e}")

        # dedupe by external_id (동일 글이 여러 쿼리에서 잡힐 수 있음)
        seen: set = set()
        unique: List[RawVOC] = []
        for it in items:
            if it.external_id in seen:
                continue
            seen.add(it.external_id)
            unique.append(it)

        # Galaxy/Samsung 키워드 필터
        filtered = [v for v in unique if self._is_galaxy_related(v)]

        # 최신순 정렬 → MAX_POSTS
        filtered.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = filtered[:MAX_POSTS]
        logger.info(
            f"ResetEra 수집 완료: {len(result)}건 "
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
            logger.warning(f"ResetEra GN RSS 파싱 실패: {e}")
            return []

        results: List[RawVOC] = []
        for item in root.findall(".//item"):
            try:
                raw_title = (item.findtext("title") or "").strip()
                if not raw_title:
                    continue
                title = TITLE_SUFFIX_RE.sub("", raw_title).strip()
                if not title:
                    continue

                link = (item.findtext("link") or "").strip()
                if not link:
                    continue

                guid_raw = (item.findtext("guid") or link).strip()
                pub_text = item.findtext("pubDate") or ""
                published_at = self._parse_rss_date(pub_text)

                external_id = hashlib.md5(
                    f"resetera#{guid_raw}".encode()
                ).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=external_id,
                    content=title,
                    source_url=link,
                    author_name=None,
                    published_at=published_at,
                    country_code="US",
                    meta={
                        "guid": guid_raw,
                        "source": "google_news_rss",
                        "publisher": "ResetEra",
                    },
                ))
            except Exception as e:
                logger.debug(f"ResetEra item 파싱 실패: {e}")
        return results

    # ---------- helpers ----------

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = voc.content or ""
        if not text.strip():
            return False
        return bool(GALAXY_KEYWORD_RE.search(text))

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        """RFC822 → UTC. Google News 는 pubDate 를 항상 GMT 로 제공."""
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                # naive 일 경우 UTC 로 가정
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
