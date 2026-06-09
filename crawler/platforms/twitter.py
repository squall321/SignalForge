"""
Twitter/X 크롤러 — Playwright 기반
트위터에서 Samsung Galaxy 관련 트윗 수집 (로그인 세션 사용)
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import List, Optional
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

TWITTER_URL = "https://x.com"
SESSION_FILE = "/tmp/signalforge_twitter_session.json"

# 검색 쿼리 — 최근 24h, 영어 + 한국어
SEARCH_QUERIES = [
    "Samsung Galaxy S25 -is:retweet lang:en",
    "Galaxy Z Fold7 OR Galaxy Z Flip7 -is:retweet lang:en",
    "Samsung Galaxy review -is:retweet lang:en",
    "갤럭시 S25 -is:retweet lang:ko",
    "갤럭시 Z폴드 OR 갤럭시 Z플립 -is:retweet lang:ko",
    "Samsung Galaxy -is:retweet until:now since:24h",
]


# @lat: TwitterCrawler — [[crawler#Platform Strategy]] 참조.
class TwitterCrawler(BaseCrawler):
    MIN_DELAY = 3.0
    MAX_DELAY = 6.0

    def __init__(self, platform_code: str = "twitter", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)
        self.username = os.getenv("TWITTER_USERNAME", "")
        self.password = os.getenv("TWITTER_PASSWORD", "")

    async def crawl(self) -> List[RawVOC]:
        if not self.username or not self.password:
            logger.warning("TWITTER_USERNAME / TWITTER_PASSWORD 미설정. Twitter 크롤링 건너뜀.")
            return []

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

            # 저장된 세션 복원 시도
            logged_in = await self._restore_session(context)
            if not logged_in:
                logged_in = await self._login(context)

            if not logged_in:
                logger.error("Twitter 로그인 실패. 크롤링 중단.")
                await browser.close()
                return []

            # 세션 저장
            await self._save_session(context)

            for query in SEARCH_QUERIES:
                try:
                    tweets = await self._search_tweets(context, query)
                    raw_vocs.extend(tweets)
                    logger.info(f"  Twitter [{query[:40]}...]: {len(tweets)}건")
                    await self._random_delay()
                except Exception as e:
                    logger.warning(f"  Twitter 검색 실패: {e}")

            await browser.close()

        # 중복 제거
        seen = set()
        unique = [v for v in raw_vocs if not (v.external_id in seen or seen.add(v.external_id))]
        logger.info(f"Twitter 수집 완료: {len(unique)}건")
        return unique

    async def _restore_session(self, context) -> bool:
        """저장된 쿠키로 세션 복원"""
        if not os.path.exists(SESSION_FILE):
            return False
        try:
            with open(SESSION_FILE) as f:
                cookies = json.load(f)
            await context.add_cookies(cookies)
            # 세션 유효성 확인
            page = await context.new_page()
            await page.goto(f"{TWITTER_URL}/home", timeout=20000)
            await page.wait_for_timeout(2000)
            is_logged = await page.query_selector("[data-testid='primaryColumn']") is not None
            await page.close()
            return is_logged
        except Exception:
            return False

    async def _login(self, context) -> bool:
        """Twitter 로그인"""
        page = await context.new_page()
        try:
            await page.goto(f"{TWITTER_URL}/i/flow/login", timeout=30000)
            await page.wait_for_timeout(2000)

            # 사용자 이름 입력
            username_input = await page.wait_for_selector("input[autocomplete='username']", timeout=10000)
            await username_input.fill(self.username)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2000)

            # 비밀번호 입력 (중간에 전화번호 인증 요청 있을 수 있음)
            # 전화번호 입력 필드 체크
            phone_input = await page.query_selector("input[data-testid='ocfEnterTextTextInput']")
            if phone_input:
                phone = os.getenv("TWITTER_PHONE", "")
                if phone:
                    await phone_input.fill(phone)
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(2000)

            password_input = await page.wait_for_selector(
                "input[name='password']", timeout=10000
            )
            await password_input.fill(self.password)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(4000)

            is_logged = await page.query_selector("[data-testid='primaryColumn']") is not None
            logger.info(f"Twitter 로그인 {'성공' if is_logged else '실패'}")
            return is_logged
        except Exception as e:
            logger.error(f"Twitter 로그인 오류: {e}")
            return False
        finally:
            await page.close()

    async def _save_session(self, context):
        """쿠키 저장"""
        try:
            cookies = await context.cookies()
            with open(SESSION_FILE, "w") as f:
                json.dump(cookies, f)
        except Exception as e:
            logger.debug(f"세션 저장 실패: {e}")

    async def _search_tweets(self, context, query: str) -> List[RawVOC]:
        page = await context.new_page()
        results = []
        try:
            encoded = query.replace(" ", "%20").replace(":", "%3A")
            url = f"{TWITTER_URL}/search?q={encoded}&src=typed_query&f=live"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            # 스크롤해서 더 많은 트윗 로드
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                await page.wait_for_timeout(1500)

            tweet_els = await page.query_selector_all("article[data-testid='tweet']")
            for el in tweet_els[:20]:
                try:
                    voc = await self._parse_tweet(el, page)
                    if voc:
                        results.append(voc)
                except Exception as e:
                    logger.debug(f"트윗 파싱 실패: {e}")
        except Exception as e:
            logger.warning(f"트윗 검색 페이지 오류: {e}")
        finally:
            await page.close()
        return results

    async def _parse_tweet(self, el, page) -> Optional[RawVOC]:
        # 트윗 본문
        text_el = await el.query_selector("[data-testid='tweetText']")
        text = (await text_el.inner_text()).strip() if text_el else ""
        if len(text) < 10:
            return None

        # 링크 (tweet URL)
        link_el = await el.query_selector("a[href*='/status/']")
        href = await link_el.get_attribute("href") if link_el else ""
        tweet_url = f"{TWITTER_URL}{href}" if href.startswith("/") else href
        tweet_id_match = re.search(r"/status/(\d+)", href)
        tweet_id = tweet_id_match.group(1) if tweet_id_match else \
            hashlib.md5(text[:50].encode()).hexdigest()[:16]

        # 작성자
        author_el = await el.query_selector("[data-testid='User-Name'] span")
        author = (await author_el.inner_text()).strip() if author_el else "Unknown"

        # 날짜
        time_el = await el.query_selector("time")
        published_at = None
        if time_el:
            dt_str = await time_el.get_attribute("datetime")
            try:
                published_at = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except Exception:
                pass

        # 좋아요 / 답글 수
        like_el = await el.query_selector("[data-testid='like'] span")
        like_text = (await like_el.inner_text()).strip() if like_el else "0"
        likes = self._parse_count(like_text)

        reply_el = await el.query_selector("[data-testid='reply'] span")
        reply_text = (await reply_el.inner_text()).strip() if reply_el else "0"
        replies = self._parse_count(reply_text)

        retweet_el = await el.query_selector("[data-testid='retweet'] span")
        rt_text = (await retweet_el.inner_text()).strip() if retweet_el else "0"
        retweets = self._parse_count(rt_text)

        # 언어 기반 국가 코드 추정 (한국어 → KR, 나머지 → US)
        country = "KR" if re.search(r"[가-힣]", text) else "US"

        return RawVOC(
            external_id=tweet_id,
            content=text,
            source_url=tweet_url,
            author_name=author,
            published_at=published_at,
            likes_count=likes,
            comments_count=replies,
            shares_count=retweets,
            country_code=country,
        )

    @staticmethod
    def _parse_count(text: str) -> int:
        """'1.2K' → 1200, '1M' → 1000000"""
        try:
            text = text.replace(",", "").strip()
            if not text or text == "0":
                return 0
            if text.endswith("K"):
                return int(float(text[:-1]) * 1000)
            if text.endswith("M"):
                return int(float(text[:-1]) * 1000000)
            return int(text)
        except Exception:
            return 0
