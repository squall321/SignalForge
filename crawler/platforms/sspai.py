"""
sspai 少数派 크롤러 — CN 공백 보강 (Stage 5C T1)

sspai.com 은 중국 본토 디지털 생산성/소비전자 매체. /feed 200 OK 직접 응답.
ithome (CN) 이 이미 활성이지만 sspai 는 리뷰·생활기기 결이 달라 보완.

전략
  - /feed 단일 호출, 최근 ~20건. Samsung/Galaxy 한·영문 모두 매칭.
  - title + description 결합.
  - country_code="CN"

회고
  - sspai 는 Apple/Samsung/Sony 다 다루는 일반 디지털 매체.
    Galaxy 비중 추정 3-10%. 노이즈 차단 위해 키워드 필터 적용.
  - 한자/영문 혼합 매칭: '三星'(중국 표기) / 'Galaxy' / 'samsung' 다 OK.
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

RSS_URL = "https://sspai.com/feed"
MAX_POSTS = 100

# 中文 三星 + 영문 samsung/galaxy 둘 다 매칭
GALAXY_KEYWORD_RE = re.compile(
    r"(三星|"
    r"\b("
    r"samsung|galaxy"
    r"|one ?ui|oneui|bixby|exynos"
    r"|galaxy ?s\d{1,2}"
    r"|galaxy ?z ?fold|galaxy ?z ?flip|galaxy ?fold|galaxy ?flip"
    r"|galaxy ?(?:m|a|f|note)\d{1,2}"
    r"|galaxy ?buds|galaxy ?watch|galaxy ?tab|galaxy ?ring"
    r")\b)",
    re.I,
)


class SspaiCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "sspai", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        async with self._make_httpx_client() as client:
            client.headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
            client.headers["Accept-Language"] = "zh-CN,zh;q=0.9,en;q=0.8"
            client.headers["Accept"] = (
                "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
            )
            try:
                resp = await client.get(RSS_URL, follow_redirects=True)
                resp.raise_for_status()
                items = self._parse(resp.text)
                logger.info(f"  sspai RSS: {len(items)}건 (raw)")
            except Exception as e:
                logger.warning(f"  sspai RSS 실패: {e}")
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
            f"sspai 수집 완료: {len(result)}건 "
            f"(raw {len(items)} → uniq {len(unique)} → galaxy {len(filtered)})"
        )
        return result

    def _parse(self, xml_text: str) -> List[RawVOC]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"sspai RSS 파싱 실패: {e}")
            return []
        out: List[RawVOC] = []
        for item in root.findall(".//item"):
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = (item.findtext("description") or "").strip()
                author = (item.findtext("author") or "").strip() or None
                if not title or not link:
                    continue
                # HTML 태그 제거
                desc = re.sub(r"<[^>]+>", " ", desc)
                desc = re.sub(r"\s+", " ", desc).strip()
                content = title if not desc else f"{title}\n{desc}"
                pub_text = item.findtext("pubDate") or ""
                pub = self._parse_rss_date(pub_text)
                # sspai 는 guid 가 없음 — link 사용
                external_id = hashlib.md5(
                    f"sspai#{link}".encode()
                ).hexdigest()[:16]
                out.append(RawVOC(
                    external_id=external_id,
                    content=content,
                    source_url=link,
                    author_name=author,
                    published_at=pub,
                    country_code="CN",
                    meta={"guid": link, "source": "sspai_rss"},
                ))
            except Exception as e:
                logger.debug(f"sspai item 파싱 실패: {e}")
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
                # sspai 는 +0800 명시하므로 거의 사용 안 됨
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
