"""
Best Buy 크롤러 — Playwright + 네트워크 인터셉트
검색 페이지에서 제품 정보 수집 + 리뷰 API 응답 인터셉트.
Best Buy 제품 상세 페이지는 직접 접근이 막혀 있으므로,
검색 결과에서 SKU → 제품 상세 URL을 인터셉트하여 리뷰를 수집한다.
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import List, Optional
import logging

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

# 제품 코드 → Best Buy 검색 키워드
PRODUCT_KEYWORDS = {
    "GS26U":  "Samsung Galaxy S26 Ultra",
    "GS26":   "Samsung Galaxy S26",
    "GS25U":  "Samsung Galaxy S25 Ultra",
    "GS25P":  "Samsung Galaxy S25 Plus",
    "GS25":   "Samsung Galaxy S25",
    "GZF7":   "Samsung Galaxy Z Fold 7",
    "GZFL7":  "Samsung Galaxy Z Flip 7",
    "GW8":    "Samsung Galaxy Watch 8",
    "GB3P":   "Samsung Galaxy Buds 3 Pro",
    "GR2":    "Samsung Galaxy Ring",
}

# 제품 코드 → 알려진 SKU (직접 매핑이 있으면 검색 생략)
PRODUCT_SKUS: dict[str, str] = {
    "GS26U": "6669749",   # Galaxy S26 Ultra 512GB
}

BESTBUY_SEARCH_URL = (
    "https://www.bestbuy.com/site/searchpage.jsp"
    "?st={keyword}&intl=nosplash"
)


# @lat: BestBuyCrawler — [[crawler#Platform Strategy]] 참조.
class BestBuyCrawler(BaseCrawler):
    MIN_DELAY = 3.0
    MAX_DELAY = 6.0

    def __init__(self, platform_code: str = "bestbuy", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        from playwright.async_api import async_playwright

        raw_vocs: List[RawVOC] = []
        target = (
            {self.product_code: PRODUCT_KEYWORDS[self.product_code]}
            if self.product_code and self.product_code in PRODUCT_KEYWORDS
            else PRODUCT_KEYWORDS
        )

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-http2"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                viewport={"width": 1280, "height": 800},
            )

            for product_code, keyword in target.items():
                try:
                    sku = PRODUCT_SKUS.get(product_code)
                    if not sku:
                        sku = await self._search_sku(context, keyword)
                    if not sku:
                        logger.warning(f"  BestBuy [{product_code}] SKU 없음")
                        continue

                    reviews = await self._fetch_reviews_via_intercept(
                        context, sku, product_code
                    )
                    raw_vocs.extend(reviews)
                    logger.info(f"  BestBuy [{product_code}]: {len(reviews)}건")
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  BestBuy [{product_code}] 실패: {e}")

            await browser.close()

        logger.info(f"BestBuy 수집 완료: {len(raw_vocs)}건")
        return raw_vocs

    async def _search_sku(self, context, keyword: str) -> Optional[str]:
        """검색 결과에서 첫 번째 유효한 SKU 추출"""
        page = await context.new_page()
        try:
            url = BESTBUY_SEARCH_URL.format(keyword=keyword.replace(" ", "+"))
            await page.goto(url, timeout=30000)
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")

            for item in soup.select(".list-item.wrapper"):
                a = item.select_one("a.image-section")
                if not a:
                    continue
                href = a.get("href", "")
                m = re.search(r"/sku/(\d+)", href)
                if m:
                    return m.group(1)
        except Exception as e:
            logger.debug(f"SKU 검색 실패 ({keyword}): {e}")
        finally:
            await page.close()
        return None

    async def _fetch_reviews_via_intercept(
        self, context, sku: str, product_code: str
    ) -> List[RawVOC]:
        """
        네트워크 인터셉트로 BestBuy 리뷰 API 응답 캡처.
        BestBuy는 페이지 로드 시 /reviews API를 내부적으로 호출함.
        """
        page = await context.new_page()
        captured: list[dict] = []

        async def on_response(response):
            url = response.url
            if "reviews" in url and "bestbuy.com" in url:
                try:
                    body = await response.text()
                    if body.strip().startswith("{") or body.strip().startswith("["):
                        data = json.loads(body)
                        captured.append(data)
                except Exception:
                    pass

        page.on("response", on_response)

        try:
            search_url = (
                f"https://www.bestbuy.com/site/searchpage.jsp"
                f"?st={sku}&intl=nosplash"
            )
            await page.goto(search_url, timeout=30000)
            await page.wait_for_timeout(3000)

            reviews = []
            for data in captured:
                reviews.extend(self._parse_review_api(data, sku, product_code))

            if not reviews:
                html = await page.content()
                reviews = self._parse_html_reviews(html, sku, product_code)

            return reviews
        except Exception as e:
            logger.warning(f"BestBuy 리뷰 인터셉트 실패 (sku={sku}): {e}")
            return []
        finally:
            await page.close()

    def _parse_review_api(
        self, data: dict, sku: str, product_code: str
    ) -> List[RawVOC]:
        """BestBuy 내부 리뷰 API 응답 파싱"""
        reviews = []
        items: list = []
        if isinstance(data, dict):
            items = (
                data.get("reviews", [])
                or data.get("results", [])
                or data.get("Results", [])
            )
        elif isinstance(data, list):
            items = data

        for item in items[:20]:
            try:
                body = (
                    item.get("comment", "")
                    or item.get("reviewText", "")
                    or item.get("ReviewText", "")
                )
                title = item.get("title", "") or item.get("Title", "")
                if len(body) < 20 and len(title) < 10:
                    continue

                raw_rating = (
                    item.get("rating")
                    or item.get("Rating")
                    or item.get("overallRating")
                )
                rating = float(raw_rating) if raw_rating is not None else None

                date_str = (
                    item.get("submissionTime", "")
                    or item.get("SubmissionTime", "")
                    or item.get("reviewSubmittedTime", "")
                )
                published_at = self._parse_iso_date(date_str)

                author = (
                    item.get("authorName", "")
                    or item.get("UserNickname", "")
                    or "Anonymous"
                )
                helpful = int(item.get("helpfulVoteCount", 0) or 0)

                content = f"{title}\n{body}".strip() if title else body
                uid = hashlib.md5(
                    f"bestbuy_{sku}_{content[:50]}".encode()
                ).hexdigest()[:16]

                reviews.append(RawVOC(
                    external_id=uid,
                    content=content,
                    source_url=f"https://www.bestbuy.com/site/{sku}.p",
                    author_name=author,
                    published_at=published_at,
                    likes_count=helpful,
                    country_code="US",
                    meta={"rating": rating, "product_code": product_code},
                ))
            except Exception as e:
                logger.debug(f"BestBuy API 리뷰 파싱 실패: {e}")

        return reviews

    def _parse_html_reviews(
        self, html: str, sku: str, product_code: str
    ) -> List[RawVOC]:
        """HTML에서 직접 리뷰 파싱 (폴백)"""
        soup = BeautifulSoup(html, "html.parser")
        reviews = []

        for el in soup.select(".review-item, .ugc-review"):
            try:
                body_el = el.select_one(".ugc-review-body, .review-body")
                body = body_el.get_text(strip=True) if body_el else ""
                if len(body) < 20:
                    continue

                title_el = el.select_one(".review-title")
                title = title_el.get_text(strip=True) if title_el else ""

                rating_el = el.select_one(".ugc-ratings-score, [class*='rating']")
                rating_text = rating_el.get_text(strip=True) if rating_el else ""
                rating_m = re.search(r"([\d.]+)", rating_text)
                rating = float(rating_m.group(1)) if rating_m else None

                date_el = el.select_one(".review-date, time")
                date_text = (
                    date_el.get("datetime", "") or date_el.get_text(strip=True)
                ) if date_el else ""
                published_at = self._parse_date(date_text)

                content = f"{title}\n{body}".strip() if title else body
                uid = hashlib.md5(
                    f"bestbuy_{sku}_{content[:50]}".encode()
                ).hexdigest()[:16]

                reviews.append(RawVOC(
                    external_id=uid,
                    content=content,
                    source_url=f"https://www.bestbuy.com/site/{sku}.p",
                    published_at=published_at,
                    country_code="US",
                    meta={"rating": rating, "product_code": product_code},
                ))
            except Exception as e:
                logger.debug(f"BestBuy HTML 리뷰 파싱 실패: {e}")

        return reviews

    def _parse_date(self, text: str) -> Optional[datetime]:
        """'January 15, 2026' 파싱"""
        try:
            m = re.search(r"(\w+ \d+, \d{4})", text)
            if m:
                return datetime.strptime(m.group(1), "%B %d, %Y").replace(tzinfo=timezone.utc)
        except Exception:
            pass
        return None

    def _parse_iso_date(self, text: str) -> Optional[datetime]:
        """ISO 8601 파싱"""
        try:
            if text:
                return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            pass
        return None
