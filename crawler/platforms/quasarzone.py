"""
퀘이사존 크롤러 — httpx + BeautifulSoup
quasarzone.com 스마트폰/태블릿(qf_mobile) 게시판 + 모바일뉴스(qn_mobile) 게시판에서
삼성 Galaxy 관련 VOC 수집. 자격증명 불필요 (공개 게시판).

본문: 상세 페이지의 <textarea id="org_contents"> 안 HTML-escaped 콘텐츠 추출
댓글: /comments/{board}/getComment JSON API 호출 (안정 ID = comment["id"])
"""
import hashlib
import html as html_lib
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://quasarzone.com"

KST = timezone(timedelta(hours=9))

# (게시판 코드, 표시명)
#   - qf_mobile : 스마트폰/태블릿 (사용자 질의·후기)
#   - qn_mobile : 모바일 뉴스 (기사 + 댓글 토론)
QUASAR_BOARDS = [
    ("qf_mobile", "스마트폰/태블릿"),
    ("qn_mobile", "모바일뉴스"),
]

LIST_URL = "{base}/bbs/{board}?page={page}"
COMMENT_URL = "{base}/comments/{board}/getComment"

# 상세 페이지에서 본문/댓글을 수집할 최대 게시물 수 (최신순)
MAX_POSTS = 150
# 목록 스캔 페이지 수 (1-indexed range 상한 = LIST_PAGES+1)
LIST_PAGES = 12

GALAXY_KEYWORDS = [
    "갤럭시", "Galaxy", "S25", "S26", "S24", "S23", "S22",
    "폴드", "Fold", "플립", "Flip", "버즈", "Buds", "워치", "Watch",
    "삼성", "Samsung", "울트라", "Ultra", "원유아이", "One UI",
    "엑시노스", "스냅드래곤",
    "iPhone", "아이폰", "Pixel", "픽셀",
]


# @lat: QuasarzoneCrawler — [[crawler#Platform Strategy]] 참조.
class QuasarzoneCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0

    def __init__(self, platform_code: str = "quasarzone", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        list_posts: List[RawVOC] = []

        # Cloudflare 보호: Referer + UA 필수
        async with self._make_httpx_client() as client:
            client.headers["Referer"] = BASE_URL
            for board_code, board_name in QUASAR_BOARDS:
                for page in range(1, LIST_PAGES + 1):
                    try:
                        posts = await self._fetch_list_page(client, board_code, page)
                        filtered = [p for p in posts if self._is_galaxy_related(p)]
                        list_posts.extend(filtered)
                        logger.info(
                            f"  Quasarzone {board_name} p{page}: {len(filtered)}/{len(posts)}건"
                        )
                        await self._random_delay()
                    except Exception as e:
                        logger.warning(f"  Quasarzone {board_name} p{page} 실패: {e}")

        # 중복 제거 (게시판/페이지 간 동일 글 가능)
        seen: set = set()
        unique: List[RawVOC] = []
        for v in list_posts:
            if v.external_id not in seen:
                seen.add(v.external_id)
                unique.append(v)

        # 최신순 정렬 후 MAX_POSTS 건만 상세 수집
        unique.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        target_posts = unique[:MAX_POSTS]
        logger.info(
            f"Quasarzone 리스트 {len(unique)}건 중 상위 {len(target_posts)}건 상세 수집"
        )

        detailed: List[RawVOC] = []
        async with self._make_httpx_client() as client:
            client.headers["Referer"] = BASE_URL
            for idx, row in enumerate(target_posts, 1):
                try:
                    await self._random_delay()
                    recs = await self._fetch_post_detail(client, row)
                    detailed.extend(recs)
                    logger.info(
                        f"  [{idx}/{len(target_posts)}] {row.source_url} → {len(recs)}건"
                    )
                except Exception as e:
                    logger.warning(f"  Quasarzone 상세 실패 {row.source_url}: {e}")

        # MX 통합 키워드 영구 필터 (Data Clean 4)
        from nlp.mx_keywords import is_mx_relevant
        before_n = len(detailed)
        detailed = [v for v in detailed if is_mx_relevant(v.content)]
        logger.info(f"Quasarzone 수집 완료: {len(detailed)}건 (본문+댓글, mx_filter {before_n}→{len(detailed)})")
        return detailed

    async def _fetch_list_page(
        self, client: httpx.AsyncClient, board_code: str, page: int
    ) -> List[RawVOC]:
        url = LIST_URL.format(base=BASE_URL, board=board_code, page=page)
        resp = await client.get(url)
        resp.raise_for_status()
        return self._parse_list(resp.text, board_code)

    def _parse_list(self, html: str, board_code: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[RawVOC] = []

        # 목록 영역 한정 — 공지/광고 행 회피
        wrap = soup.select_one(".list-board-wrap") or soup
        for tr in wrap.select("table tbody tr"):
            try:
                link_el = tr.select_one("a.subject-link")
                if not link_el:
                    continue
                href = link_el.get("href", "")
                if f"/bbs/{board_code}/views/" not in href:
                    continue

                title = link_el.get_text(strip=True)
                # 제목 끝에 댓글수 '5' 등이 붙어 있을 수 있어 정리
                title = re.sub(r"\s+\d+\s*$", "", title).strip()
                if not title:
                    continue

                post_url = href if href.startswith("http") else f"{BASE_URL}{href}"

                # 작성자: data-nick 우선
                wr_el = tr.select_one(".user-nick-wrap")
                author = None
                if wr_el:
                    author = wr_el.get("data-nick") or wr_el.get_text(strip=True)
                author = (author or "").strip() or "익명"

                # 날짜: 'MM-DD' 또는 'HH:MM' 형식
                dt_el = tr.select_one(".date")
                date_raw = dt_el.get_text(strip=True) if dt_el else ""
                published_at = self._parse_quasar_date(date_raw)

                # 댓글수: .ctn-count
                cc_el = tr.select_one(".ctn-count")
                comment_count = self._to_int(cc_el.get_text(strip=True) if cc_el else "0")

                uid = hashlib.md5(post_url.encode()).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=uid,
                    content=title,
                    source_url=post_url,
                    author_name=author,
                    published_at=published_at,
                    likes_count=0,  # 목록엔 추천수 없음 — 상세 페이지에서도 의미 약함
                    comments_count=comment_count,
                    country_code="KR",
                    meta={"board": board_code},
                ))
            except Exception as e:
                logger.debug(f"Quasarzone 게시물 파싱 실패: {e}")

        return results

    async def _fetch_post_detail(
        self, client: httpx.AsyncClient, row: RawVOC
    ) -> List[RawVOC]:
        """상세 페이지 + 댓글 JSON API → 본문/댓글 RawVOC 리스트"""
        post_url = row.source_url
        board_code = row.meta.get("board") if row.meta else None
        # board_code, write_id 추출 (URL: /bbs/qf_mobile/views/90531)
        m = re.search(r"/bbs/([^/]+)/views/(\d+)", post_url)
        if not m:
            return []
        board_code = board_code or m.group(1)
        write_id = m.group(2)

        resp = await client.get(post_url, headers={"Referer": f"{BASE_URL}/bbs/{board_code}"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 본문 — <textarea id="org_contents"> 안에 HTML-escaped 콘텐츠
        org_el = soup.select_one("textarea#org_contents")
        body_text = ""
        if org_el:
            inner_html = html_lib.unescape(org_el.get_text() or "")
            body_text = BeautifulSoup(inner_html, "html.parser").get_text("\n", strip=True)

        title = row.content  # 목록의 제목

        # 댓글 — JSON API
        comments: list = []
        try:
            comments = await self._fetch_comments(client, board_code, write_id, post_url)
        except Exception as e:
            logger.warning(f"  Quasarzone 댓글 수집 실패 {post_url}: {e}")

        recs: List[RawVOC] = []

        body_uid = hashlib.md5(post_url.encode()).hexdigest()[:16]
        body_content = f"{title}\n{body_text}".strip()
        recs.append(RawVOC(
            external_id=body_uid,
            content=body_content,
            source_url=post_url,
            author_name=row.author_name,
            published_at=row.published_at,
            likes_count=row.likes_count,
            comments_count=len(comments),
            country_code="KR",
        ))

        for cm in comments:
            # 삭제 댓글 스킵
            if cm.get("isDelete") in (1, "1"):
                continue
            raw_html = cm.get("content") or ""
            ctext = BeautifulSoup(html_lib.unescape(raw_html), "html.parser").get_text(
                "\n", strip=True
            )
            if not ctext or len(ctext) < 5:
                continue

            cid = cm.get("id")  # 안정 ID (예: 90532)
            if cid is None:
                continue
            c_uid = hashlib.md5(f"{post_url}#c{cid}".encode()).hexdigest()[:16]

            try:
                clikes = int(cm.get("good") or 0)
            except (TypeError, ValueError):
                clikes = 0

            recs.append(RawVOC(
                external_id=c_uid,
                content=ctext,
                source_url=post_url,
                author_name=cm.get("user_nick") or cm.get("name") or "익명",
                published_at=self._parse_comment_date(cm.get("created_at", "")),
                likes_count=clikes,
                country_code="KR",
            ))

        return recs

    async def _fetch_comments(
        self,
        client: httpx.AsyncClient,
        board_code: str,
        write_id: str,
        post_url: str,
    ) -> list:
        """퀘이사존 댓글 JSON API — 일반/best/cider 모두 합쳐 반환"""
        params = {
            "boardName": board_code,
            "writeId": write_id,
            "comment_id": "",
            "page": "1",
            "order": "",
        }
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": post_url,
        }
        resp = await client.get(
            COMMENT_URL.format(base=BASE_URL, board=board_code),
            params=params,
            headers=headers,
        )
        resp.raise_for_status()
        payload = resp.json()
        cl = payload.get("comm_list") or {}

        merged: list = []
        # cider/best 가 일반 댓글과 중복될 수 있음 — id 로 dedup
        seen_ids: set = set()
        for key in ("cider_comments", "best_comments"):
            arr = cl.get(key) or []
            for cm in arr:
                cid = cm.get("id")
                if cid is not None and cid not in seen_ids:
                    seen_ids.add(cid)
                    merged.append(cm)
        common = (cl.get("comments") or {}).get("data") or []
        for cm in common:
            cid = cm.get("id")
            if cid is not None and cid not in seen_ids:
                seen_ids.add(cid)
                merged.append(cm)
        return merged

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        content_lower = voc.content.lower()
        return any(kw.lower() in content_lower for kw in GALAXY_KEYWORDS)

    @staticmethod
    def _to_int(text: str) -> int:
        try:
            return int(re.sub(r"[^\d]", "", text) or 0)
        except ValueError:
            return 0

    def _parse_quasar_date(self, text: str) -> Optional[datetime]:
        """목록 표기: 'MM-DD' (이전일자) 또는 'HH:MM' (오늘)"""
        text = (text or "").strip()
        if not text:
            return None
        try:
            now = datetime.now(KST)
            if re.match(r"^\d{2}-\d{2}$", text):
                m, d = text.split("-")
                return datetime(
                    year=now.year, month=int(m), day=int(d), tzinfo=KST
                ).astimezone(timezone.utc)
            if re.match(r"^\d{2}:\d{2}$", text):
                h, mi = text.split(":")
                return datetime(
                    year=now.year, month=now.month, day=now.day,
                    hour=int(h), minute=int(mi), tzinfo=KST,
                ).astimezone(timezone.utc)
            if re.match(r"^\d{4}-\d{2}-\d{2}", text):
                return datetime.strptime(text[:10], "%Y-%m-%d").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
        except Exception:
            pass
        return None

    def _parse_comment_date(self, text: str) -> Optional[datetime]:
        """댓글 created_at: '2026-05-28 18:20:26'"""
        text = (text or "").strip()
        try:
            if re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text):
                return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
            if re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text):
                return datetime.strptime(text[:16], "%Y-%m-%d %H:%M").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
        except Exception:
            pass
        return None
