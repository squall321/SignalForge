"""
FMKorea 크롤러 — Playwright 보안 우회 + httpx 본문 수집
fmkorea.com '디지털' 게시판 모바일/태블릿/리뷰 카테고리에서 Galaxy 관련 VOC 수집

FMKorea는 자체 JavaScript/WASM 보안 챌린지(에펨코리아 보안 시스템)를
거치지 않으면 HTTP 430을 반환한다. Playwright로 챌린지를 한 번 통과해
세션 쿠키(lite_year, fm5, PHPSESSID 등)를 획득한 뒤, 빠른 httpx로 본문/댓글을 수집한다.
"""
import hashlib
import os
import re
import sys
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
from typing import List
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC
from base.proxy_pool import build_proxy_client_kwargs

logger = logging.getLogger(__name__)

BASE_URL = "https://www.fmkorea.com"

# FMKorea의 '디지털 (컴퓨터/폰/IT)' 게시판 카테고리들.
# mid=mobile 는 home 으로 라우팅되므로 사용 불가. mid=digital + category 조합 사용.
FMK_BOARDS = [
    ("digital", "9068553",    "모바일"),
    ("digital", "6890542001", "태블릿"),
    ("digital", "2357831175", "리뷰"),
]

def _env_int(name: str, default: int, *, min_value: int = 1) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return max(min_value, v)


# 상세 페이지에서 본문/댓글 수집 최대 게시물 수 (최신순)
MAX_POSTS = _env_int("FMKOREA_MAX_POSTS", 80)
# 보드별 스캔 페이지 수
# BACKFILL_PAGES 환경변수로 옛 글 백카탈로그 수집 시 50~100 까지 확장
LIST_PAGES = _env_int("FMKOREA_BACKFILL_PAGES", 5)

BOARD_LIST_URL = "{base}/index.php?mid={mid}&category={cat}&page={page}"

# 제품 키워드 (Galaxy + 경쟁사). clien.py 와 일관.
GALAXY_KEYWORDS = [
    "갤럭시", "Galaxy", "S25", "S24", "S23", "S22", "Fold", "Flip", "폴드", "플립",
    "버즈", "Buds", "워치", "Watch", "링", "Ring", "S울트라", "Ultra",
    "iPhone", "아이폰", "Pixel", "픽셀",
]


# @lat: FMKoreaCrawler — [[crawler#Platform Strategy]] 참조.
class FMKoreaCrawler(BaseCrawler):
    # Harvest 3 트랙 A: 1.5~3.5s → 3.0~6.0s, 보드 페이지 추가 jitter.
    # FMKorea 는 PHPSESSID + UA 페어가 세션 인증이므로 UA 자체는 회전하지 않고
    # Accept-Language 만 매 요청 회전 + sleep 강화로 봇 패턴 분산.
    MIN_DELAY = 3.0
    MAX_DELAY = 6.0
    BOARD_PAGE_EXTRA_JITTER = 1.5

    def __init__(self, platform_code: str = "fmkorea", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        # 1) Playwright 로 보안 챌린지 통과 + 쿠키/UA 획득
        cookie_header, user_agent = await self._bootstrap_session()
        if not cookie_header:
            logger.warning("FMKorea 보안 챌린지 통과 실패 — 빈 결과 반환")
            return []

        headers = {
            "User-Agent": user_agent,
            "Cookie": cookie_header,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": self._random_accept_language(),
            "Referer": BASE_URL + "/",
        }

        list_posts: List[RawVOC] = []
        # Harvest 3p 트랙 P1: FMKOREA_USE_PROXY=true 시 Tor SOCKS5 등 라우팅.
        # env 미설정/probe 실패 시 proxy_kwargs 는 빈 dict → 직접 호출 폴백.
        proxy_kwargs = build_proxy_client_kwargs(prefix="FMKOREA")
        async with httpx.AsyncClient(
            headers=headers, timeout=30.0, follow_redirects=True, **proxy_kwargs,
        ) as client:
            for mid, cat, name in FMK_BOARDS:
                for page in range(1, LIST_PAGES + 1):
                    try:
                        posts = await self._fetch_board_page(client, mid, cat, page)
                        filtered = [p for p in posts if self._is_galaxy_related(p)]
                        list_posts.extend(filtered)
                        logger.info(f"  FMKorea {name} p{page}: {len(filtered)}/{len(posts)}건")
                        await self._random_delay()
                        # 보드 페이지 추가 jitter (목록 빠르게 도는 패턴 완화)
                        import asyncio as _asyncio
                        import random as _random
                        await _asyncio.sleep(_random.uniform(0, self.BOARD_PAGE_EXTRA_JITTER))
                    except Exception as e:
                        logger.warning(f"  FMKorea {name} p{page} 실패: {e}")

            # 최신순 정렬 + MAX_POSTS 캡
            list_posts.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target_posts = list_posts[:MAX_POSTS]
            logger.info(
                f"FMKorea 리스트 {len(list_posts)}건 중 상위 {len(target_posts)}건 상세 수집 시작"
            )

            raw_vocs: List[RawVOC] = []
            for post in target_posts:
                await self._random_delay()
                try:
                    detail_vocs = await self._fetch_post_detail(client, post)
                    raw_vocs.extend(detail_vocs)
                except Exception as e:
                    logger.warning(f"  FMKorea 상세 수집 실패 ({post.source_url}): {e}")

        # MX 필터 적용 (Data Clean 2 / D1)
        from nlp.mx_keywords import is_mx_relevant
        before_n = len(raw_vocs)
        raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
        logger.info(
            f"FMKorea 수집 완료: {len(raw_vocs)}건 (MX 필터 적용 {before_n}→{len(raw_vocs)}, 게시물 {len(target_posts)}건)"
        )
        return raw_vocs

    async def _bootstrap_session(self) -> tuple[str, str]:
        """Playwright 로 보안 챌린지 통과 후 쿠키/UA 추출"""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("Playwright 미설치 — FMKorea 크롤링 불가")
            return "", ""

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                ctx = await browser.new_context(
                    user_agent=self._random_ua(),
                    locale="ko-KR",
                )
                page = await ctx.new_page()
                # 홈 페이지로 챌린지 통과
                await page.goto(BASE_URL + "/", wait_until="domcontentloaded", timeout=30000)
                # 챌린지 자동 redirect 완료 대기
                await page.wait_for_timeout(5000)
                # 한 번 더 보드를 열어서 challenge 가 끝났는지 확인
                await page.goto(
                    f"{BASE_URL}/index.php?mid=digital&category={FMK_BOARDS[0][1]}",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                await page.wait_for_timeout(2000)

                cookies = await ctx.cookies()
                ua = await page.evaluate("navigator.userAgent")
            finally:
                await browser.close()

        cookie_header = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies if "fmkorea" in (c.get("domain") or "")
        )
        return cookie_header, ua

    async def _fetch_board_page(self, client: httpx.AsyncClient, mid: str, cat: str, page: int) -> List[RawVOC]:
        url = BOARD_LIST_URL.format(base=BASE_URL, mid=mid, cat=cat, page=page)
        # Harvest 3 트랙 A: Accept-Language 매 요청 회전 (UA/Cookie 는 세션 페어 유지)
        client.headers["Accept-Language"] = self._random_accept_language()
        resp = await client.get(url)
        resp.raise_for_status()
        return self._parse_board_list(resp.text)

    def _parse_board_list(self, html: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[RawVOC] = []

        # tbody tr — notice 클래스 가진 row 는 공지/고정글이므로 스킵
        for tr in soup.select("tbody tr"):
            classes = tr.get("class") or []
            if any(cls.startswith("notice") for cls in classes):
                continue
            try:
                title_a = tr.select_one("td.title a.hx")
                if not title_a:
                    continue
                href = title_a.get("href", "")
                if not href or "document_srl=" not in href:
                    continue
                title = title_a.get_text(strip=True)
                if not title:
                    continue

                post_url = BASE_URL + href if href.startswith("/") else href

                # 작성자
                author_a = tr.select_one("td.author .member_plate")
                author = author_a.get_text(strip=True) if author_a else "익명"

                # 댓글 수 (a.replyNum 의 텍스트)
                reply_a = tr.select_one("a.replyNum")
                try:
                    comment_count = int(re.sub(r"[^\d]", "", reply_a.get_text(strip=True))) if reply_a else 0
                except ValueError:
                    comment_count = 0

                # 작성 시각 (td.time : 같은날엔 'HH:MM', 다른 날엔 'YY.MM.DD')
                time_td = tr.select_one("td.time")
                published_at = self._parse_fmkorea_date(time_td.get_text(strip=True)) if time_td else None

                # 조회수 (첫 m_no 셀) - 추천 수는 두번째 m_no_voted 셀
                voted_td = tr.select_one("td.m_no_voted")
                try:
                    like_count = int(re.sub(r"[^\d]", "", voted_td.get_text(strip=True)) or 0) if voted_td else 0
                except ValueError:
                    like_count = 0

                uid = hashlib.md5(post_url.encode()).hexdigest()[:16]
                results.append(RawVOC(
                    external_id=uid,
                    content=title,
                    source_url=post_url,
                    author_name=author,
                    published_at=published_at,
                    likes_count=like_count,
                    comments_count=comment_count,
                    country_code="KR",
                ))
            except Exception as e:
                logger.debug(f"FMKorea 게시물 파싱 실패: {e}")

        return results

    async def _fetch_post_detail(self, client: httpx.AsyncClient, post: RawVOC) -> List[RawVOC]:
        """본문 + 댓글을 RawVOC 리스트로 변환"""
        # Harvest 3 트랙 A: Accept-Language 매 요청 회전
        client.headers["Accept-Language"] = self._random_accept_language()
        resp = await client.get(post.source_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        post_url = post.source_url
        title = post.content

        # 본문: article.rd_body 내부 div.xe_content 우선, 없으면 .xe_content 첫번째
        body_el = soup.select_one("article.rd_body .xe_content") or soup.select_one(".rd_body .xe_content") or soup.select_one(".xe_content")
        body_text = body_el.get_text("\n", strip=True) if body_el else ""

        # 본문 페이지의 정확한 작성 시각 (top_area .date)
        date_el = soup.select_one(".top_area .date")
        if date_el:
            d = self._parse_fmkorea_date(date_el.get_text(strip=True))
            if d:
                # 페이지 헤더의 'YYYY.MM.DD HH:MM' 이 더 정확하므로 덮어쓰기
                post = RawVOC(**{**post.__dict__, "published_at": d})

        # 댓글
        comment_vocs: List[RawVOC] = []
        for idx, li in enumerate(soup.select("li.fdb_itm[id^=comment_]"), start=1):
            cls = li.get("class") or []
            # 삭제 댓글 스킵 (FMKorea: 'deleted', 'blame' 등 사용)
            if any(c in cls for c in ("deleted", "blame_comment")):
                continue

            content_el = li.select_one(".comment-content .xe_content") or li.select_one(".comment-content")
            if not content_el:
                continue
            # 멘션('@닉네임') 링크 제거 후 텍스트화
            for a in content_el.select("a.findParent"):
                a.decompose()
            ctext = content_el.get_text("\n", strip=True)
            if not ctext or len(ctext) < 5:
                continue

            # 댓글 ID (li id="comment_NNN")
            cid_raw = li.get("id", "") or ""
            stable_id = cid_raw.replace("comment_", "") or f"i{idx}"

            author_el = li.select_one(".meta .member_plate")
            cauthor = author_el.get_text(strip=True) if author_el else "익명"

            cdate_el = li.select_one(".meta .date")
            cdate = self._parse_fmkorea_date(cdate_el.get_text(strip=True)) if cdate_el else None

            # 추천수
            vote_el = li.select_one(".voted_count")
            try:
                clikes = int(re.sub(r"[^\d]", "", vote_el.get_text(strip=True)) or 0) if vote_el else 0
            except ValueError:
                clikes = 0

            comment_vocs.append(RawVOC(
                external_id=hashlib.md5(f"{post_url}#c{stable_id}".encode()).hexdigest()[:16],
                content=ctext,
                source_url=post_url,
                author_name=cauthor,
                published_at=cdate,
                likes_count=clikes,
                country_code="KR",
            ))

        body_voc = RawVOC(
            external_id=hashlib.md5(post_url.encode()).hexdigest()[:16],
            content=f"{title}\n{body_text}".strip(),
            source_url=post_url,
            author_name=post.author_name,
            published_at=post.published_at,
            likes_count=post.likes_count,
            comments_count=len(comment_vocs),
            country_code="KR",
        )

        # document_srl 추출 로그용
        srl_m = re.search(r"document_srl=(\d+)", post_url)
        srl = srl_m.group(1) if srl_m else "?"
        logger.info(
            f"  FMKorea 상세 {srl}: 본문 {len(body_text)}자 + 댓글 {len(comment_vocs)}건"
        )
        return [body_voc] + comment_vocs

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        content_lower = voc.content.lower()
        return any(kw.lower() in content_lower for kw in GALAXY_KEYWORDS)

    def _parse_fmkorea_date(self, text: str):
        """FMKorea 날짜 포맷 파싱.
        - '2026.05.28 11:43' (게시물 상세 헤더)
        - '26.05.28' / '05.28' (목록 td.time)
        - '11:43' (오늘, 목록 td.time)
        - '1 시간 전', '3 분 전' (상대 시각, 댓글)
        """
        text = (text or "").strip()
        now_kst = datetime.now(KST)
        try:
            # 'YYYY.MM.DD HH:MM'
            m = re.match(r"(\d{4})\.(\d{2})\.(\d{2}) (\d{2}):(\d{2})", text)
            if m:
                y, mo, d, h, mi = map(int, m.groups())
                return datetime(y, mo, d, h, mi, tzinfo=KST).astimezone(timezone.utc)
            # 'YY.MM.DD'
            m = re.match(r"^(\d{2})\.(\d{2})\.(\d{2})$", text)
            if m:
                y, mo, d = map(int, m.groups())
                return datetime(2000 + y, mo, d, tzinfo=KST).astimezone(timezone.utc)
            # 'MM.DD' — 올해 (KST 기준)
            m = re.match(r"^(\d{2})\.(\d{2})$", text)
            if m:
                mo, d = map(int, m.groups())
                return datetime(now_kst.year, mo, d, tzinfo=KST).astimezone(timezone.utc)
            # 'HH:MM' — 오늘 (KST 기준 자정 경계)
            m = re.match(r"^(\d{2}):(\d{2})$", text)
            if m:
                h, mi = map(int, m.groups())
                return now_kst.replace(hour=h, minute=mi, second=0, microsecond=0).astimezone(timezone.utc)
            # '1 시간 전', '30 분 전', '2 일 전'
            m = re.match(r"^(\d+)\s*분\s*전", text)
            if m:
                return (now_kst - timedelta(minutes=int(m.group(1)))).astimezone(timezone.utc)
            m = re.match(r"^(\d+)\s*시간\s*전", text)
            if m:
                return (now_kst - timedelta(hours=int(m.group(1)))).astimezone(timezone.utc)
            m = re.match(r"^(\d+)\s*일\s*전", text)
            if m:
                return (now_kst - timedelta(days=int(m.group(1)))).astimezone(timezone.utc)
        except Exception:
            pass
        return None
