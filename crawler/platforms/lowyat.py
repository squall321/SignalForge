"""
Lowyat.NET 크롤러 — httpx + BeautifulSoup

forum.lowyat.net 은 말레이시아 최대 IT/생활 커뮤니티이며 IPB(Invision Power Board)
엔진 기반이다. Cloudflare 가 앞단에 있지만 정상 브라우저 UA 로 200 OK 응답하므로
RSS 폴백 없이 그대로 크롤 가능.

전략
  - 진입 서브포럼: /Android, /MobilePhonesandTablets (스마트폰 일반)
  - 각 카테고리 페이지에서 토픽 리스트 → Samsung/Galaxy 키워드 필터
  - 토픽 상세는 /topic/{ID}/last (가장 최신 페이지) 수집 → 활성 스레드는 최신 댓글이
    훨씬 정보 밀도가 높음. 첫 페이지(본문 OP)도 함께 fetch.
  - 본문(1st post=OP) + 댓글(나머지 .post1/.post2 블록) 분리
  - 시간대: 말레이시아 표준시 (MYT, UTC+8)
"""
import hashlib
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

BASE_URL = "https://forum.lowyat.net"

# Lowyat 크롤링 대상 서브포럼 — 슬러그 경로 그대로 사용
LOWYAT_BOARDS = [
    ("/Android",                "Android"),
    ("/MobilePhonesandTablets", "Mobile Phones and Tablets"),
]

# 말레이시아 표준시
MYT = timezone(timedelta(hours=8))

# 목록 스캔: 카테고리당 최근 N 페이지
LIST_PAGES = 12
# 상세 수집 최대 토픽 수 (필터 통과한 최신순 상위)
MAX_POSTS = 150
# 토픽 페이지당 게시글 수 (IPB 기본)
PAGE_SIZE = 20

# 갤럭시/삼성 + 경쟁 안드로이드 폰 비교 키워드 (영문/말레이어 혼용 환경)
GALAXY_KEYWORDS = [
    "samsung", "galaxy",
    "s27", "s26", "s25", "s24", "s23", "s22",
    "fold", "flip", "ultra",
    "buds", "watch", "tab s",
    "one ui", "oneui", "exynos",
    # 비교 컨텍스트 — Galaxy 토론에 자주 등장
    "pixel", "iphone", "oneplus", "xiaomi",
]


# @lat: LowyatCrawler — [[crawler#Platform Strategy]] 참조.
class LowyatCrawler(BaseCrawler):
    MIN_DELAY = 1.5
    MAX_DELAY = 3.5

    def __init__(self, platform_code: str = "lowyat", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    async def crawl(self) -> List[RawVOC]:
        candidates: List[RawVOC] = []

        async with self._make_httpx_client() as client:
            # IPB + Cloudflare → 정상 브라우저 헤더 세팅
            client.headers["Accept-Language"] = "en-MY,en;q=0.9,ms;q=0.8"
            client.headers["Accept-Encoding"] = "gzip, deflate"
            client.headers["Referer"] = BASE_URL + "/"

            for board_path, board_name in LOWYAT_BOARDS:
                for page in range(LIST_PAGES):
                    try:
                        topics = await self._fetch_board_page(client, board_path, page)
                        filtered = [t for t in topics if self._is_galaxy_related(t)]
                        candidates.extend(filtered)
                        logger.info(
                            f"  Lowyat {board_name} p{page}: {len(filtered)}/{len(topics)}건"
                        )
                        await self._random_delay()
                    except Exception as e:
                        logger.warning(f"  Lowyat {board_name} p{page} 실패: {e}")

            # 중복 제거 (board 간 동일 토픽 가능)
            seen = set()
            unique: List[RawVOC] = []
            for t in candidates:
                if t.source_url in seen:
                    continue
                seen.add(t.source_url)
                unique.append(t)

            # 최신 활동 순 정렬 (last action time = published_at 으로 채워둠)
            unique.sort(
                key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            target = unique[:MAX_POSTS]
            logger.info(
                f"Lowyat 후보 {len(candidates)} → 고유 {len(unique)} → 상세 {len(target)}건"
            )

            raw_vocs: List[RawVOC] = []
            for topic in target:
                await self._random_delay()
                try:
                    detail = await self._fetch_topic_detail(client, topic)
                    raw_vocs.extend(detail)
                except Exception as e:
                    logger.warning(
                        f"  Lowyat 상세 실패 ({topic.source_url}): {e}"
                    )

        # MX 통합 키워드 영구 필터 (Data Clean 4)
        from nlp.mx_keywords import is_mx_relevant
        before_n = len(raw_vocs)
        raw_vocs = [v for v in raw_vocs if is_mx_relevant(v.content)]
        logger.info(f"Lowyat 수집 완료: {len(raw_vocs)}건 (토픽 {len(target)}건, mx_filter {before_n}→{len(raw_vocs)})")
        return raw_vocs

    # ---------- 목록 ----------
    async def _fetch_board_page(
        self, client: httpx.AsyncClient, board_path: str, page: int
    ) -> List[RawVOC]:
        # IPB slug 경로 페이지네이션: /Slug 가 첫 페이지, /Slug/+25 가 다음 (게시판은 25개 단위)
        # Lowyat 게시판은 페이지당 25 토픽
        if page == 0:
            url = f"{BASE_URL}{board_path}"
        else:
            url = f"{BASE_URL}{board_path}/+{page * 25}"
        resp = await client.get(url)
        resp.raise_for_status()
        return self._parse_board_list(resp.text)

    def _parse_board_list(self, html: str) -> List[RawVOC]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[RawVOC] = []

        for title_td in soup.select("td#forum_topic_title"):
            try:
                row = title_td.find_parent("tr")
                if not row:
                    continue
                link = title_td.select_one("a[href^='/topic/']")
                if not link:
                    continue
                href = link.get("href", "")
                m = re.match(r"^/topic/(\d+)", href)
                if not m:
                    continue
                topic_id = m.group(1)
                title = link.get_text(strip=True)
                if not title:
                    continue

                topic_url = f"{BASE_URL}/topic/{topic_id}"

                # 댓글(=replies) 수
                reply_el = row.select_one("td#forum_topic_replies a")
                try:
                    replies = int(re.sub(r"[^\d]", "", reply_el.get_text(strip=True)) or 0) if reply_el else 0
                except ValueError:
                    replies = 0

                # 토픽 작성자 (starter)
                start_el = row.select_one("td#forum_topic_ts a")
                starter = start_el.get_text(strip=True) if start_el else None

                # last action time → 최신순 정렬 키로 사용
                last_el = row.select_one("td#forum_topic_lastaction .lastaction")
                last_text = ""
                if last_el:
                    # "Today, 10:31 AM\nLast post by:\nkweng84"
                    raw_text = last_el.get_text("\n", strip=True)
                    # 첫 줄 = 날짜
                    last_text = raw_text.split("\n")[0].strip()
                last_dt = self._parse_lowyat_date(last_text)

                uid = hashlib.md5(topic_url.encode()).hexdigest()[:16]

                results.append(RawVOC(
                    external_id=uid,
                    content=title,
                    source_url=topic_url,
                    author_name=starter,
                    published_at=last_dt,
                    likes_count=0,
                    comments_count=replies,
                    country_code="MY",
                    meta={"topic_id": topic_id},
                ))
            except Exception as e:
                logger.debug(f"Lowyat 토픽 행 파싱 실패: {e}")

        return results

    # ---------- 상세 ----------
    async def _fetch_topic_detail(
        self, client: httpx.AsyncClient, topic: RawVOC
    ) -> List[RawVOC]:
        topic_id = topic.meta.get("topic_id")
        # 1) 첫 페이지 — OP(본문) 확보
        first_url = topic.source_url
        resp1 = await client.get(first_url)
        resp1.raise_for_status()
        first_posts = self._parse_topic_page(resp1.text, topic.source_url)
        if not first_posts:
            return []
        op_post = first_posts[0]  # OP = 토픽 첫 게시글
        op_text = op_post["content"]
        op_author = op_post["author"]
        op_date = op_post["date"]
        op_pid = op_post["post_id"]

        # 총 페이지 수 파악 — last_page from multi_page_jump
        last_page = self._extract_last_page(resp1.text)

        # 2) 활성 스레드는 최신 댓글이 더 가치 → last 페이지 추가 fetch
        comment_posts = list(first_posts[1:])  # 첫 페이지의 OP 이후 댓글들
        if last_page and last_page > 1:
            await self._random_delay()
            last_url = f"{BASE_URL}/topic/{topic_id}/last"
            try:
                resp2 = await client.get(last_url)
                resp2.raise_for_status()
                last_posts = self._parse_topic_page(resp2.text, topic.source_url)
                # OP가 last 페이지에도 우연히 포함될 수 있으니 post_id 중복 제거
                seen_ids = {op_pid} | {p["post_id"] for p in comment_posts}
                for p in last_posts:
                    if p["post_id"] in seen_ids:
                        continue
                    seen_ids.add(p["post_id"])
                    comment_posts.append(p)
            except Exception as e:
                logger.debug(f"  Lowyat last page 실패 ({topic_id}): {e}")

        # OP가 본문, 나머지는 댓글
        title = topic.content  # list에서 받은 토픽 제목
        body_voc = RawVOC(
            external_id=hashlib.md5(first_url.encode()).hexdigest()[:16],
            content=f"{title}\n{op_text}".strip(),
            source_url=first_url,
            author_name=op_author,
            published_at=op_date or topic.published_at,
            likes_count=0,
            comments_count=len(comment_posts),
            country_code="MY",
        )

        comment_vocs: List[RawVOC] = []
        for cp in comment_posts:
            text = cp["content"]
            if not text or len(text) < 5:
                continue
            comment_vocs.append(RawVOC(
                external_id=hashlib.md5(
                    f"{first_url}#c{cp['post_id']}".encode()
                ).hexdigest()[:16],
                content=text,
                source_url=first_url,
                author_name=cp["author"],
                published_at=cp["date"],
                likes_count=0,
                country_code="MY",
            ))

        logger.info(
            f"  Lowyat 상세 t{topic_id}: 본문 {len(op_text)}자 + 댓글 {len(comment_vocs)}건"
        )
        return [body_voc] + comment_vocs

    def _parse_topic_page(self, html: str, topic_url: str) -> List[dict]:
        """페이지의 모든 게시글을 dict 리스트로 반환

        각 dict: {post_id, author, date, content}
        """
        soup = BeautifulSoup(html, "html.parser")
        results: List[dict] = []

        # 본문 컨테이너: table[id^=post_]
        for tbl in soup.select("table[id^='post_']"):
            tbl_id = tbl.get("id", "")
            m = re.match(r"post_(\d+)", tbl_id)
            if not m:
                continue
            post_id = m.group(1)

            # 작성자
            name_el = tbl.select_one(".normalname")
            author = name_el.get_text(strip=True) if name_el else None

            # 본문
            body_el = tbl.select_one(".postcolor")
            if not body_el:
                continue
            # 따옴표(quote) 블록은 제거 — 인용 노이즈 줄임
            for q in body_el.select(".quotetop, .quotemain, blockquote"):
                q.decompose()
            for sig in body_el.select(".signature"):
                sig.decompose()
            for trash in body_el.select("script, style"):
                trash.decompose()
            text = body_el.get_text("\n", strip=True)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()

            # 날짜 — .postdetails 들 중 날짜 형식인 것
            date_dt = None
            for d in tbl.select(".postdetails"):
                dtxt = d.get_text(" ", strip=True)
                # "Today, 10:31 AM" / "May 27 2026, 07:48 PM" 등
                parsed = self._parse_lowyat_date(dtxt)
                if parsed:
                    date_dt = parsed
                    break

            results.append({
                "post_id": post_id,
                "author": author,
                "date": date_dt,
                "content": text,
            })

        return results

    def _extract_last_page(self, html: str) -> Optional[int]:
        """multi_page_jump('/topic/ID', last_page, page_size) 에서 last_page 추출"""
        m = re.search(r"multi_page_jump\('[^']+',\s*(\d+),\s*(\d+)\)", html)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
        return None

    # ---------- 필터/유틸 ----------
    def _is_galaxy_related(self, voc: RawVOC) -> bool:
        text = (voc.content or "").lower()
        return any(kw in text for kw in GALAXY_KEYWORDS)

    def _parse_lowyat_date(self, text: str) -> Optional[datetime]:
        """Lowyat 날짜 포맷 → UTC datetime (말레이시아 MYT = UTC+8)

        지원 포맷:
          - "Today, 10:31 AM"
          - "Yesterday, 02:41 PM"
          - "May 27 2026, 07:48 PM"
          - "27 May 2026, 07:48 PM" (일부 IPB 로케일)
        """
        if not text:
            return None
        text = text.strip()
        now_myt = datetime.now(MYT)

        try:
            # Today, HH:MM (AM/PM)
            m = re.match(r"Today,\s*(\d{1,2}):(\d{2})\s*(AM|PM)?", text, re.I)
            if m:
                hour, minute = int(m.group(1)), int(m.group(2))
                ap = (m.group(3) or "").upper()
                if ap == "PM" and hour < 12:
                    hour += 12
                elif ap == "AM" and hour == 12:
                    hour = 0
                dt = now_myt.replace(hour=hour, minute=minute, second=0, microsecond=0)
                return dt.astimezone(timezone.utc)

            # Yesterday, HH:MM (AM/PM)
            m = re.match(r"Yesterday,\s*(\d{1,2}):(\d{2})\s*(AM|PM)?", text, re.I)
            if m:
                hour, minute = int(m.group(1)), int(m.group(2))
                ap = (m.group(3) or "").upper()
                if ap == "PM" and hour < 12:
                    hour += 12
                elif ap == "AM" and hour == 12:
                    hour = 0
                y = now_myt - timedelta(days=1)
                dt = y.replace(hour=hour, minute=minute, second=0, microsecond=0)
                return dt.astimezone(timezone.utc)

            # "May 27 2026, 07:48 PM"
            m = re.match(
                r"([A-Za-z]{3,9})\s+(\d{1,2})\s+(\d{4}),\s*(\d{1,2}):(\d{2})\s*(AM|PM)?",
                text,
            )
            if m:
                month_name, day, year, hour, minute, ap = m.groups()
                hour = int(hour); minute = int(minute)
                if ap and ap.upper() == "PM" and hour < 12:
                    hour += 12
                elif ap and ap.upper() == "AM" and hour == 12:
                    hour = 0
                dt = datetime.strptime(
                    f"{month_name} {day} {year} {hour:02d}:{minute:02d}",
                    "%b %d %Y %H:%M",
                ).replace(tzinfo=MYT)
                return dt.astimezone(timezone.utc)

            # "27 May 2026, 07:48 PM"
            m = re.match(
                r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4}),\s*(\d{1,2}):(\d{2})\s*(AM|PM)?",
                text,
            )
            if m:
                day, month_name, year, hour, minute, ap = m.groups()
                hour = int(hour); minute = int(minute)
                if ap and ap.upper() == "PM" and hour < 12:
                    hour += 12
                elif ap and ap.upper() == "AM" and hour == 12:
                    hour = 0
                dt = datetime.strptime(
                    f"{day} {month_name} {year} {hour:02d}:{minute:02d}",
                    "%d %b %Y %H:%M",
                ).replace(tzinfo=MYT)
                return dt.astimezone(timezone.utc)
        except Exception:
            return None
        return None
