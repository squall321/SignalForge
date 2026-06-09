"""
디시인사이드 크롤러 — httpx + BeautifulSoup
gall.dcinside.com 갤럭시 마이너 갤러리 / 스마트폰 갤러리에서 삼성 Galaxy VOC 수집.
자격증명 불필요 (공개 갤러리, 목록 페이지 파싱).
"""
import hashlib
import html as html_lib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from urllib.parse import urlparse, parse_qs
import logging

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://gall.dcinside.com"

KST = timezone(timedelta(hours=9))

# (목록 경로 prefix, 갤러리 id, 표시명)
#   - 갤럭시 마이너 갤러리 : mgallery/board/lists?id=galaxy
#   - 스마트폰 갤러리       : board/lists?id=smartphone
DC_GALLERIES = [
    ("mgallery/board", "galaxy",     "갤럭시 마이너 갤러리"),
    ("board",          "smartphone", "스마트폰 갤러리"),
]

LIST_URL = "{base}/{prefix}/lists/?id={gid}&page={page}"
COMMENT_URL = "{base}/board/comment/"

def _env_int(name: str, default: int, *, min_value: int = 1) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return max(min_value, v)


# 본문/제목 외 본문 detail 가져온 뒤 추가로 emit 할 최대 게시물 수
MAX_POSTS = _env_int("DCINSIDE_MAX_POSTS", 150)
# 목록 스캔 페이지 수 (1-indexed range 상한 = LIST_PAGES+1)
# BACKFILL_PAGES 환경변수로 옛 글 백카탈로그 수집 시 50~100 까지 확장
LIST_PAGES = _env_int("DCINSIDE_BACKFILL_PAGES", 12)

GALAXY_KEYWORDS = [
    "갤럭시", "Galaxy", "S25", "S26", "S24", "S23", "S22",
    "폴드", "Fold", "플립", "Flip", "버즈", "Buds", "워치", "Watch",
    "삼성", "Samsung", "울트라", "Ultra", "원유아이", "One UI",
    "엑시노스", "스냅드래곤",
    "iPhone", "아이폰", "Pixel", "픽셀",
]


# @lat: DCInsideCrawler — [[crawler#Platform Strategy]] 참조.
class DCInsideCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.5

    def __init__(self, platform_code: str = "dcinside", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        raw_vocs: List[RawVOC] = []

        # 디시는 Referer 없는 요청을 차단하는 경우가 있어 헤더 보강
        async with self._make_httpx_client() as client:
            client.headers["Referer"] = BASE_URL
            for prefix, gid, name in DC_GALLERIES:
                for page in range(1, LIST_PAGES + 1):  # 최근 N페이지
                    try:
                        posts = await self._fetch_list_page(client, prefix, gid, page)
                        filtered = [p for p in posts if self._is_galaxy_related(p)]
                        raw_vocs.extend(filtered)
                        logger.info(f"  DCInside {name} p{page}: {len(filtered)}/{len(posts)}건")
                        await self._random_delay()
                    except Exception as e:
                        logger.warning(f"  DCInside {name} p{page} 실패: {e}")

        # 중복 제거 (같은 글이 갤러리/페이지 간 중복될 수 있음)
        seen: set = set()
        unique: List[RawVOC] = []
        for v in raw_vocs:
            if v.external_id not in seen:
                seen.add(v.external_id)
                unique.append(v)

        # 최근 MAX_POSTS 건으로 제한 후, 각 글의 본문 + 댓글 수집
        targets = unique[:MAX_POSTS]
        logger.info(
            f"DCInside 목록 수집 완료: {len(unique)}건 (중복 제거) → 본문/댓글 대상 {len(targets)}건"
        )

        detailed: List[RawVOC] = []
        async with self._make_httpx_client() as client:
            client.headers["Referer"] = BASE_URL
            for idx, row in enumerate(targets, 1):
                try:
                    await self._random_delay()
                    recs = await self._fetch_post_detail(client, row)
                    detailed.extend(recs)
                    logger.info(
                        f"  [{idx}/{len(targets)}] {row.source_url} → {len(recs)}건"
                    )
                except Exception as e:
                    logger.warning(f"  DCInside 게시물 처리 실패 {row.source_url}: {e}")

        # 2026-06-08 C3: MX 통합 키워드 필터 강제 (Samsung+경쟁사+일반 폰)
        from nlp.mx_keywords import is_mx_relevant
        before = len(detailed)
        detailed = [v for v in detailed if is_mx_relevant(v.content)]
        logger.info(f"DCInside 수집 완료: {len(detailed)}/{before}건 (MX 필터 적용)")
        return detailed

    @staticmethod
    def _parse_post_url(post_url: str):
        """post_url 에서 (gallery_id, post_no, prefix) 추출"""
        q = parse_qs(urlparse(post_url).query)
        gid = (q.get("id") or [""])[0]
        no = (q.get("no") or [""])[0]
        is_mgallery = "/mgallery/" in post_url
        return gid, no, is_mgallery

    @staticmethod
    def _strip_html(memo: str) -> str:
        """댓글 memo HTML → 순수 텍스트 (디시콘 이미지/태그 제거)"""
        soup = BeautifulSoup(memo or "", "html.parser")
        text = soup.get_text(" ", strip=True)
        return html_lib.unescape(text).strip()

    @staticmethod
    def _is_dccon_only(memo: str) -> bool:
        """디시콘/이미지만 있고 텍스트가 없는 댓글 판별"""
        return ("written_dccon" in (memo or "") or "<img" in (memo or "")) and not (
            BeautifulSoup(memo or "", "html.parser").get_text(strip=True)
        )

    def _parse_comment_date(self, text: str) -> Optional[datetime]:
        """'2024.05.11 16:35:15' 또는 '05.17 11:22:35' 형식 파싱"""
        text = (text or "").strip()
        try:
            if re.match(r"\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}", text):
                return datetime.strptime(text[:19], "%Y.%m.%d %H:%M:%S").replace(
                    tzinfo=KST
                ).astimezone(timezone.utc)
            if re.match(r"\d{2}\.\d{2} \d{2}:\d{2}:\d{2}", text):
                now = datetime.now(KST)
                return datetime.strptime(text[:14], "%m.%d %H:%M:%S").replace(
                    year=now.year, tzinfo=KST
                ).astimezone(timezone.utc)
        except Exception:
            pass
        return None

    async def _fetch_post_detail(
        self, client: httpx.AsyncClient, row: RawVOC
    ) -> List[RawVOC]:
        """상세 페이지(본문 + e_s_n_o) → 댓글 AJAX → 본문/댓글 RawVOC 리스트"""
        post_url = row.source_url
        gid, no, is_mgallery = self._parse_post_url(post_url)

        resp = await client.get(post_url, headers={"Referer": BASE_URL})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 본문 추출
        body_el = soup.select_one(".write_div") or soup.select_one(".writing_view_box")
        body_text = body_el.get_text("\n", strip=True) if body_el else ""
        # 페이지네이션 UI 텍스트 제거 ("1 / 20 이전 다음" 등)
        body_text = re.sub(r"\s*\d+\s*/\s*\d+\s+이전\s+다음\s*", " ", body_text).strip()
        # 푸터 정리 ("- dc App")
        body_text = re.sub(r"\s*-\s*dc\s*App\s*$", "", body_text, flags=re.IGNORECASE).strip()

        title = row.content  # 목록에서 가져온 제목
        recs: List[RawVOC] = []

        # 댓글 토큰 추출 (상세 페이지 hidden input)
        esno_el = soup.select_one("input[name=e_s_n_o]")
        galltype_el = soup.select_one("input[name=_GALLTYPE_]")
        e_s_n_o = esno_el.get("value", "") if esno_el else ""
        # mgallery → M, 일반 갤러리 → G (상세 페이지 hidden 우선)
        galltype = (
            galltype_el.get("value", "")
            if galltype_el
            else ("M" if is_mgallery else "G")
        )

        comments: list = []
        try:
            comments = await self._fetch_comments(
                client, post_url, gid, no, e_s_n_o, galltype
            )
        except Exception as e:
            logger.warning(f"  댓글 수집 실패 {post_url}: {e}")

        # 본문 레코드
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

        # 댓글 레코드
        for i, cm in enumerate(comments, 1):
            memo = cm.get("memo", "")
            # 삭제/대댓글헤더/디시콘 전용 스킵
            if cm.get("is_delete") not in ("0", 0, None, "N"):
                continue
            if not memo or self._is_dccon_only(memo):
                continue
            text = self._strip_html(memo)
            if len(text) < 5:
                continue
            # 안정적 댓글 ID(JSON 'no') 우선 — 주기 재크롤 시 중복 방지. 없으면 순번 fallback.
            cno = cm.get("no") or f"i{i}"
            c_uid = hashlib.md5(f"{post_url}#c{cno}".encode()).hexdigest()[:16]
            recs.append(RawVOC(
                external_id=c_uid,
                content=text,
                source_url=post_url,
                author_name=cm.get("name") or "ㅇㅇ",
                published_at=self._parse_comment_date(cm.get("reg_date", "")),
                likes_count=0,
                country_code="KR",
            ))

        return recs

    async def _fetch_comments(
        self,
        client: httpx.AsyncClient,
        post_url: str,
        gid: str,
        no: str,
        e_s_n_o: str,
        galltype: str,
    ) -> list:
        """디시 댓글 AJAX 엔드포인트 호출 → comments 리스트 반환"""
        data = {
            "id": gid,
            "no": no,
            "cmt_id": gid,
            "cmt_no": no,
            "e_s_n_o": e_s_n_o,
            "comment_page": "1",
            "_GALLTYPE_": galltype,
            "sort": "",
            "prevCnt": "0",
        }
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": post_url,  # Referer 필수 (없으면 403)
            "Origin": BASE_URL,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        resp = await client.post(
            COMMENT_URL.format(base=BASE_URL), data=data, headers=headers
        )
        resp.raise_for_status()
        payload = json.loads(resp.text)
        return payload.get("comments") or []

    async def _fetch_list_page(
        self, client: httpx.AsyncClient, prefix: str, gid: str, page: int
    ) -> List[RawVOC]:
        url = LIST_URL.format(base=BASE_URL, prefix=prefix, gid=gid, page=page)
        resp = await client.get(url)
        resp.raise_for_status()
        return self._parse_list(resp.text)

    def _parse_list(self, html: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[RawVOC] = []

        for tr in soup.select("tr.ub-content"):
            try:
                # 광고/AD/이벤트 행: gall_num 이 숫자가 아님 ('-')
                num_el = tr.select_one(".gall_num")
                num_text = num_el.get_text(strip=True) if num_el else ""
                if not num_text.isdigit():
                    continue

                # 제목 + 링크 (.gall_tit 의 첫 a — 두 번째 a는 댓글수 링크)
                tit_el = tr.select_one(".gall_tit a")
                if not tit_el:
                    continue
                href = tit_el.get("href", "")
                if "/board/view/" not in href:
                    continue
                # 제목 텍스트 (말머리 span 제외하고 순수 텍스트)
                title = tit_el.get_text(strip=True)
                # 댓글수 표기 '[3]' 가 제목 끝에 붙으면 제거
                title = re.sub(r"\[\d+\]$", "", title).strip()
                if not title:
                    continue

                post_url = href if href.startswith("http") else f"{BASE_URL}{href}"

                # 작성자: data-nick 우선
                wr_el = tr.select_one(".gall_writer")
                author = None
                if wr_el:
                    author = wr_el.get("data-nick") or wr_el.get_text(strip=True)
                author = author or "ㅇㅇ"

                # 날짜: title 속성에 풀 datetime, 없으면 텍스트(YY/MM/DD)
                dt_el = tr.select_one(".gall_date")
                date_raw = ""
                if dt_el:
                    date_raw = dt_el.get("title") or dt_el.get_text(strip=True)
                published_at = self._parse_dc_date(date_raw)

                # 추천수
                rec_el = tr.select_one(".gall_recommend")
                like_count = self._to_int(rec_el.get_text(strip=True) if rec_el else "0")

                # 댓글수: .reply_num '[3]'
                reply_el = tr.select_one(".gall_tit .reply_num")
                comment_count = 0
                if reply_el:
                    m = re.search(r"\d+", reply_el.get_text(strip=True))
                    comment_count = int(m.group()) if m else 0

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
                logger.debug(f"DCInside 게시물 파싱 실패: {e}")

        return results

    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        content_lower = voc.content.lower()
        return any(kw.lower() in content_lower for kw in GALAXY_KEYWORDS)

    @staticmethod
    def _to_int(text: str) -> int:
        try:
            return int(re.sub(r"[^\d]", "", text) or 0)
        except ValueError:
            return 0

    def _parse_dc_date(self, text: str):
        """'2026-05-16 13:02:07' (title 속성) 또는 '26/05/11', '13:02' 파싱"""
        text = (text or "").strip()
        try:
            if re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text):
                return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST).astimezone(timezone.utc)
            if re.match(r"\d{2}/\d{2}/\d{2}", text):
                return datetime.strptime(text[:8], "%y/%m/%d").replace(tzinfo=KST).astimezone(timezone.utc)
            if re.match(r"\d{2}:\d{2}", text):
                now = datetime.now(KST)
                return datetime.strptime(text[:5], "%H:%M").replace(
                    year=now.year, month=now.month, day=now.day, tzinfo=KST
                ).astimezone(timezone.utc)
        except Exception:
            pass
        return None
