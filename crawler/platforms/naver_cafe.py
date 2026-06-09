"""
Naver Cafe 크롤러 — Playwright 기반
삼성 공식 네이버 카페에서 Galaxy 관련 게시물 수집
카페 URL: https://cafe.naver.com/samsungmobile
"""
import hashlib
import os
import re
import sys
from datetime import datetime, timezone, timedelta

# 한국 사이트 — KST 표시 시각을 UTC 로 변환 저장
KST = timezone(timedelta(hours=9))
from typing import List
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

NAVER_CAFE_URL = "https://cafe.naver.com"
CAFE_ID = "samsungmobile"

# 크롤링 대상 게시판 ID (네이버 카페는 게시판별 menuId 사용)
# 실제 menuId는 카페 구조에 따라 다름 — 공통 검색으로 대체
CAFE_SEARCH_URL = (
    "{base}/ArticleSearchList.nhn?"
    "search.clubid=28543326"   # 삼성모바일 공식 카페 ID
    "&search.searchBy=0"
    "&search.query={keyword}"
    "&search.page={page}"
)

GALAXY_KEYWORDS = [
    "갤럭시 S25", "갤럭시 폴드", "갤럭시 플립",
    "Galaxy S25", "Galaxy Fold", "Galaxy Flip",
    "버즈3", "워치8", "갤럭시링",
]


# @lat: NaverCafeCrawler — [[crawler#Platform Strategy]] 참조.
class NaverCafeCrawler(BaseCrawler):
    MIN_DELAY = 2.0
    MAX_DELAY = 5.0

    def __init__(self, platform_code: str = "naver_cafe", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)
        self.naver_id = os.getenv("NAVER_ID", "")
        self.naver_pw = os.getenv("NAVER_PASSWORD", "")

    async def crawl(self) -> List[RawVOC]:
        if not self.naver_id or not self.naver_pw:
            logger.warning("NAVER_ID / NAVER_PASSWORD 미설정. Naver Cafe 크롤링은 공개 게시물만 시도합니다.")

        from playwright.async_api import async_playwright

        raw_vocs: List[RawVOC] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent=self._random_ua(),
                viewport={"width": 1280, "height": 900},
            )

            # 로그인 시도 (선택)
            if self.naver_id and self.naver_pw:
                await self._login_naver(context)

            for keyword in GALAXY_KEYWORDS[:5]:  # 속도 제한을 위해 5개만
                try:
                    posts = await self._search_cafe(context, keyword)
                    raw_vocs.extend(posts)
                    logger.info(f"  Naver Cafe [{keyword}]: {len(posts)}건")
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  Naver Cafe [{keyword}] 실패: {e}")

            await browser.close()

        # 중복 제거
        seen = set()
        unique = [v for v in raw_vocs if not (v.external_id in seen or seen.add(v.external_id))]
        logger.info(f"Naver Cafe 수집 완료: {len(unique)}건")
        return unique

    async def _login_naver(self, context) -> bool:
        """네이버 로그인"""
        page = await context.new_page()
        try:
            await page.goto("https://nid.naver.com/nidlogin.login", timeout=20000)
            await page.wait_for_timeout(1500)

            await page.fill("#id", self.naver_id)
            await page.fill("#pw", self.naver_pw)
            await page.click(".btn_login")
            await page.wait_for_timeout(3000)

            is_logged = "naver.com" in page.url and "login" not in page.url
            logger.info(f"Naver 로그인 {'성공' if is_logged else '실패'}")
            return is_logged
        except Exception as e:
            logger.warning(f"Naver 로그인 오류: {e}")
            return False
        finally:
            await page.close()

    async def _search_cafe(self, context, keyword: str) -> List[RawVOC]:
        """카페 검색으로 게시물 수집"""
        page = await context.new_page()
        results = []
        try:
            # 네이버 카페 검색 URL (iframe 구조)
            search_url = (
                f"{NAVER_CAFE_URL}/f-search?query={keyword.replace(' ', '+')}"
                f"&searchBy=0&includeAll=&exclude=&include=&exact=&page=1"
                f"&cafeId={CAFE_ID}"
            )
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)

            # 네이버 카페는 iframe 내에 콘텐츠가 있음
            frame = page.frame("cafe_main") or page.main_frame

            article_els = await frame.query_selector_all(".article-item") \
                or await page.query_selector_all(".article-item")

            for el in article_els[:20]:
                try:
                    title_el = await el.query_selector(".article-item__title")
                    title = (await title_el.inner_text()).strip() if title_el else ""
                    if not title:
                        continue

                    link_el = await el.query_selector("a")
                    href = await link_el.get_attribute("href") if link_el else ""
                    post_url = href if href.startswith("http") else f"{NAVER_CAFE_URL}{href}"

                    date_el = await el.query_selector(".article-item__date")
                    date_text = (await date_el.inner_text()).strip() if date_el else ""
                    published_at = self._parse_naver_date(date_text)

                    author_el = await el.query_selector(".article-item__writer")
                    author = (await author_el.inner_text()).strip() if author_el else "익명"

                    uid = hashlib.md5(post_url.encode()).hexdigest()[:16]

                    results.append(RawVOC(
                        external_id=uid,
                        content=title,
                        source_url=post_url,
                        author_name=author,
                        published_at=published_at,
                        country_code="KR",
                    ))
                except Exception as e:
                    logger.debug(f"Naver Cafe 게시물 파싱 실패: {e}")
        except Exception as e:
            logger.warning(f"Naver Cafe 검색 실패 ({keyword}): {e}")
        finally:
            await page.close()
        return results

    def _parse_naver_date(self, text: str):
        """'2026.05.15.' 또는 '05.15.' 파싱"""
        try:
            text = text.strip().rstrip(".")
            if re.match(r"\d{4}\.\d{2}\.\d{2}", text):
                return datetime.strptime(text[:10], "%Y.%m.%d").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
            elif re.match(r"\d{2}\.\d{2}", text):
                now = datetime.now(KST)
                return datetime.strptime(text[:5], "%m.%d").replace(
                    year=now.year, tzinfo=KST
                ).astimezone(timezone.utc)
        except Exception:
            pass
        return None
