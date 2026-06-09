"""
GSMArena 크롤러 — httpx + BeautifulSoup
gsmarena.com 의 디바이스별 user-opinion(리뷰) 페이지에서 영문 VOC 수집.

URL 패턴
  - 디바이스 상세:  https://www.gsmarena.com/<slug>-<id>.php
  - 리뷰 페이지:    https://www.gsmarena.com/<slug>-reviews-<id>.php
                    페이지 N:  <slug>-reviews-<id>p<N>.php
"""
import hashlib
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://www.gsmarena.com"

# product_code → "<slug>-<id>"  (URL fragment, 모두 -reviews-<id> 페이지로 매핑됨)
# 라이브 검증 결과 (2026-05):
#   * samsung_galaxy_s25-13322 는 실제로 S25 Ultra 페이지로 매핑됨 → S25는 13610
#   * S25 Ultra는 13322
GSMARENA_DEVICES: dict = {
    "GS25":  "samsung_galaxy_s25-13610",
    "GS25U": "samsung_galaxy_s25_ultra-13322",
    "GS24U": "samsung_galaxy_s24_ultra-12771",
    "GZF7":  "samsung_galaxy_z_fold7-13826",
    "GZFL7": "samsung_galaxy_z_flip7-13712",
    "AP16P": "apple_iphone_16_pro-13315",
    "PX9":   "google_pixel_9-13219",
}

# 검색 fallback 시 product_code → 검색 키워드
_SEARCH_QUERY = {
    "GS25":  "samsung galaxy s25",
    "GS25U": "samsung galaxy s25 ultra",
    "GS24U": "samsung galaxy s24 ultra",
    "GZF7":  "samsung galaxy z fold7",
    "GZFL7": "samsung galaxy z flip7",
    "AP16P": "iphone 16 pro",
    "PX9":   "google pixel 9",
}

# 검색 fallback 시 결과 후보를 거를 정규식 (가장 일반적인 모델을 우선 선택하도록 anchor)
_SEARCH_PREFER = {
    "GS25":  re.compile(r"Galaxy S25$", re.I),                # plain S25
    "GS25U": re.compile(r"Galaxy S25 Ultra", re.I),
    "GS24U": re.compile(r"Galaxy S24 Ultra", re.I),
    "GZF7":  re.compile(r"Galaxy Z Fold ?7", re.I),
    "GZFL7": re.compile(r"Galaxy Z Flip ?7", re.I),
    "AP16P": re.compile(r"iPhone 16 Pro$", re.I),             # plain Pro (not Max)
    "PX9":   re.compile(r"Pixel 9$", re.I),                   # plain Pixel 9
}


class GSMArenaCrawler(BaseCrawler):
    MIN_DELAY = 2.0
    MAX_DELAY = 4.0

    MAX_DEVICES = 6
    MAX_REVIEWS_PER_DEVICE = 40
    REVIEWS_PER_PAGE = 20  # GSMArena 고정

    def __init__(self, platform_code: str = "gsmarena", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        raw_vocs: List[RawVOC] = []

        # MAX_DEVICES 개만 처리
        targets = list(GSMARENA_DEVICES.items())[: self.MAX_DEVICES]

        async with self._make_httpx_client() as client:
            for product_code, slug_id in targets:
                try:
                    resolved = await self._resolve_device_url(client, product_code, slug_id)
                    if not resolved:
                        logger.warning(f"  GSMArena {product_code}: 디바이스 URL 해결 실패")
                        continue
                    slug_id_used, reviews_url = resolved
                    logger.info(
                        f"  GSMArena {product_code}: {slug_id_used} 리뷰 수집 시작"
                    )

                    device_vocs = await self._fetch_device_reviews(
                        client, product_code, reviews_url
                    )
                    logger.info(
                        f"  GSMArena {product_code}: {len(device_vocs)}건 수집"
                    )
                    raw_vocs.extend(device_vocs)
                except Exception as e:
                    logger.warning(f"  GSMArena {product_code} 실패: {e}")

        logger.info(f"GSMArena 수집 완료: {len(raw_vocs)}건")
        return raw_vocs

    async def _resolve_device_url(
        self, client: httpx.AsyncClient, product_code: str, slug_id: str
    ) -> Optional[Tuple[str, str]]:
        """디바이스 reviews URL을 반환. 하드코딩된 slug_id로 먼저 시도,
        실패하거나 잘못된 디바이스로 리다이렉트 되면 검색 fallback.
        반환: (slug_id, reviews_url) 또는 None
        """
        # 1) 하드코딩된 slug_id의 디바이스 페이지가 살아있고 제목이 맞는지 검증
        device_url = f"{BASE_URL}/{slug_id}.php"
        try:
            r = await client.get(device_url)
            if r.status_code == 200 and self._title_matches(r.text, product_code):
                return slug_id, f"{BASE_URL}/{self._slug_only(slug_id)}-reviews-{self._id_only(slug_id)}.php"
        except Exception as e:
            logger.debug(f"GSMArena {product_code} 직접 URL 실패: {e}")

        # 2) 검색 fallback
        await self._random_delay()
        q = _SEARCH_QUERY.get(product_code, product_code)
        search_url = f"{BASE_URL}/results.php3?sQuickSearch=yes&sName={q.replace(' ', '+')}"
        try:
            r = await client.get(search_url)
            soup = BeautifulSoup(r.text, "html.parser")
            prefer = _SEARCH_PREFER.get(product_code)
            chosen: Optional[Tuple[str, str]] = None  # (name, href)
            first: Optional[Tuple[str, str]] = None
            for a in soup.select("div.makers ul li a"):
                href = a.get("href", "")
                name = a.get_text(strip=True)
                if not re.search(r"-\d+\.php$", href):
                    continue
                if first is None:
                    first = (name, href)
                if prefer and prefer.search(name):
                    chosen = (name, href)
                    break
            picked = chosen or first
            if not picked:
                return None
            name, href = picked
            # href 형태: samsung_galaxy_s25-13610.php
            m = re.match(r"(.+)-(\d+)\.php$", href)
            if not m:
                return None
            new_slug_id = f"{m.group(1)}-{m.group(2)}"
            logger.info(
                f"  GSMArena {product_code}: 검색 fallback → {name} ({new_slug_id})"
            )
            return (
                new_slug_id,
                f"{BASE_URL}/{m.group(1)}-reviews-{m.group(2)}.php",
            )
        except Exception as e:
            logger.warning(f"GSMArena {product_code} 검색 fallback 실패: {e}")
            return None

    @staticmethod
    def _slug_only(slug_id: str) -> str:
        return slug_id.rsplit("-", 1)[0]

    @staticmethod
    def _id_only(slug_id: str) -> str:
        return slug_id.rsplit("-", 1)[1]

    def _title_matches(self, html: str, product_code: str) -> bool:
        """페이지 <title>이 product_code 가 의도한 모델과 일치하는지 휴리스틱 검증."""
        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.string or "") if soup.title else ""
        prefer = _SEARCH_PREFER.get(product_code)
        if prefer and prefer.search(title):
            return True
        # prefer 정규식이 없으면 검색 쿼리 키워드 모두 포함되는지로 대신 검증
        q = _SEARCH_QUERY.get(product_code, "")
        return all(tok.lower() in title.lower() for tok in q.split())

    async def _fetch_device_reviews(
        self, client: httpx.AsyncClient, product_code: str, reviews_url: str
    ) -> List[RawVOC]:
        """디바이스 reviews 페이지(여러 페이지)에서 리뷰 추출."""
        results: List[RawVOC] = []
        pages_needed = (
            self.MAX_REVIEWS_PER_DEVICE + self.REVIEWS_PER_PAGE - 1
        ) // self.REVIEWS_PER_PAGE

        for page in range(1, pages_needed + 1):
            if page == 1:
                page_url = reviews_url
            else:
                # <slug>-reviews-<id>p<N>.php
                m = re.match(r"(.+)-reviews-(\d+)\.php$", reviews_url)
                if not m:
                    break
                page_url = f"{m.group(1)}-reviews-{m.group(2)}p{page}.php"

            await self._random_delay()
            try:
                r = await client.get(page_url)
                if r.status_code != 200:
                    logger.debug(f"GSMArena {product_code} p{page} status={r.status_code}")
                    break
                page_vocs = self._parse_reviews_page(
                    html=r.text, page_url=page_url, product_code=product_code
                )
                if not page_vocs:
                    break
                results.extend(page_vocs)
                if len(results) >= self.MAX_REVIEWS_PER_DEVICE:
                    break
            except Exception as e:
                logger.warning(f"  GSMArena {product_code} p{page} 실패: {e}")
                break

        return results[: self.MAX_REVIEWS_PER_DEVICE]

    def _parse_reviews_page(
        self, html: str, page_url: str, product_code: str
    ) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        out: List[RawVOC] = []

        for idx, block in enumerate(soup.select("div.user-thread")):
            try:
                # 안정적인 review ID — div#NNN 또는 a href="postopinion.php3?...idOpinion=NNN"
                rev_id = block.get("id") or ""
                if not rev_id:
                    a = block.select_one("a[href*='idOpinion=']")
                    if a:
                        m = re.search(r"idOpinion=(\d+)", a.get("href", ""))
                        if m:
                            rev_id = m.group(1)

                # 본문: p.uopin 에서 인용 블록(span.uinreply, span.uinreply-msg, a.uinreply) 제거
                p = block.select_one("p.uopin")
                if not p:
                    continue
                p_copy = BeautifulSoup(str(p), "html.parser")
                for q in p_copy.select(
                    "span.uinreply, span.uinreply-msg, a.uinreply"
                ):
                    q.decompose()
                content = p_copy.get_text(" ", strip=True)
                content = re.sub(r"\s+", " ", content).strip()
                if not content or len(content) < 5:
                    continue

                # 작성자
                nm_el = block.select_one("li.uname2")
                author = nm_el.get_text(strip=True) if nm_el else "anonymous"
                if not author:
                    author = "anonymous"

                # 날짜
                tm_el = block.select_one("li.upost time")
                date_text = tm_el.get_text(strip=True) if tm_el else ""
                published_at = self._parse_gsmarena_date(date_text)

                # 좋아요 (data-votes 속성)
                votes_el = block.select_one("ul.votes")
                try:
                    likes = int(votes_el.get("data-votes") or 0) if votes_el else 0
                except (TypeError, ValueError):
                    likes = 0

                # external_id 생성: rev_id 가 있으면 사용, 없으면 hash fallback
                if rev_id:
                    external_id = f"gsma_{rev_id}"
                    src_url = f"{page_url}#{rev_id}"
                else:
                    external_id = hashlib.md5(
                        f"{page_url}#{idx}_{content[:50]}".encode()
                    ).hexdigest()[:16]
                    src_url = page_url

                out.append(RawVOC(
                    external_id=external_id,
                    content=content,
                    source_url=src_url,
                    author_name=author,
                    published_at=published_at,
                    likes_count=likes,
                    country_code="US",
                    meta={"product_code": product_code},
                ))
            except Exception as e:
                logger.debug(f"GSMArena 리뷰 블록 파싱 실패: {e}")

        return out

    @staticmethod
    def _parse_gsmarena_date(text: str) -> Optional[datetime]:
        """GSMArena 날짜 포맷:
          - "01 May 2026"
          - "5 hours ago" / "2 days ago" / "30 minutes ago"
          - "yesterday"
        애매하면 None.
        """
        text = (text or "").strip()
        if not text:
            return None

        now = datetime.now(timezone.utc)

        try:
            # "01 May 2026"
            return datetime.strptime(text, "%d %b %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            pass

        low = text.lower()
        if low == "yesterday":
            return (now - timedelta(days=1)).replace(microsecond=0)

        m = re.match(r"(\d+)\s*(minute|min|hour|hr|day|week|month|year)s?\s*ago", low)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            if unit in ("minute", "min"):
                return now - timedelta(minutes=n)
            if unit in ("hour", "hr"):
                return now - timedelta(hours=n)
            if unit == "day":
                return now - timedelta(days=n)
            if unit == "week":
                return now - timedelta(weeks=n)
            if unit == "month":
                return now - timedelta(days=30 * n)
            if unit == "year":
                return now - timedelta(days=365 * n)
        return None
