# 종합뉴스 크롤러 — Google News RSS 로 삼성/갤럭시 메인스트림 보도를 다국어·이슈쿼리로 수집
"""
General News 크롤러 — Google News RSS (site: 제한 없음).

기존 GN RSS 크롤러(resetera/notebookcheck 등)는 `site:<도메인>` 으로 특정 매체에 한정했다.
이 크롤러는 그 제한을 빼고 삼성/갤럭시 관련 쿼리를 전 매체(연합뉴스·조선·Reuters·CNN·
BBC 등)에서 다국어로 수집한다 — 종합지의 삼성 제품 이슈·결함·리콜 보도 커버리지.

특징
====
- 다국어 로케일(ko/en/+) × 일반 + 이슈(결함/리콜/발화/배터리) 쿼리 fan-out.
- pubDate(RFC822) 로 원 게시일 정확. <source> 요소로 매체명 보존.
- GN 인덱스는 최근성 위주(옛 기사 소급은 제한적) → 최신 보도 실시간 추적이 주 목적.

플랫폼 코드: googlenews  (alembic platforms row)
"""
import hashlib
import re
import sys
import os
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC  # noqa: E402

logger = logging.getLogger(__name__)

_GN = "https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"
_TITLE_SUFFIX = re.compile(r"\s+-\s+[^-]+$")  # "제목 - 매체명" 의 매체 접미 제거
_GALAXY = re.compile(
    r"삼성|갤럭시|samsung|galaxy|z\s?fold|z\s?flip|갤워치|버즈|galaxy\s?watch|galaxy\s?buds"
    r"|아이폰|iphone|apple|ipad|airpods",
    re.IGNORECASE,
)

# (검색어, hl, gl, ceid, country) — 일반 + 이슈 쿼리, 다국어. env YOUTUBE 식 override 불필요.
_QUERIES = [
    # 한국 종합지
    ("삼성 갤럭시", "ko-KR", "KR", "KR:ko", "KR"),
    ("갤럭시 결함", "ko-KR", "KR", "KR:ko", "KR"),
    ("갤럭시 리콜", "ko-KR", "KR", "KR:ko", "KR"),
    ("갤럭시 발화", "ko-KR", "KR", "KR:ko", "KR"),
    ("갤럭시 배터리 문제", "ko-KR", "KR", "KR:ko", "KR"),
    # 영어권 종합지
    ("Samsung Galaxy", "en-US", "US", "US:en", "US"),
    ("Samsung Galaxy recall", "en-US", "US", "US:en", "US"),
    ("Samsung Galaxy defect", "en-US", "US", "US:en", "US"),
    ("Samsung Galaxy battery issue", "en-US", "US", "US:en", "US"),
    ("Samsung Galaxy overheating", "en-GB", "GB", "GB:en", "GB"),
    # Apple iPhone — 경쟁사 결함·리콜 보도 커버리지(시리즈별 비교분석용).
    ("아이폰 결함", "ko-KR", "KR", "KR:ko", "KR"),
    ("iPhone defect", "en-US", "US", "US:en", "US"),
    ("iPhone recall", "en-US", "US", "US:en", "US"),
    ("iPhone battery issue", "en-US", "US", "US:en", "US"),
    ("iPhone overheating", "en-US", "US", "US:en", "US"),
]


class GoogleNewsCrawler(BaseCrawler):
    """Google News RSS 로 삼성/갤럭시 종합뉴스를 다국어 수집."""

    MIN_DELAY = 0.5
    MAX_DELAY = 1.2
    MAX_POSTS = 200

    def __init__(self, product_code: Optional[str] = None, job_id: Optional[int] = None):
        super().__init__("googlenews", product_code=product_code, job_id=job_id)

    async def crawl(self) -> List[RawVOC]:
        seen: set[str] = set()
        out: List[RawVOC] = []
        async with self._make_httpx_client() as client:
            for q, hl, gl, ceid, cc in _QUERIES:
                url = _GN.format(q=urllib.parse.quote(q), hl=hl, gl=gl, ceid=ceid)
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    items = self._parse(resp.text, cc)
                except (httpx.HTTPError, ET.ParseError) as e:
                    logger.warning("GN '%s' 실패: %r", q, e)
                    await self._random_delay()
                    continue
                for v in items:
                    if v.external_id in seen:
                        continue
                    if not _GALAXY.search(v.content or ""):
                        continue
                    seen.add(v.external_id)
                    out.append(v)
                await self._random_delay()
        logger.info("종합뉴스 수집 완료 — 쿼리 %d · 고유 %d건", len(_QUERIES), len(out))
        return out[: self.MAX_POSTS]

    def _parse(self, xml_text: str, country: str) -> List[RawVOC]:
        root = ET.fromstring(xml_text)
        res: List[RawVOC] = []
        for item in root.findall(".//item"):
            raw_title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not raw_title or not link:
                continue
            title = _TITLE_SUFFIX.sub("", raw_title).strip() or raw_title
            src_el = item.find("source")
            publisher = (src_el.text.strip() if src_el is not None and src_el.text else None)
            guid = (item.findtext("guid") or link).strip()
            published_at = self._parse_rss_date(item.findtext("pubDate") or "")
            res.append(RawVOC(
                external_id=hashlib.md5(f"gnews#{guid}".encode()).hexdigest()[:16],
                content=title,
                source_url=link,
                author_name=publisher,
                published_at=published_at,
                country_code=country,
                meta={"source": "google_news_rss", "publisher": publisher},
            ))
        return res

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
