"""
Danawa DPG 크롤러 — httpx + BeautifulSoup
dpg.danawa.com 소비자사용기/상품의견 게시판에서 삼성 Galaxy 관련 VOC 수집
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import List
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://dpg.danawa.com"

KST = timezone(timedelta(hours=9))

# Danawa DPG 크롤링 대상 게시판
# boardSeq=28 소비자사용기 (메인), boardSeq=33 상품의견
DANAWA_BOARDS = [
    (28, "소비자사용기"),
    (33, "상품의견"),
]

# 게시판 목록 URL 패턴
BOARD_LIST_URL = "{base}/bbs/list?boardSeq={board}&page={page}"

# 상세 페이지로 본문+댓글 수집할 게시물 캡
MAX_POSTS = 150
# 목록 스캔 페이지 수 (1-indexed range 상한 = LIST_PAGES+1)
LIST_PAGES = 12

# 제품 관련 검색 키워드 (clien/ppomppu와 동일 수준)
GALAXY_KEYWORDS = [
    "갤럭시", "Galaxy", "S25", "S24", "S23", "S22", "Fold", "Flip", "폴드", "플립",
    "버즈", "Buds", "워치", "Watch", "링", "Ring", "Ultra", "울트라",
    "삼성", "Samsung",
    "iPhone", "아이폰", "Pixel", "픽셀",
]


# @lat: DanawaCrawler — [[crawler#Platform Strategy]] 참조.
class DanawaCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "danawa", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        list_posts: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            for board_seq, board_name in DANAWA_BOARDS:
                for page in range(1, LIST_PAGES + 1):
                    try:
                        posts = await self._fetch_board_page(client, board_seq, page)
                        filtered = [p for p in posts if self._is_galaxy_related(p)]
                        list_posts.extend(filtered)
                        logger.info(
                            f"  Danawa {board_name} p{page}: {len(filtered)}/{len(posts)}건"
                        )
                        await self._random_delay()
                    except Exception as e:
                        logger.warning(f"  Danawa {board_name} p{page} 실패: {e}")

            # 최신순 정렬 후 상위 MAX_POSTS건만 상세 수집
            list_posts.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target_posts = list_posts[:MAX_POSTS]
            logger.info(
                f"Danawa 리스트 {len(list_posts)}건 중 상위 {len(target_posts)}건 상세 수집 시작"
            )

            raw_vocs: List[RawVOC] = []
            for post in target_posts:
                await self._random_delay()
                try:
                    detail_vocs = await self._fetch_post_detail(client, post)
                    raw_vocs.extend(detail_vocs)
                except Exception as e:
                    logger.warning(f"  Danawa 상세 수집 실패 ({post.source_url}): {e}")

        # 2026-06-08 C3: MX 통합 키워드 필터 강제
        from nlp.mx_keywords import is_mx_relevant
        before = len(raw_vocs)
        raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
        logger.info(
            f"Danawa 수집 완료: {len(raw_vocs)}/{before}건 (MX 필터 적용)"
        )
        return raw_vocs

    async def _fetch_board_page(
        self, client: httpx.AsyncClient, board_seq: int, page: int
    ) -> List[RawVOC]:
        url = BOARD_LIST_URL.format(base=BASE_URL, board=board_seq, page=page)
        resp = await client.get(url)
        resp.raise_for_status()
        return self._parse_board_list(resp.text)

    def _parse_board_list(self, html: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[RawVOC] = []

        for item in soup.select(".gallery_item"):
            try:
                # 제목 + URL: a.gall_desc_link (썸네일 a는 제목 텍스트 없음)
                link_el = item.select_one("a.gall_desc_link")
                if not link_el:
                    continue
                title_el = link_el.select_one(".post_title")
                if not title_el:
                    continue
                title = title_el.get_text(" ", strip=True)
                # "[사용기]" prefix 정리
                title = re.sub(r"^\[\s*사용기\s*\]\s*", "", title).strip()
                if not title:
                    continue

                href = link_el.get("href", "")
                post_url = f"{BASE_URL}{href}" if href.startswith("/") else href

                # 작성자
                author_el = item.select_one(".user_name")
                author = author_el.get_text(strip=True) if author_el else "익명"
                if not author:
                    author = "익명"

                # 날짜
                date_el = item.select_one(".post_date")
                published_at = (
                    self._parse_danawa_date(date_el.get_text(strip=True))
                    if date_el
                    else None
                )

                # 공감 수 / 댓글 수: .post_recom 안 두 개 strong.recom_num (순서대로 공감/댓글)
                recom_nums = item.select(".post_recom .recom_num")
                like_count = self._safe_int(recom_nums[0].get_text(strip=True)) if len(recom_nums) > 0 else 0
                comment_count = self._safe_int(recom_nums[1].get_text(strip=True)) if len(recom_nums) > 1 else 0

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
                logger.debug(f"Danawa 게시물 파싱 실패: {e}")

        return results

    async def _fetch_post_detail(
        self, client: httpx.AsyncClient, post: RawVOC
    ) -> List[RawVOC]:
        """상세 페이지에서 본문 + 댓글을 RawVOC로 변환"""
        resp = await client.get(post.source_url)
        resp.raise_for_status()
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        post_url = post.source_url
        title = post.content

        # 본문 — itemprop="articleBody" 가 가장 안정
        body_el = soup.select_one('[itemprop="articleBody"]') or soup.select_one(".arti_content")
        body_text = body_el.get_text("\n", strip=True) if body_el else ""
        body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()

        # 상세 페이지 날짜 보강 (.date_info 가 시:분:초까지 정확)
        date_el = soup.select_one(".article_area .date_info")
        if date_el:
            detailed_at = self._parse_danawa_date(date_el.get_text(strip=True))
            if detailed_at:
                post_published_at = detailed_at
            else:
                post_published_at = post.published_at
        else:
            post_published_at = post.published_at

        # 댓글 — commentInitData 인라인 JSON 파싱
        comment_vocs = self._parse_comments_from_inline_json(html, post_url)

        body_voc = RawVOC(
            external_id=hashlib.md5(post_url.encode()).hexdigest()[:16],
            content=f"{title}\n{body_text}".strip(),
            source_url=post_url,
            author_name=post.author_name,
            published_at=post_published_at,
            likes_count=post.likes_count,
            comments_count=len(comment_vocs),
            country_code="KR",
        )

        logger.info(
            f"  Danawa 상세 {post_url.split('listSeq=')[-1].split('&')[0]}: "
            f"본문 {len(body_text)}자 + 댓글 {len(comment_vocs)}건"
        )
        return [body_voc] + comment_vocs

    def _parse_comments_from_inline_json(self, html: str, post_url: str) -> List[RawVOC]:
        """페이지 내부 commentInitData 자바스크립트 변수에서 댓글 JSON 추출.

        구조: commentInitData = { ..., boardComment: { ..., commentItems: [...] } };

        JSON 본문은 한 줄에 `{...{...}...}` 형태로 중첩 객체가 들어있어 정규식 매칭이
        어렵다 — `boardComment :` 다음 첫 `{` 위치부터 brace 카운팅으로 정확히 잘라낸다.
        """
        results: List[RawVOC] = []
        anchor = re.search(r"boardComment\s*:\s*\{", html)
        if not anchor:
            return results

        start = anchor.end() - 1  # 첫 `{` 위치
        depth = 0
        end_idx = None
        in_str = False
        escape = False
        for i in range(start, len(html)):
            ch = html[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end_idx = i + 1
                        break
        if end_idx is None:
            return results
        try:
            data = json.loads(html[start:end_idx])
        except json.JSONDecodeError:
            return results

        items = (data.get("commentItems") or []) + (data.get("bestCommentItems") or [])
        seen_ids = set()
        for it in items:
            try:
                cid = it.get("id")
                if not cid or cid in seen_ids:
                    continue
                seen_ids.add(cid)

                if it.get("isDeleted") or it.get("status") not in (None, "NORMAL"):
                    continue

                ctext = (it.get("content") or "").strip()
                if not ctext or len(ctext) < 5:
                    continue

                cauthor = it.get("nickname") or it.get("memberId") or "익명"
                cdate_str = it.get("createDateTime")
                cdate = self._parse_iso_kst(cdate_str)
                clikes = int(it.get("recommendCount") or 0)

                results.append(RawVOC(
                    external_id=hashlib.md5(f"{post_url}#c{cid}".encode()).hexdigest()[:16],
                    content=ctext,
                    source_url=post_url,
                    author_name=cauthor,
                    published_at=cdate,
                    likes_count=clikes,
                    country_code="KR",
                ))
            except Exception as e:
                logger.debug(f"Danawa 댓글 항목 파싱 실패: {e}")

        return results

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        content_lower = voc.content.lower()
        return any(kw.lower() in content_lower for kw in GALAXY_KEYWORDS)

    @staticmethod
    def _safe_int(text: str) -> int:
        try:
            return int(re.sub(r"[^\d]", "", text or "") or 0)
        except ValueError:
            return 0

    def _parse_iso_kst(self, text):
        """'2026-05-29T15:18:36.000+09:00' → UTC datetime"""
        if not text:
            return None
        try:
            # python 3.11+ : fromisoformat 가 +09:00 처리
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _parse_danawa_date(self, text: str):
        """다나와 날짜 표기 파싱.
            '2026.05.28.'          — 목록 카드
            '2026.05.28. 17:49:11' — 상세 페이지
            '17:49' (오늘)          — 안전 fallback
        KST 표시 → UTC 저장.
        """
        text = (text or "").strip().rstrip(".")
        try:
            # 'YYYY.MM.DD HH:MM:SS' (rstrip 후 마지막 '.'은 사라짐)
            m = re.match(r"^(\d{4})\.(\d{2})\.(\d{2})\.?\s+(\d{2}):(\d{2}):(\d{2})$", text)
            if m:
                y, mo, d, h, mi, s = map(int, m.groups())
                return datetime(y, mo, d, h, mi, s, tzinfo=KST).astimezone(timezone.utc)
            # 'YYYY.MM.DD HH:MM'
            m = re.match(r"^(\d{4})\.(\d{2})\.(\d{2})\.?\s+(\d{2}):(\d{2})$", text)
            if m:
                y, mo, d, h, mi = map(int, m.groups())
                return datetime(y, mo, d, h, mi, tzinfo=KST).astimezone(timezone.utc)
            # 'YYYY.MM.DD'
            m = re.match(r"^(\d{4})\.(\d{2})\.(\d{2})$", text)
            if m:
                y, mo, d = map(int, m.groups())
                return datetime(y, mo, d, tzinfo=KST).astimezone(timezone.utc)
            # 'HH:MM' (오늘)
            m = re.match(r"^(\d{2}):(\d{2})$", text)
            if m:
                now = datetime.now(KST)
                h, mi = map(int, m.groups())
                return now.replace(hour=h, minute=mi, second=0, microsecond=0).astimezone(timezone.utc)
        except Exception:
            pass
        return None
