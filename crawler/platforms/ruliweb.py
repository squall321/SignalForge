"""
Ruliweb 크롤러 — httpx + BeautifulSoup
bbs.ruliweb.com 안드/애플 기기 게시판에서 삼성 Galaxy 관련 VOC 수집
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

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC
from nlp.mx_keywords import is_mx_relevant

logger = logging.getLogger(__name__)

# Ruliweb 크롤링 대상 게시판 (board_id, 표시명)
# 320008: 안드 기기 (Android 기기 — Galaxy 메인)
# 320001: 애플 기기 (iPhone 비교 컨텍스트)
RULIWEB_BOARDS = [
    ("320008", "안드기기"),
    ("320001", "애플기기"),
]

BASE_URL = "https://bbs.ruliweb.com"
BOARD_LIST_URL = "{base}/community/board/{board}?page={page}"

MAX_POSTS = 80
LIST_PAGES = 5
MIN_DELAY = 1.5
MAX_DELAY = 3.5

GALAXY_KEYWORDS = [
    # Galaxy 2025-26 + 구세대
    "갤럭시", "Galaxy", "S26", "S25", "S24", "S23", "S22",
    "Fold", "Flip", "폴드", "플립", "Z폴드", "Z플립",
    "버즈", "Buds", "워치", "Watch", "링", "Ring", "Ultra", "울트라",
    # 경쟁사 (비교용)
    "iPhone", "아이폰", "Pixel", "픽셀",
    # 일반 폰 컨텍스트
    "스마트폰", "휴대폰", "안드로이드",
]


class RuliwebCrawler(BaseCrawler):
    MIN_DELAY = MIN_DELAY
    MAX_DELAY = MAX_DELAY

    def __init__(self, platform_code: str = "ruliweb", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        list_posts: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            for board_id, board_name in RULIWEB_BOARDS:
                for page in range(1, LIST_PAGES + 1):  # Ruliweb은 1-indexed
                    try:
                        posts = await self._fetch_board_page(client, board_id, page)
                        filtered = [p for p in posts if self._is_galaxy_related(p)]
                        list_posts.extend(filtered)
                        logger.info(f"  Ruliweb {board_name} p{page}: {len(filtered)}/{len(posts)}건")
                        await self._random_delay()
                    except Exception as e:
                        logger.warning(f"  Ruliweb {board_name} p{page} 실패: {e}")

            list_posts.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target_posts = list_posts[:MAX_POSTS]
            logger.info(
                f"Ruliweb 리스트 {len(list_posts)}건 중 상위 {len(target_posts)}건 상세 수집 시작"
            )

            raw_vocs: List[RawVOC] = []
            for post in target_posts:
                await self._random_delay()
                try:
                    detail_vocs = await self._fetch_post_detail(client, post)
                    raw_vocs.extend(detail_vocs)
                except Exception as e:
                    logger.warning(f"  Ruliweb 상세 수집 실패 ({post.source_url}): {e}")

        before = len(raw_vocs)
        raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
        logger.info(
            f"Ruliweb 수집 완료: {len(raw_vocs)}/{before} (MX 필터, 게시물 {len(target_posts)}건)"
        )
        return raw_vocs

    async def _fetch_board_page(
        self, client: httpx.AsyncClient, board_id: str, page: int
    ) -> List[RawVOC]:
        url = BOARD_LIST_URL.format(base=BASE_URL, board=board_id, page=page)
        resp = await client.get(url)
        resp.raise_for_status()
        return self._parse_board_list(resp.text, board_id)

    def _parse_board_list(self, html: str, board_id: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[RawVOC] = []

        for row in soup.select("tr.table_body"):
            try:
                classes = row.get("class") or []
                if "notice" in classes:
                    continue

                link_el = row.select_one("td.subject a.subject_link")
                if not link_el:
                    continue

                href = link_el.get("href", "")
                if not href:
                    continue
                post_url = href.split("#")[0]

                # 제목: <a> 안의 직접 텍스트 (자식 <span>, <i> 제외)
                title_parts = [
                    t for t in link_el.find_all(string=True, recursive=False)
                ]
                title = " ".join(p.strip() for p in title_parts if p.strip())
                if not title:
                    title = link_el.get_text(" ", strip=True)
                    # 댓글 수 표기 (6) 같은 꼬리표 제거
                    title = re.sub(r"\s*\(\d+\)\s*$", "", title).strip()
                if not title:
                    continue

                # 작성자
                writer_el = row.select_one("td.writer a")
                author = writer_el.get_text(strip=True) if writer_el else "익명"

                # 날짜: "2026.05.23" 또는 "11:33" (오늘)
                time_el = row.select_one("td.time")
                date_text = time_el.get_text(strip=True) if time_el else ""
                published_at = self._parse_ruliweb_list_date(date_text)

                # 추천
                rec_el = row.select_one("td.recomd")
                try:
                    likes = int(re.sub(r"[^\d]", "", rec_el.get_text(strip=True)) or 0) if rec_el else 0
                except ValueError:
                    likes = 0

                # 댓글 수 (제목 옆 (N))
                reply_el = link_el.select_one(".num_reply")
                try:
                    comments = int(re.sub(r"[^\d]", "", reply_el.get_text(strip=True)) or 0) if reply_el else 0
                except ValueError:
                    comments = 0

                uid = hashlib.md5(post_url.encode()).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=uid,
                    content=title,
                    source_url=post_url,
                    author_name=author,
                    published_at=published_at,
                    likes_count=likes,
                    comments_count=comments,
                    country_code="KR",
                ))
            except Exception as e:
                logger.debug(f"Ruliweb 게시물 파싱 실패: {e}")

        return results

    async def _fetch_post_detail(
        self, client: httpx.AsyncClient, post: RawVOC
    ) -> List[RawVOC]:
        resp = await client.get(post.source_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        post_url = post.source_url
        title = post.content

        # 본문
        body_el = soup.select_one(".view_content")
        body_text = body_el.get_text("\n", strip=True) if body_el else ""

        # 게시물 작성 시각 (상세에서 가져와 보강)
        regdate_el = soup.select_one(".user_info .regdate") or soup.select_one("span.regdate")
        post_dt = self._parse_ruliweb_detail_date(regdate_el.get_text(strip=True)) if regdate_el else None
        published_at = post_dt or post.published_at

        # 댓글 파싱
        comment_vocs: List[RawVOC] = []
        for row in soup.select("tr.comment_element"):
            try:
                classes = row.get("class") or []
                if "deleted" in classes or "blocked" in classes:
                    continue

                text_el = row.select_one("td.comment .text_wrapper .text") \
                    or row.select_one(".text_wrapper .text") \
                    or row.select_one("td.comment .text")
                if not text_el:
                    continue
                ctext = text_el.get_text("\n", strip=True)
                if not ctext or len(ctext) < 3:
                    continue

                # 안정적 ID: tr id="ct_XXXXX"
                row_id = row.get("id") or ""
                csn = row_id.replace("ct_", "") if row_id else f"i{len(comment_vocs)+1}"

                nick_el = row.select_one(".nick_link strong span") \
                    or row.select_one(".nick a") \
                    or row.select_one(".nick")
                cauthor = nick_el.get_text(strip=True) if nick_el else "익명"
                if not cauthor:
                    cauthor = "익명"

                date_el = row.select_one("span.time") or row.select_one(".time")
                cdate = self._parse_ruliweb_comment_date(date_el.get_text(strip=True)) if date_el else None

                comment_vocs.append(RawVOC(
                    external_id=hashlib.md5(f"{post_url}#c{csn}".encode()).hexdigest()[:16],
                    content=ctext,
                    source_url=post_url,
                    author_name=cauthor,
                    published_at=cdate,
                    likes_count=0,
                    country_code="KR",
                ))
            except Exception as e:
                logger.debug(f"Ruliweb 댓글 파싱 실패: {e}")

        body_voc = RawVOC(
            external_id=hashlib.md5(post_url.encode()).hexdigest()[:16],
            content=f"{title}\n{body_text}".strip(),
            source_url=post_url,
            author_name=post.author_name,
            published_at=published_at,
            likes_count=post.likes_count,
            comments_count=len(comment_vocs),
            country_code="KR",
        )

        logger.info(
            f"  Ruliweb 상세 {post_url.split('/')[-1]}: "
            f"본문 {len(body_text)}자 + 댓글 {len(comment_vocs)}건"
        )
        return [body_voc] + comment_vocs

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        content_lower = voc.content.lower()
        return any(kw.lower() in content_lower for kw in GALAXY_KEYWORDS)

    def _parse_ruliweb_list_date(self, text: str):
        """리스트 시각: '2026.05.23' 또는 '11:33' (오늘)"""
        text = text.strip()
        try:
            if re.match(r"\d{4}\.\d{2}\.\d{2}", text):
                return datetime.strptime(text[:10], "%Y.%m.%d").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
            elif re.match(r"\d{2}:\d{2}", text):
                now = datetime.now(KST)
                t = datetime.strptime(text[:5], "%H:%M").replace(
                    year=now.year, month=now.month, day=now.day, tzinfo=KST
                ).astimezone(timezone.utc)
                return t
        except Exception:
            pass
        return None

    def _parse_ruliweb_detail_date(self, text: str):
        """상세 시각: '2026.05.23 (22:17:20)'"""
        text = text.strip()
        try:
            m = re.match(r"(\d{4}\.\d{2}\.\d{2})\s*\((\d{2}:\d{2}:\d{2})\)", text)
            if m:
                return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y.%m.%d %H:%M:%S").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
            m = re.match(r"(\d{4}\.\d{2}\.\d{2})\s*(\d{2}:\d{2}:\d{2})", text)
            if m:
                return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y.%m.%d %H:%M:%S").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
            m = re.match(r"\d{4}\.\d{2}\.\d{2}", text)
            if m:
                return datetime.strptime(text[:10], "%Y.%m.%d").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
        except Exception:
            pass
        return None

    def _parse_ruliweb_comment_date(self, text: str):
        """댓글 시각: '26.05.24 11:33'"""
        text = text.strip()
        try:
            m = re.match(r"(\d{2})\.(\d{2})\.(\d{2})\s+(\d{2}):(\d{2})", text)
            if m:
                yy, mm, dd, HH, MM = m.groups()
                year = 2000 + int(yy)
                return datetime(year, int(mm), int(dd), int(HH), int(MM), tzinfo=timezone.utc)
            m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})\s+(\d{2}):(\d{2})", text)
            if m:
                yyyy, mm, dd, HH, MM = m.groups()
                return datetime(int(yyyy), int(mm), int(dd), int(HH), int(MM), tzinfo=timezone.utc)
        except Exception:
            pass
        return None
