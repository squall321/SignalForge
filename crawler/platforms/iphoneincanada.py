"""
iPhone in Canada 크롤러 — CA 공백 보강 (Stage 5C T1)

iphoneincanada.ca: 캐나다 Apple/통신/기술/거래 매체. WordPress 표준 RSS
(/feed/) 200 OK 직접 응답.

전략
  - /feed/ 최신 ~25건 fetch → Samsung/Galaxy 키워드 필터.
  - 이름은 Apple 중심이지만 Samsung/통신사 비교 기사도 정기적으로 발행됨.
  - title + description (content:encoded 가능하면 보강) 결합.
  - country_code="CA"

회고
  - Apple 매체라 Galaxy 비중 낮을 것 — 1-5% 추정. 페이지네이션 없음 (RSS 1페이지).
  - mobilesyrup (CA) 가 이미 활성, 본 매체는 발행 빈도 보완 역할.
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

RSS_URL = "https://www.iphoneincanada.ca/feed/"
MAX_POSTS = 100

# WordPress content:encoded namespace
NS = {"content": "http://purl.org/rss/1.0/modules/content/",
      "dc": "http://purl.org/dc/elements/1.1/"}

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


class IPhoneInCanadaCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "iphoneincanada", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        async with self._make_httpx_client() as client:
            client.headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
            client.headers["Accept-Language"] = "en-CA,en;q=0.9"
            client.headers["Accept"] = (
                "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
            )
            try:
                resp = await client.get(RSS_URL, follow_redirects=True)
                resp.raise_for_status()
                items = self._parse(resp.text)
                logger.info(f"  iPhoneInCanada RSS: {len(items)}건 (raw)")
            except Exception as e:
                logger.warning(f"  iPhoneInCanada RSS 실패: {e}")
                return []

        seen: set = set()
        unique: List[RawVOC] = []
        for it in items:
            if it.external_id in seen:
                continue
            seen.add(it.external_id)
            unique.append(it)

        filtered = [v for v in unique if self._is_galaxy_related(v)]
        filtered.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = filtered[:MAX_POSTS]
        logger.info(
            f"iPhoneInCanada 수집 완료: {len(result)}건 "
            f"(raw {len(items)} → uniq {len(unique)} → galaxy {len(filtered)})"
        )
        return result

    def _parse(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"iPhoneInCanada RSS 파싱 실패: {e}")
            return []
        out: List[RawVOC] = []
        for item in root.findall(".//item"):
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = (item.findtext("description") or "").strip()
                author = (item.findtext("dc:creator", namespaces=NS) or "").strip() or None
                if not title or not link:
                    continue
                # content:encoded 가 있으면 description 대체 (더 풍부)
                enc = item.findtext("content:encoded", namespaces=NS)
                body = (enc or desc or "").strip()
                # HTML 태그 단순 제거 (BaseCrawler 측 정제기 있을 수 있으나 안전 차원)
                body = re.sub(r"<[^>]+>", " ", body)
                body = re.sub(r"\s+", " ", body).strip()
                content = title if not body else f"{title}\n{body[:1200]}"
                guid_raw = (item.findtext("guid") or link).strip()
                pub_text = item.findtext("pubDate") or ""
                pub = self._parse_rss_date(pub_text)
                external_id = hashlib.md5(
                    f"iphoneincanada#{guid_raw}".encode()
                ).hexdigest()[:16]
                out.append(RawVOC(
                    external_id=external_id,
                    content=content,
                    source_url=link,
                    author_name=author,
                    published_at=pub,
                    country_code="CA",
                    meta={"guid": guid_raw, "source": "iphoneincanada_rss"},
                ))
            except Exception as e:
                logger.debug(f"iPhoneInCanada item 파싱 실패: {e}")
        return out

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = voc.content or ""
        if not text.strip():
            return False
        return bool(GALAXY_KEYWORD_RE.search(text))

    def _parse_rss_date(self, text: str) -> Optional[datetime]:
        if not text:
            return None
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
