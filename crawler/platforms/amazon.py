"""
Amazon 크롤러 — Playwright
국가별 Amazon 도메인에서 제품 리뷰 수집.
/dp/{asin} 제품 페이지 접근 후 리뷰 파싱 (로그인 불필요).
"""
import hashlib
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

# 국가별 Amazon 도메인 + 국가 코드
AMAZON_DOMAINS = {
    "amazon_us": ("www.amazon.com", "US"),
    "amazon_de": ("www.amazon.de", "DE"),
    "amazon_jp": ("www.amazon.co.jp", "JP"),
    "amazon_kr": ("www.amazon.co.kr", "KR"),
    "amazon_uk": ("www.amazon.co.uk", "GB"),
    "amazon_fr": ("www.amazon.fr", "FR"),
    "amazon_ca": ("www.amazon.ca", "CA"),
    "amazon_au": ("www.amazon.com.au", "AU"),
}

# 제품 코드 → Amazon ASIN 매핑 (실제 2026년 현재 ASIN)
# 운영 시 .env 또는 DB에서 관리
PRODUCT_ASINS: dict[str, dict[str, str]] = {
    "GS26U": {
        "amazon_us": "B0GH33YP71",  # Galaxy S26 Ultra
    },
    "GS25U": {
        "amazon_us": "B0DQXB9ZKT",
    },
    "GS25P": {
        "amazon_us": "B0DQXB7X5M",
    },
    "GS25": {
        "amazon_us": "B0DQXB6W4L",
    },
}

# ASIN이 없을 때 검색 키워드
PRODUCT_SEARCH_KEYWORDS = {
    "GS26U":  "Samsung Galaxy S26 Ultra",
    "GS26":   "Samsung Galaxy S26",
    "GS25U":  "Samsung Galaxy S25 Ultra",
    "GS25P":  "Samsung Galaxy S25 Plus",
    "GS25":   "Samsung Galaxy S25",
    "GZF7":   "Samsung Galaxy Z Fold 7",
    "GZFL7":  "Samsung Galaxy Z Flip 7",
    "GA56":   "Samsung Galaxy A56",
    "GW8":    "Samsung Galaxy Watch 8",
    "GWU":    "Samsung Galaxy Watch Ultra",
    "GB3":    "Samsung Galaxy Buds 3",
    "GB3P":   "Samsung Galaxy Buds 3 Pro",
    "GR2":    "Samsung Galaxy Ring",
}


# @lat: AmazonCrawler — [[crawler#Platform Strategy]] 참조.
class AmazonCrawler(BaseCrawler):
    MIN_DELAY = 3.0
    MAX_DELAY = 7.0

    def __init__(self, platform_code: str = "amazon_us", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)
        self.domain, self.country_code = AMAZON_DOMAINS.get(
            platform_code, ("www.amazon.com", "US")
        )

    async def crawl(self) -> List[RawVOC]:
        from playwright.async_api import async_playwright

        raw_vocs: List[RawVOC] = []
        products = self._get_target_products()

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = await ctx.new_page()

            for product_code, _ in products:
                asin = PRODUCT_ASINS.get(product_code, {}).get(self.platform_code)
                if not asin:
                    keyword = PRODUCT_SEARCH_KEYWORDS.get(product_code, product_code)
                    asin = await self._search_asin(page, keyword)
                if not asin:
                    logger.warning(f"ASIN 없음: {product_code}")
                    continue

                reviews = await self._fetch_reviews(page, asin, product_code)
                raw_vocs.extend(reviews)
                logger.info(f"  Amazon({self.domain}) {product_code}: {len(reviews)}건")
                await self._random_delay()

            await browser.close()

        logger.info(f"Amazon({self.domain}) 수집 완료: {len(raw_vocs)}건")
        return raw_vocs

    def _get_target_products(self) -> list[tuple[str, str]]:
        if self.product_code:
            kw = PRODUCT_SEARCH_KEYWORDS.get(self.product_code, self.product_code)
            return [(self.product_code, kw)]
        return list(PRODUCT_SEARCH_KEYWORDS.items())

    async def _is_blocked(self, page) -> bool:
        """Amazon 봇 차단 페이지('Sorry! / Dogs of Amazon') 감지"""
        try:
            title = (await page.title()) or ""
            if "Sorry" in title or "Robot Check" in title:
                self.logger.warning(
                    f"Amazon 봇 차단 감지 (title={title!r}) — "
                    f"헤드리스 차단됨. 프록시/스텔스 세션 필요."
                )
                return True
        except Exception:
            pass
        return False

    async def _search_asin(self, page, keyword: str) -> Optional[str]:
        """검색어로 첫 번째 유효한 ASIN 추출"""
        try:
            url = f"https://{self.domain}/s?k={keyword.replace(' ', '+')}&i=electronics"
            await page.goto(url, timeout=20000)
            if await self._is_blocked(page):
                return None
            els = await page.query_selector_all("[data-asin]")
            for el in els:
                asin = await el.get_attribute("data-asin")
                if asin and len(asin) == 10:  # 유효한 ASIN은 10자
                    return asin
        except Exception as e:
            logger.warning(f"ASIN 검색 실패 ({keyword}): {e}")
        return None

    async def _fetch_reviews(self, page, asin: str, product_code: str) -> List[RawVOC]:
        """제품 페이지에서 리뷰 수집 (로그인 불필요)"""
        try:
            await page.goto(
                f"https://{self.domain}/dp/{asin}",
                timeout=20000,
                wait_until="domcontentloaded",
            )
            if await self._is_blocked(page):
                return []
            html = await page.content()
            return self._parse_reviews(html, product_code, asin)
        except Exception as e:
            logger.warning(f"리뷰 수집 실패 (ASIN={asin}): {e}")
            return []

    def _parse_reviews(self, html: str, product_code: str, asin: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        reviews: List[RawVOC] = []

        for review_el in soup.select("[data-hook='review']"):
            try:
                review_id = review_el.get("id", "")
                if not review_id:
                    review_id = hashlib.md5(
                        review_el.get_text()[:100].encode()
                    ).hexdigest()[:12]

                # 리뷰 본문
                body_el = review_el.select_one("[data-hook='review-body'] span")
                if not body_el:
                    body_el = review_el.select_one("[data-hook='review-body']")
                body = body_el.get_text(strip=True) if body_el else ""
                if len(body) < 20:
                    continue

                # 평점
                rating_el = review_el.select_one("[data-hook='review-star-rating'] span.a-icon-alt")
                if not rating_el:
                    rating_el = review_el.select_one("[data-hook='review-star-rating'] span")
                rating_text = rating_el.get_text(strip=True) if rating_el else ""
                rating_match = re.search(r"([\d.]+)", rating_text)
                rating = float(rating_match.group(1)) if rating_match else None

                # 작성자
                author_el = review_el.select_one(".a-profile-name")
                author = author_el.get_text(strip=True) if author_el else "Anonymous"

                # 날짜
                date_el = review_el.select_one("[data-hook='review-date']")
                published_at = self._parse_date(date_el.get_text(strip=True) if date_el else "")

                # 도움이 됨
                helpful_el = review_el.select_one("[data-hook='helpful-vote-statement']")
                helpful_text = helpful_el.get_text(strip=True) if helpful_el else "0"
                helpful_match = re.search(r"(\d+)", helpful_text)
                helpful = int(helpful_match.group(1)) if helpful_match else 0

                # 제목 + 본문 결합
                title_el = review_el.select_one("[data-hook='review-title'] span:not(.a-letter-space)")
                title = title_el.get_text(strip=True) if title_el else ""
                # 별점 텍스트가 제목 자리에 있을 경우 필터링
                if title and re.match(r"[\d.]+ out of", title):
                    title = ""
                content = f"{title}\n{body}".strip() if title else body

                uid = hashlib.md5(f"{self.platform_code}_{review_id}".encode()).hexdigest()[:16]

                reviews.append(RawVOC(
                    external_id=uid,
                    content=content,
                    source_url=f"https://{self.domain}/gp/customer-reviews/{review_id}",
                    author_name=author,
                    published_at=published_at,
                    likes_count=helpful,
                    country_code=self.country_code,
                    meta={"rating": rating, "product_code": product_code, "asin": asin},
                ))
            except Exception as e:
                logger.debug(f"리뷰 파싱 실패: {e}")

        return reviews

    def _parse_date(self, text: str) -> Optional[datetime]:
        """'Reviewed in the United States on January 15, 2026' 파싱"""
        try:
            # 날짜 부분만 추출
            match = re.search(r"(\w+ \d+, \d{4})", text)
            if match:
                return datetime.strptime(match.group(1), "%B %d, %Y").replace(tzinfo=timezone.utc)
        except Exception:
            pass
        return None
