# Wayback(archive.org) 뉴스 옛기사 backfill — 아카이브된 RSS 스냅샷을 연도별로 파싱
"""
Wayback News 크롤러 — archive.org 아카이브 RSS 스냅샷으로 옛 뉴스 소급.

배경
====
The Verge·Engadget·PhoneArena 등은 단일 RSS(최신만)이고 WP REST 도 막혀 옛 기사를
못 긁는다. archive.org 는 이들 RSS 피드를 오래 전부터 스냅샷해 왔다. 연도별로
아카이브된 RSS 를 가져와 파싱하면 그 시점의 기사들을 원 게시일 그대로 얻는다.

동작
====
1. CDX API 로 사이트 RSS URL 의 연도별 스냅샷 timestamp 수집(월 단위 collapse).
2. 각 스냅샷을 web/{ts}id_/{rss} 로 원본 그대로 fetch → Atom/RSS 파싱.
3. 삼성/갤럭시 필터 → RawVOC(published_at = RSS pubDate = 원 게시일).
- archive.org 예의: 요청 간 1.5s. 느려도 됨(천천히 채움). CDX 503 은 graceful skip.

env: WAYBACK_YEAR (필수, backfill 스크립트가 주입). 플랫폼 코드: waybacknews.
"""
import asyncio
import hashlib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional, Tuple
import logging

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC  # noqa: E402

logger = logging.getLogger(__name__)

CDX = "http://web.archive.org/cdx/search/cdx"
WB = "http://web.archive.org/web"
_GALAXY = re.compile(r"samsung|galaxy|z\s?fold|z\s?flip", re.IGNORECASE)

# RSS-only + WP REST 막힌 매체 (publisher, RSS URL). archive.org 에 스냅샷 존재 확인된 것 위주.
_SITES = [
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("Engadget", "https://www.engadget.com/rss.xml"),
    ("PhoneArena", "https://www.phonearena.com/feed"),
    ("Android Police", "https://www.androidpolice.com/feed/"),
    ("Tom's Guide", "https://www.tomsguide.com/feeds/all"),
]


class WaybackNewsCrawler(BaseCrawler):
    """archive.org 아카이브 RSS 스냅샷으로 옛 뉴스를 연도별 소급."""

    MIN_DELAY = 0.0
    MAX_DELAY = 0.0
    POLITE = 1.5          # archive.org 요청 간 최소 간격(초)
    SNAPSHOTS_PER_YEAR = 12   # 월 단위 collapse → 연 최대 12 스냅샷

    def __init__(self, product_code: Optional[str] = None, job_id: Optional[int] = None):
        super().__init__("waybacknews", product_code=product_code, job_id=job_id)
        self.year = int(os.getenv("WAYBACK_YEAR") or "0")

    async def crawl(self) -> List[RawVOC]:
        if not self.year:
            logger.warning("WAYBACK_YEAR 미지정 — skip")
            return []
        seen: set = set()
        out: List[RawVOC] = []
        async with self._make_httpx_client() as client:
            for pub, rss in _SITES:
                snaps = await self._cdx(client, rss)
                logger.info("Wayback %s %d: 스냅샷 %d개", pub, self.year, len(snaps))
                for ts in snaps:
                    out += await self._fetch_snapshot(client, ts, rss, pub, seen)
                    await asyncio.sleep(self.POLITE)
        logger.info("Wayback뉴스 %d 수집 완료 — %d건", self.year, len(out))
        return out

    async def _cdx(self, client, rss_url) -> List[str]:
        """연도 스냅샷 timestamp 목록 (월 단위 collapse)."""
        params = {
            "url": rss_url, "output": "json",
            "from": f"{self.year}0101", "to": f"{self.year}1231",
            "filter": "statuscode:200",
            "collapse": "timestamp:6",   # YYYYMM → 월당 1개
            "limit": str(self.SNAPSHOTS_PER_YEAR * 3),
            "fl": "timestamp",
        }
        for attempt in range(3):
            try:
                r = await client.get(CDX, params=params, timeout=60.0)
                if r.status_code != 200:
                    await asyncio.sleep(2.0 * (attempt + 1)); continue
                rows = r.json()
                return [row[0] for row in rows[1:]][: self.SNAPSHOTS_PER_YEAR]
            except (httpx.HTTPError, ValueError):
                await asyncio.sleep(2.0 * (attempt + 1))
        return []

    async def _fetch_snapshot(self, client, ts, rss_url, pub, seen) -> List[RawVOC]:
        url = f"{WB}/{ts}id_/{rss_url}"
        try:
            r = await client.get(url, timeout=45.0)
            if r.status_code != 200 or len(r.text) < 100:
                return []
            root = ET.fromstring(r.text)
        except (httpx.HTTPError, ET.ParseError):
            return []
        ns = {"a": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//a:entry", ns) or root.findall(".//item")
        res: List[RawVOC] = []
        for it in items:
            title = (it.findtext("a:title", default="", namespaces=ns)
                     or it.findtext("title") or "").strip()
            if not title or not _GALAXY.search(title):
                continue
            link = (self._atom_link(it, ns) or it.findtext("link") or "").strip()
            if not link:
                continue
            uid = hashlib.md5(f"wbnews#{link}".encode()).hexdigest()[:16]
            if uid in seen:
                continue
            seen.add(uid)
            pub_raw = (it.findtext("a:published", default="", namespaces=ns)
                       or it.findtext("a:updated", default="", namespaces=ns)
                       or it.findtext("pubDate") or "")
            res.append(RawVOC(
                external_id=uid, content=title, source_url=link,
                author_name=pub, published_at=self._parse_date(pub_raw),
                meta={"source": "wayback_rss", "publisher": pub, "snapshot": ts},
            ))
        return res

    def _atom_link(self, it, ns) -> str:
        el = it.find("a:link", ns)
        return el.get("href") if el is not None else ""

    def _parse_date(self, text: str) -> Optional[datetime]:
        if not text:
            return None
        try:  # ISO(Atom)
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
        try:  # RFC822(RSS)
            dt = parsedate_to_datetime(text)
            return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(timezone.utc)
        except Exception:
            return None
