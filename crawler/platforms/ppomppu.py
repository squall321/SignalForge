"""
ppomppu 크롤러 — httpx + BeautifulSoup
ppomppu.co.kr 휴대폰 게시판에서 삼성 Galaxy 관련 VOC 수집
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

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ppomppu.co.kr"

# 크롤링 대상 게시판
PPOMPPU_BOARDS = [
    ("phone",    "휴대폰게시판"),
    ("review",   "리뷰게시판"),
]

# ppomppu 게시판 목록 URL
BOARD_LIST_URL = "{base}/zboard/zboard.php?id={board}&page={page}"

GALAXY_KEYWORDS = [
    "갤럭시", "Galaxy", "S25", "S24", "S23", "S22", "폴드", "Fold", "플립", "Flip",
    "버즈", "Buds", "워치", "Watch", "삼성", "Samsung", "울트라", "Ultra",
    "iPhone", "아이폰", "Pixel", "픽셀",
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


# 상세 페이지로 본문/댓글까지 긁을 게시물 최대 개수
MAX_POSTS = _env_int("PPOMPPU_MAX_POSTS", 150)
# 목록 스캔 페이지 수 (1-indexed range 상한 = LIST_PAGES+1)
# BACKFILL_PAGES 환경변수로 옛 글 백카탈로그 수집 시 50~100 까지 확장
LIST_PAGES = _env_int("PPOMPPU_BACKFILL_PAGES", 12)


# @lat: PpomppuCrawler — [[crawler#Platform Strategy]] 참조.
class PpomppuCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.5

    def __init__(self, platform_code: str = "ppomppu", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        # 1단계: 목록 페이지에서 Galaxy 관련 게시물(제목 RawVOC) 수집
        list_posts: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            for board_code, board_name in PPOMPPU_BOARDS:
                for page in range(1, LIST_PAGES + 1):  # 최근 N페이지
                    try:
                        posts = await self._fetch_board_page(client, board_code, page)
                        filtered = [p for p in posts if self._is_galaxy_related(p)]
                        list_posts.extend(filtered)
                        logger.info(f"  ppomppu {board_name} p{page}: {len(filtered)}/{len(posts)}건")
                        await self._random_delay()
                    except Exception as e:
                        logger.warning(f"  ppomppu {board_name} p{page} 실패: {e}")

            # 최근 게시물 우선, MAX_POSTS개로 제한
            list_posts = list_posts[:MAX_POSTS]
            logger.info(f"  ppomppu 상세 수집 대상: {len(list_posts)}건 (cap {MAX_POSTS})")

            # 2단계: 각 게시물 상세 페이지 → 본문 + 댓글 RawVOC 생성
            raw_vocs: List[RawVOC] = []
            for post in list_posts:
                try:
                    await self._random_delay()
                    detail_vocs = await self._fetch_post_detail(client, post)
                    raw_vocs.extend(detail_vocs)
                except Exception as e:
                    logger.warning(f"  ppomppu 상세 실패 {post.source_url}: {e}")

        # 2026-06-08 C3: MX 통합 키워드 필터 강제
        from nlp.mx_keywords import is_mx_relevant
        before = len(raw_vocs)
        raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
        logger.info(f"ppomppu 수집 완료: {len(raw_vocs)}/{before}건 (MX 필터 적용)")
        return raw_vocs

    async def _fetch_post_detail(
        self, client: httpx.AsyncClient, post: RawVOC
    ) -> List[RawVOC]:
        post_url = post.source_url
        resp = await client.get(post_url)
        resp.raise_for_status()
        # ppomppu는 EUC-KR — 한글 깨짐 방지 위해 명시적으로 디코딩
        html = resp.content.decode("euc-kr", "ignore")
        # 상세 페이지는 td에 class 속성이 2개라 html.parser가 망가짐 → lxml 사용
        soup = BeautifulSoup(html, "lxml")

        title = post.content.strip()
        out: List[RawVOC] = []

        # --- 본문 ---
        body_el = soup.select_one("td.board-contents")
        body_text = ""
        if body_el:
            body_text = body_el.get_text("\n", strip=True)
        body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()

        comments = self._parse_comments(soup, post_url)

        body_uid = hashlib.md5(post_url.encode()).hexdigest()[:16]
        out.append(RawVOC(
            external_id=body_uid,
            content=f"{title}\n{body_text}".strip(),
            source_url=post_url,
            author_name=post.author_name,
            published_at=post.published_at,
            likes_count=post.likes_count,
            comments_count=len(comments),
            country_code="KR",
        ))
        out.extend(comments)
        return out

    def _parse_comments(self, soup: BeautifulSoup, post_url: str) -> List[RawVOC]:
        results: List[RawVOC] = []
        i = 0
        for cc in soup.select("div[id^=commentContent_]"):
            cid = cc.get("id", "").split("_")[-1]
            text = cc.get_text("\n", strip=True)
            text = re.sub(r"\n{2,}", "\n", text).strip()

            # 삭제/빈/스티커전용/너무 짧은 댓글 제외
            if not text or len(text) < 5:
                continue
            if text.startswith("삭제된 댓글") or "삭제된 코멘트" in text:
                continue

            # 작성자/날짜/추천: 댓글 블록(comment_line) 기준
            blk = cc.find_parent("div", class_="comment_line")
            author = "익명"
            published_at = None
            if blk:
                a_el = blk.select_one("b a")
                if a_el:
                    a_txt = a_el.get_text(strip=True)
                    if a_txt:
                        author = a_txt
                day_el = blk.select_one("font.eng-day")
                if day_el:
                    published_at = self._parse_comment_date(day_el.get_text(strip=True))

            like_el = soup.select_one(f"#vote_cnt_{cid}")
            like_count = 0
            if like_el:
                like_count = int(re.sub(r"[^\d]", "", like_el.get_text(strip=True)) or 0)

            i += 1
            # 안정적 댓글 ID(cid) 우선 — 주기 재크롤 시 중복 방지. 없으면 순번 fallback.
            ckey = cid or f"i{i}"
            cuid = hashlib.md5(f"{post_url}#c{ckey}".encode()).hexdigest()[:16]
            results.append(RawVOC(
                external_id=cuid,
                content=text,
                source_url=post_url,
                author_name=author,
                published_at=published_at,
                likes_count=like_count,
                country_code="KR",
            ))
        return results

    def _parse_comment_date(self, text: str):
        """'2026-05-16', '09:15:05', '09:15:05 *'(수정표시) 파싱 — KST 표시 → UTC 저장."""
        text = text.strip().rstrip("*").strip()
        try:
            if re.match(r"\d{4}-\d{2}-\d{2}", text):
                return datetime.strptime(text[:10], "%Y-%m-%d").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
            if re.match(r"\d{2}:\d{2}:\d{2}", text):
                now = datetime.now(KST)
                return datetime.strptime(text[:8], "%H:%M:%S").replace(
                    year=now.year, month=now.month, day=now.day, tzinfo=KST
                ).astimezone(timezone.utc)
            if re.match(r"\d{2}:\d{2}", text):
                now = datetime.now(KST)
                return datetime.strptime(text[:5], "%H:%M").replace(
                    year=now.year, month=now.month, day=now.day, tzinfo=KST
                ).astimezone(timezone.utc)
        except Exception:
            pass
        return None

    async def _fetch_board_page(
        self, client: httpx.AsyncClient, board_code: str, page: int
    ) -> List[RawVOC]:
        url = BOARD_LIST_URL.format(base=BASE_URL, board=board_code, page=page)
        resp = await client.get(url)
        resp.raise_for_status()
        return self._parse_board_list(resp.text, board_code)

    def _parse_board_list(self, html: str, board_code: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        results = []

        # ppomppu 테이블 행 — 실제 클래스: tr.baseList
        for row in soup.select("tr.baseList"):
            try:
                cols = row.find_all("td")  # 중첩 포함 전체 td
                if len(cols) < 4:
                    continue

                # 공지사항/광고 제외 (번호 셀이 숫자가 아님)
                num_text = cols[0].get_text(strip=True)
                if not num_text.isdigit():
                    continue

                # 제목: a.baseList-title
                title_el = row.select_one("a.baseList-title")
                if not title_el:
                    continue
                title = title_el.select_one("span").get_text(strip=True) if title_el.select_one("span") else title_el.get_text(strip=True)
                if not title:
                    continue

                href = title_el.get("href", "")
                post_url = f"{BASE_URL}/zboard/{href}" if not href.startswith("http") else href

                # 댓글 수: 제목 td 안의 .baseList-c
                comment_el = row.select_one(".baseList-c")
                comment_count = int(re.sub(r"[^\d]", "", comment_el.get_text(strip=True)) or 0) if comment_el else 0

                # 작성자: cols[2]
                author_el = cols[2].select_one(".list_name a") or cols[2]
                author = author_el.get_text(strip=True) if author_el else "익명"

                # 날짜: cols[3]
                date_text = cols[3].get_text(strip=True) if len(cols) > 3 else ""
                published_at = self._parse_ppomppu_date(date_text)

                # 추천: cols[4] (.baseList-rec)
                like_count = int(re.sub(r"[^\d]", "", cols[4].get_text(strip=True)) or 0) if len(cols) > 4 else 0

                # 조회수: cols[5] (.baseList-views)
                view_count = int(re.sub(r"[^\d]", "", cols[5].get_text(strip=True)) or 0) if len(cols) > 5 else 0

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
                logger.debug(f"ppomppu 게시물 파싱 실패: {e}")

        return results

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        content_lower = voc.content.lower()
        return any(kw.lower() in content_lower for kw in GALAXY_KEYWORDS)

    def _parse_ppomppu_date(self, text: str):
        """'26/05/15', '14:32', '14:32:59' 파싱"""
        text = text.strip()
        try:
            if re.match(r"\d{2}/\d{2}/\d{2}", text):
                return datetime.strptime(text[:8], "%y/%m/%d").replace(tzinfo=KST).astimezone(timezone.utc)
            elif re.match(r"\d{2}:\d{2}:\d{2}", text):
                now = datetime.now(KST)
                t = datetime.strptime(text[:8], "%H:%M:%S").replace(
                    year=now.year, month=now.month, day=now.day, tzinfo=KST
                ).astimezone(timezone.utc)
                return t
            elif re.match(r"\d{2}:\d{2}", text):
                now = datetime.now(KST)
                t = datetime.strptime(text[:5], "%H:%M").replace(
                    year=now.year, month=now.month, day=now.day, tzinfo=KST
                ).astimezone(timezone.utc)
                return t
        except Exception:
            pass
        return None
