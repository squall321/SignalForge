"""
GSMArena Forum 크롤러 (Harvest 4 H4)

기존 ``platforms.gsmarena`` 는 ``GSMARENA_DEVICES`` 하드코딩 6모델 (S25/S25U/S24U/
ZF7/ZFL7/iPhone16P/Pixel9) 만 수집한다. 본 모듈은 ``samsung-phones-9.php`` 목록 페이지
에서 **최신·구형 Samsung 갤럭시 모델을 동적으로 발견**해 각 디바이스의 user opinion
페이지를 추가로 수확한다 (S26/S26 Ultra/Z Trifold/A57/A37/F70e/M17e/A07/F07/A17/
Tab S11/S25 FE 등).

소스 URL 패턴
  - 모델 목록:    https://www.gsmarena.com/samsung-phones-9.php
  - 디바이스 상세: https://www.gsmarena.com/<slug>-<id>.php
  - 리뷰 페이지:   https://www.gsmarena.com/<slug>-reviews-<id>.php
                  (페이지 N:  <slug>-reviews-<id>p<N>.php)

external_id 충돌 방지 — 기존 ``gsma_<id>`` 와 구분되도록 ``gsmaf_<id>`` prefix 사용.
플랫폼 코드는 신규 ``gsmarena_forum`` 으로 분리해 metric 추적과 운영 토글을 독립화.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC  # noqa: E402
from nlp.mx_keywords import is_mx_relevant  # noqa: E402

logger = logging.getLogger(__name__)

BASE_URL = "https://www.gsmarena.com"
SAMSUNG_LIST_URL = f"{BASE_URL}/samsung-phones-9.php"

# 목록에서 추출된 device 슬러그 → 안정 product_code 매핑. base/product_match.py
# 의 코드와 정합. 매핑이 없는 모델은 None 으로 두고 infer_product_code() 가 본문에서
# 추론하도록 위임.
SLUG_TO_PRODUCT_CODE: dict = {
    # S26 라인
    "samsung_galaxy_s26_ultra_5g":      "GS26U",
    "samsung_galaxy_s26_5g":            "GS26",
    "samsung_galaxy_s26_plus_5g":       "GS26P",
    "samsung_galaxy_s26":               "GS26",
    "samsung_galaxy_s26_ultra":         "GS26U",
    # S25 FE (기존 S25 와 다른 SKU 지만 본 collector 는 GS25 로 흡수 — 사이트 차원에서는 노이즈 최소)
    "samsung_galaxy_s25_fe_5g":         "GS25",
    # Z Trifold / Fold / Flip 시리즈 — 신규
    "samsung_galaxy_z_trifold_5g":      "GZTRI",
    "samsung_galaxy_z_flip7_fe_5g":     "GZFL7",
}

# Galaxy slug 캐쳐 — id 4자리 이상 (구형/신형 통합)
_DEVICE_HREF_RE = re.compile(
    r'href="(samsung_galaxy_[a-z0-9_]+)-(\d{4,})\.php"'
)


class GSMArenaForumCrawler(BaseCrawler):
    """삼성 갤럭시 모델 목록 (samsung-phones-9.php) 에서 동적으로 디바이스를 발견하고
    각 디바이스의 user opinion(리뷰) 페이지를 수확한다.
    """

    MIN_DELAY = 2.0
    MAX_DELAY = 4.0

    # 1회 크롤에서 수집할 최대 디바이스/디바이스당 리뷰/페이지당 리뷰 정원
    MAX_DEVICES: int = 8
    MAX_REVIEWS_PER_DEVICE: int = 30
    REVIEWS_PER_PAGE: int = 20  # GSMArena 고정

    # 기존 gsmarena.py 가 이미 커버하는 모델 — 본 collector 에서는 제외
    EXCLUDED_SLUG_IDS: set = {
        "samsung_galaxy_s25-13610",
        "samsung_galaxy_s25_ultra-13322",
        "samsung_galaxy_s24_ultra-12771",
        "samsung_galaxy_z_fold7-13826",
        "samsung_galaxy_z_flip7-13712",
    }

    def __init__(self, platform_code: str = "gsmarena_forum", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        raw_vocs: List[RawVOC] = []
        async with self._make_httpx_client() as client:
            # 1) Samsung 모델 목록 발견
            try:
                devices = await self._discover_devices(client)
            except Exception as e:
                logger.warning(f"GSMArena Forum 모델 목록 발견 실패: {e}")
                return raw_vocs
            logger.info(
                f"GSMArena Forum: {len(devices)} 디바이스 발견 (정원 {self.MAX_DEVICES})"
            )
            targets = devices[: self.MAX_DEVICES]

            # 2) 디바이스별 user opinion 수집
            for slug, dev_id, slug_id in targets:
                try:
                    reviews_url = f"{BASE_URL}/{slug}-reviews-{dev_id}.php"
                    device_vocs = await self._fetch_device_reviews(
                        client=client,
                        slug=slug,
                        dev_id=dev_id,
                        reviews_url=reviews_url,
                    )
                    logger.info(
                        f"  GSMArena Forum {slug_id}: {len(device_vocs)}건 수집"
                    )
                    raw_vocs.extend(device_vocs)
                except Exception as e:
                    logger.warning(f"  GSMArena Forum {slug_id} 실패: {e}")

        before = len(raw_vocs)
        raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
        logger.info(f"GSMArena Forum 수집 완료: {len(raw_vocs)}/{before} (MX 필터)")
        return raw_vocs

    # ────────────────────────────────────────────────────────────────
    # 디바이스 발견
    # ────────────────────────────────────────────────────────────────
    async def _discover_devices(
        self, client: httpx.AsyncClient
    ) -> List[Tuple[str, str, str]]:
        """samsung-phones-9.php 에서 (slug, id, "slug-id") 목록 반환. EXCLUDED 제외."""
        await self._random_delay()
        r = await client.get(SAMSUNG_LIST_URL)
        if r.status_code != 200:
            logger.warning(
                f"GSMArena Forum 목록 status={r.status_code}"
            )
            return []
        return self._parse_device_list(r.text)

    def _parse_device_list(self, html: str) -> List[Tuple[str, str, str]]:
        """HTML 에서 (slug, id, "slug-id") 추출. EXCLUDED_SLUG_IDS 제외, 순서 유지."""
        out: List[Tuple[str, str, str]] = []
        seen: set = set()
        for m in _DEVICE_HREF_RE.finditer(html):
            slug, dev_id = m.group(1), m.group(2)
            slug_id = f"{slug}-{dev_id}"
            if slug_id in seen:
                continue
            seen.add(slug_id)
            if slug_id in self.EXCLUDED_SLUG_IDS:
                continue
            out.append((slug, dev_id, slug_id))
        return out

    # ────────────────────────────────────────────────────────────────
    # 디바이스별 review 수집
    # ────────────────────────────────────────────────────────────────
    async def _fetch_device_reviews(
        self,
        client: httpx.AsyncClient,
        slug: str,
        dev_id: str,
        reviews_url: str,
    ) -> List[RawVOC]:
        results: List[RawVOC] = []
        pages_needed = (
            self.MAX_REVIEWS_PER_DEVICE + self.REVIEWS_PER_PAGE - 1
        ) // self.REVIEWS_PER_PAGE

        for page in range(1, pages_needed + 1):
            if page == 1:
                page_url = reviews_url
            else:
                page_url = f"{BASE_URL}/{slug}-reviews-{dev_id}p{page}.php"

            await self._random_delay()
            try:
                r = await client.get(page_url)
                if r.status_code != 200:
                    logger.debug(
                        f"GSMArena Forum {slug}-{dev_id} p{page} "
                        f"status={r.status_code}"
                    )
                    break
                page_vocs = self._parse_reviews_page(
                    html=r.text, page_url=page_url, slug=slug
                )
                if not page_vocs:
                    break
                results.extend(page_vocs)
                if len(results) >= self.MAX_REVIEWS_PER_DEVICE:
                    break
            except Exception as e:
                logger.warning(
                    f"  GSMArena Forum {slug}-{dev_id} p{page} 실패: {e}"
                )
                break
        return results[: self.MAX_REVIEWS_PER_DEVICE]

    def _parse_reviews_page(
        self, html: str, page_url: str, slug: str
    ) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        out: List[RawVOC] = []
        product_code = SLUG_TO_PRODUCT_CODE.get(slug)  # None 이면 infer 위임

        for idx, block in enumerate(soup.select("div.user-thread")):
            try:
                rev_id = block.get("id") or ""
                if not rev_id:
                    a = block.select_one("a[href*='idOpinion=']")
                    if a:
                        m = re.search(r"idOpinion=(\d+)", a.get("href", ""))
                        if m:
                            rev_id = m.group(1)

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

                nm_el = block.select_one("li.uname2")
                author = nm_el.get_text(strip=True) if nm_el else "anonymous"
                if not author:
                    author = "anonymous"

                tm_el = block.select_one("li.upost time")
                date_text = tm_el.get_text(strip=True) if tm_el else ""
                published_at = self._parse_gsmarena_date(date_text)

                votes_el = block.select_one("ul.votes")
                try:
                    likes = int(votes_el.get("data-votes") or 0) if votes_el else 0
                except (TypeError, ValueError):
                    likes = 0

                # external_id — 기존 gsmarena 와 충돌 방지를 위해 gsmaf_ prefix
                if rev_id:
                    external_id = f"gsmaf_{rev_id}"
                    src_url = f"{page_url}#{rev_id}"
                else:
                    external_id = hashlib.md5(
                        f"{page_url}#{idx}_{content[:50]}".encode()
                    ).hexdigest()[:16]
                    src_url = page_url

                meta = {"slug": slug, "source": "samsung_phones_list"}
                if product_code:
                    meta["product_code"] = product_code

                out.append(RawVOC(
                    external_id=external_id,
                    content=content,
                    source_url=src_url,
                    author_name=author,
                    published_at=published_at,
                    likes_count=likes,
                    country_code="US",
                    meta=meta,
                ))
            except Exception as e:
                logger.debug(f"GSMArena Forum 리뷰 블록 파싱 실패: {e}")
        return out

    @staticmethod
    def _parse_gsmarena_date(text: str) -> Optional[datetime]:
        """GSMArena 날짜 — "01 May 2026" / "5 hours ago" / "yesterday" 지원."""
        text = (text or "").strip()
        if not text:
            return None
        now = datetime.now(timezone.utc)
        try:
            return datetime.strptime(text, "%d %b %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        low = text.lower()
        if low == "yesterday":
            return (now - timedelta(days=1)).replace(microsecond=0)
        m = re.match(
            r"(\d+)\s*(minute|min|hour|hr|day|week|month|year)s?\s*ago", low
        )
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
