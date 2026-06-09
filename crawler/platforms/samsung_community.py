"""
Samsung Members (community.samsung.com) 크롤러
Lithium/Khoros 플랫폼. 한국(r1) + 미국(us) 검색 결과에서 갤럭시 관련 스레드 수집.

- 자격증명(SAMSUNG_ID/PASSWORD) 있으면 Playwright로 로그인 시도 → 세션 쿠키로 httpx 크롤
- 실패해도 공개 게시물만 httpx로 폴라이트 크롤 (graceful fallback)
- Search → 스레드 URL → 본문 + 답글 (메시지별 stable uid)
"""
import hashlib
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from typing import List, Optional, Tuple
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC
from nlp.mx_keywords import is_mx_relevant

logger = logging.getLogger(__name__)

# 지역별 호스트 + 국가 코드 + 검색 키워드
REGIONS = [
    {
        "host": "https://r1.community.samsung.com",
        "country": "KR",
        "queries": ["갤럭시 S25", "갤럭시 Z 폴드", "갤럭시 워치", "갤럭시 버즈"],
    },
    {
        "host": "https://us.community.samsung.com",
        "country": "US",
        "queries": ["Galaxy S25", "Galaxy Z Fold", "Galaxy Watch", "Galaxy Buds"],
    },
]

MAX_POSTS_PER_REGION = 20  # 지역당 최대 스레드 상세 수집 수 (폴라이트 딜레이 감안)
SEARCH_PATH = "/t5/forums/searchpage/tab/message?q={q}"


class SamsungCommunityCrawler(BaseCrawler):
    MIN_DELAY = 2.0
    MAX_DELAY = 4.5

    def __init__(self, platform_code: str = "samsung_community", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)
        self.username = os.getenv("SAMSUNG_ID", "")
        self.password = os.getenv("SAMSUNG_PASSWORD", "")
        self.session_cookies: List[dict] = []

    async def crawl(self) -> List[RawVOC]:
        # 자격증명 있으면 로그인 시도 (실패해도 진행)
        if self.username and self.password:
            try:
                ok = await self._try_login()
                logger.info(f"Samsung 로그인 시도: {'성공' if ok else '실패 → 공개 크롤로 진행'}")
            except Exception as e:
                logger.warning(f"Samsung 로그인 예외 ({type(e).__name__}: {e}) → 공개 크롤로 진행")
        else:
            logger.info("Samsung 자격증명 없음 → 공개 크롤")

        raw_vocs: List[RawVOC] = []
        for region in REGIONS:
            host = region["host"]
            country = region["country"]
            queries = region["queries"]
            try:
                thread_urls = await self._collect_thread_urls(host, queries)
                logger.info(f"  Samsung {country}: 스레드 {len(thread_urls)}건 수집 대상")

                for url in thread_urls[:MAX_POSTS_PER_REGION]:
                    await self._random_delay()
                    try:
                        vocs = await self._fetch_thread(url, country)
                        raw_vocs.extend(vocs)
                    except Exception as e:
                        logger.warning(f"  Samsung 스레드 실패 ({url[:80]}): {e}")
            except Exception as e:
                logger.warning(f"  Samsung {country} 지역 실패: {e}")

        before = len(raw_vocs)
        raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
        logger.info(f"Samsung Community 수집 완료: {len(raw_vocs)}/{before} (MX 필터)")
        return raw_vocs

    async def _try_login(self) -> bool:
        """Playwright로 로그인 시도. 성공 시 self.session_cookies 채움. 실패해도 예외 안 던짐.
        Samsung Account SSO 는 복잡하므로 짧은 타임아웃 + 폼 못찾으면 즉시 포기."""
        import asyncio
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("playwright 미설치")
            return False

        try:
            return await asyncio.wait_for(self._login_impl(), timeout=60.0)
        except asyncio.TimeoutError:
            logger.warning("Samsung 로그인 60초 타임아웃")
            return False

    async def _login_impl(self) -> bool:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                context = await browser.new_context(
                    user_agent=self._random_ua(),
                    viewport={"width": 1280, "height": 900},
                )
                page = await context.new_page()
                # 한국 사이트에서 로그인 시도 (메인페이지 → 로그인 링크)
                await page.goto("https://r1.community.samsung.com/t5/user/userloginpage",
                                wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(2500)

                # Samsung Account SSO 페이지로 리다이렉트되거나 직접 폼 표시
                # 이메일 / 비밀번호 input 탐색 (여러 selector 폴백)
                email_input = None
                for sel in ["input[name='username']", "input[type='email']",
                            "input[name='loginId']", "input#iptLgnPlnID"]:
                    email_input = await page.query_selector(sel)
                    if email_input:
                        break

                if not email_input:
                    logger.info("Samsung 로그인 폼 못찾음 (SSO 미노출)")
                    return False

                await email_input.fill(self.username)
                await page.wait_for_timeout(500)

                pw_input = None
                for sel in ["input[name='password']", "input[type='password']",
                            "input#iptLgnPlnPD"]:
                    pw_input = await page.query_selector(sel)
                    if pw_input:
                        break

                if pw_input:
                    await pw_input.fill(self.password)
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(5000)

                # 로그인 후 community 페이지로 돌아왔는지 확인
                final_url = page.url
                cookies = await context.cookies()
                self.session_cookies = cookies
                # 단순 휴리스틱: community 도메인에 있고 로그인 페이지가 아니면 성공으로 간주
                logged = "community.samsung.com" in final_url and "loginpage" not in final_url
                return logged
            finally:
                await browser.close()

    async def _collect_thread_urls(self, host: str, queries: List[str]) -> List[str]:
        """검색 결과 페이지들에서 unique 스레드 URL 수집"""
        seen: set = set()
        thread_urls: List[str] = []

        async with self._make_httpx_client_with_session() as client:
            for q in queries:
                await self._random_delay()
                try:
                    url = host + SEARCH_PATH.format(q=urllib.parse.quote(q))
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        logger.warning(f"  Samsung search {q!r} status={resp.status_code}")
                        continue
                    s = BeautifulSoup(resp.text, "html.parser")

                    # m-p 링크에서 thread base path 추출 (board/title/m-p/<msgid>)
                    for a in s.select("a[href*=m-p]"):
                        href = a.get("href", "")
                        m = re.match(r"(/t5/[^/]+/[^/]+)/m-p/\d+", href)
                        if not m:
                            continue
                        thread_base = m.group(1)
                        full = host + thread_base + "/m-p/" + re.search(r"m-p/(\d+)", href).group(1)
                        if thread_base in seen:
                            continue
                        seen.add(thread_base)
                        thread_urls.append(full)
                    logger.info(f"  Samsung search {q!r}: 누적 {len(thread_urls)}개 스레드")
                except Exception as e:
                    logger.warning(f"  Samsung search {q!r} 실패: {e}")

        return thread_urls

    async def _fetch_thread(self, url: str, country: str) -> List[RawVOC]:
        """스레드 페이지에서 본문 + 답글 추출. 본문 VOC + 답글별 VOC 리스트 반환."""
        async with self._make_httpx_client_with_session() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            final_url = str(resp.url)
            s = BeautifulSoup(resp.text, "html.parser")

        # 제목 (h1)
        title_el = s.select_one("h1.lia-message-subject-banner-topic") or s.select_one("h1")
        title = title_el.get_text(strip=True) if title_el else ""

        # 모든 메시지 panel (data-lia-message-uid + 'lia-panel-message' 클래스 보유)
        panels: List = []
        seen_uids: set = set()
        for panel in s.find_all("div", attrs={"data-lia-message-uid": True}):
            cls = panel.get("class") or []
            if "lia-panel-message" not in cls:
                continue
            uid = panel.get("data-lia-message-uid")
            if uid in seen_uids:
                continue
            seen_uids.add(uid)
            panels.append(panel)

        if not panels:
            logger.debug(f"  Samsung 메시지 패널 없음: {url[:80]}")
            return []

        # 첫 패널 = 스레드 본문, 나머지 = 답글
        body_panel = panels[0]
        reply_panels = panels[1:]

        # 본문
        body_text = self._extract_body(body_panel)
        body_voc = RawVOC(
            external_id=hashlib.md5(final_url.encode()).hexdigest()[:16],
            content=(f"{title}\n{body_text}".strip() if title else body_text),
            source_url=final_url,
            author_name=self._extract_author(body_panel),
            published_at=self._extract_date(body_panel),
            likes_count=self._extract_kudos(body_panel),
            comments_count=len(reply_panels),
            country_code=country,
        )

        out: List[RawVOC] = [body_voc]
        for panel in reply_panels:
            text = self._extract_body(panel)
            if not text or len(text) < 5:
                continue
            uid = panel.get("data-lia-message-uid")
            out.append(RawVOC(
                external_id=hashlib.md5(f"{final_url}#m{uid}".encode()).hexdigest()[:16],
                content=text,
                source_url=final_url,
                author_name=self._extract_author(panel),
                published_at=self._extract_date(panel),
                likes_count=self._extract_kudos(panel),
                country_code=country,
            ))

        logger.info(
            f"  Samsung {country} 스레드 {final_url.split('/')[-1][:20]}: "
            f"본문 {len(body_text)}자 + 답글 {len(out)-1}건"
        )
        return out

    # ---------- 파서 헬퍼 ----------
    @staticmethod
    def _extract_body(panel) -> str:
        body_el = panel.select_one(".lia-message-body-content") or panel.select_one(".lia-message-body")
        if not body_el:
            return ""
        # "원본 게시물의 답변 보기" 같은 메타 텍스트 제거
        text = body_el.get_text("\n", strip=True)
        text = re.sub(r"원본 게시물의 답변 보기\s*$", "", text).strip()
        text = re.sub(r"View solution in original post\s*$", "", text, flags=re.I).strip()
        return text

    @staticmethod
    def _extract_author(panel) -> Optional[str]:
        el = panel.select_one(".lia-user-name-link") or panel.select_one(".UserName")
        return el.get_text(strip=True) if el else None

    @staticmethod
    def _extract_kudos(panel) -> int:
        el = panel.select_one(".MessageKudosCount")
        if not el:
            return 0
        try:
            return int(re.sub(r"[^\d]", "", el.get_text(strip=True)) or 0)
        except ValueError:
            return 0

    @staticmethod
    def _extract_date(panel) -> Optional[datetime]:
        """형식: '‎05-07-2026' + '04:02 PM' → datetime(MM-DD-YYYY HH:MM)"""
        date_el = panel.select_one(".local-date")
        time_el = panel.select_one(".local-time")
        if not date_el:
            return None
        date_text = date_el.get_text(strip=True).replace("‎", "").strip()
        time_text = time_el.get_text(strip=True).replace("‎", "").strip() if time_el else ""
        try:
            if time_text:
                dt = datetime.strptime(f"{date_text} {time_text}", "%m-%d-%Y %I:%M %p")
            else:
                dt = datetime.strptime(date_text, "%m-%d-%Y")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    # ---------- httpx client with session cookies ----------
    def _make_httpx_client_with_session(self) -> httpx.AsyncClient:
        cookies = {}
        for c in self.session_cookies:
            # Playwright cookie dict → name/value
            name = c.get("name")
            value = c.get("value")
            if name and value:
                cookies[name] = value
        return httpx.AsyncClient(
            headers={
                "User-Agent": self._random_ua(),
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            },
            cookies=cookies,
            timeout=30.0,
            follow_redirects=True,
        )
