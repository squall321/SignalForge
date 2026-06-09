"""
nu.nl 크롤러 — NL 공백 보강 (Stage 5C T1)

nu.nl 은 네덜란드 최대 종합/IT 뉴스 매체. /rss/Tech 는 200 OK 직접 응답.
RSS 2.0 표준 — title/link/description/pubDate/guid.

전략
  - /rss/Tech 한 곳에서 최신 ~50건 가져와 Samsung/Galaxy 키워드 필터.
  - tweakers (NL) 가 이미 활성이지만 Cloudflare 우회용 Google News 라
    nu.nl 직접 RSS 는 신선도 보완.
  - country_code="NL"
  - title + description 결합해 VOC content (RSS 본문 짧음).

회고
  - 모바일 전문 매체 아닌 일반지라 Galaxy 비중 5-15% 예상.
    엄격한 키워드 필터로 노이즈 차단.
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

RSS_URL = "https://www.nu.nl/rss/Tech"
MAX_POSTS = 100

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


class NuNLCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "nu_nl", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        async with self._make_httpx_client() as client:
            client.headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
            client.headers["Accept-Language"] = "nl-NL,nl;q=0.9,en;q=0.8"
            client.headers["Accept"] = (
                "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
            )
            try:
                resp = await client.get(RSS_URL, follow_redirects=True)
                resp.raise_for_status()
                items = self._parse(resp.text)
                logger.info(f"  nu.nl RSS: {len(items)}건 (raw)")
            except Exception as e:
                logger.warning(f"  nu.nl RSS 실패: {e}")
                return []

        # dedupe by external_id
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
            f"nu.nl 수집 완료: {len(result)}건 "
            f"(raw {len(items)} → uniq {len(unique)} → galaxy {len(filtered)})"
        )
        return result

    def _parse(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"nu.nl RSS 파싱 실패: {e}")
            return []
        out: List[RawVOC] = []
        for item in root.findall(".//item"):
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = (item.findtext("description") or "").strip()
                if not title or not link:
                    continue
                guid_raw = (item.findtext("guid") or link).strip()
                pub_text = item.findtext("pubDate") or ""
                pub = self._parse_rss_date(pub_text)
                content = title if not desc else f"{title}\n{desc}"
                external_id = hashlib.md5(
                    f"nu_nl#{guid_raw}".encode()
                ).hexdigest()[:16]
                out.append(RawVOC(
                    external_id=external_id,
                    content=content,
                    source_url=link,
                    author_name=None,
                    published_at=pub,
                    country_code="NL",
                    meta={"guid": guid_raw, "source": "nu_nl_rss"},
                ))
            except Exception as e:
                logger.debug(f"nu.nl item 파싱 실패: {e}")
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
