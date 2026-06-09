"""
Hardware.fr — forum.hardware.fr Galaxy/Samsung 스레드 크롤러
(Harvest 3 트랙 B 신규)

전략
  - forum.hardware.fr 은 프랑스 IT 커뮤니티 1세대 포럼.  WordPress/RSS 없음 — 옛
    PHP 게시판 HTML 만 제공.  카테고리 listing 페이지에서 Samsung/Galaxy 가
    포함된 thread 제목만 필터한 뒤 마지막 페이지에서 최신 글을 채집한다.
  - 1차 카테고리: /hfr/gsmgpspda/telephone-android/liste_sujet-N.htm
    - Galaxy S, Fold, Flip, Buds 류 토픽이 다수.
  - thread URL 패턴: .../telephone-android/<slug>-sujet_<TOPIC_ID>_<PAGE>.htm
  - 한 게시글 = 한 VOC (post id = para<MSG_ID>).
    - 타임스탬프: <div class="toolbar">Posté le DD-MM-YYYY à HH:MM:SS — naive
      → CEST(UTC+2, 6월 기준) 가정 후 UTC 변환.
    - 작성자: 직전 messCase1 셀의 <b class="s2">USERNAME</b>.
    - 본문: messCase2 안의 <div id="para...">…</div> (인용/quote 영역 제거).
  - 키워드 필터: 제목 또는 본문에 samsung/galaxy/fold/flip/buds 등.

봇 패턴 우회
  - BaseCrawler.fetch_with_rotated_ua() 로 매 요청 UA + Accept-Language 회전
    (clien/fmkorea Harvest 3 트랙 A 와 동일 패턴).
  - MIN_DELAY 2.0 / MAX_DELAY 4.0 (프랑스 포럼 — 트래픽 낮음, 보수적으로).
"""
import hashlib
import html as html_lib
import logging
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base.crawler import BaseCrawler, RawVOC

logger = logging.getLogger(__name__)

BASE_URL = "https://forum.hardware.fr"

# 1차 카테고리 (텔레폰 Android — Samsung 게시글이 가장 많음).
# Harvest 3 P3 — 모바일/태블릿/액세서리 외에 일반 GSMGPSPDA 트리·기타
# Samsung 가전 토론이 묶이는 카테고리(스마트홈/montres)도 포함.
CATEGORY_PATHS = [
    "/hfr/gsmgpspda/telephone-android/",
    "/hfr/gsmgpspda/tablette/",         # Galaxy Tab
    "/hfr/gsmgpspda/accessoires/",      # Buds/Watch (기본)
    "/hfr/gsmgpspda/accessoires-smartphones/",  # Buds/Watch (대체 URL)
    "/hfr/gsmgpspda/montres-connectees/",       # Galaxy Watch 전용
    "/hfr/gsmgpspda/general-mobilite/",         # GSM 일반 잡담 — TU 다수
    # Harvest 4 H6 — Smartphones/Tablets 추가 보드
    # 실측: android p1 galaxy_hits=8, telephone p1 galaxy_hits=33.
    # GPS-PDA 는 galaxy_hits=0 으로 제외.
    "/hfr/gsmgpspda/android/",                  # Android 일반 (Samsung 토픽 다수)
    "/hfr/gsmgpspda/telephone/",                # 전화기 일반 — Samsung 비중 ↑
]

# 카테고리당 listing 페이지 수.
# Harvest 3 P3 — BACKFILL_PAGES env 로 override (기본 5, 트랙 요구).
LIST_PAGES = int(os.getenv("HARDWARE_FR_BACKFILL_PAGES", "5"))
# 채집할 thread 최대 수 (카테고리 통합) — 확장에 맞춰 24 → 더 많은 TU 포착.
MAX_THREADS = int(os.getenv("HARDWARE_FR_MAX_THREADS", "24"))
# thread 당 fetch 할 페이지 수 — 최신부터 거꾸로 N 페이지.
THREAD_PAGES_PER = int(os.getenv("HARDWARE_FR_THREAD_PAGES", "1"))
# 최종 VOC 상한.
MAX_POSTS = int(os.getenv("HARDWARE_FR_MAX_POSTS", "500"))

# CEST = UTC+2 (6월 — DST 적용중)
CEST = timezone(timedelta(hours=2))

# 스레드 URL: .../<slug>-sujet_<TOPIC_ID>_<PAGE>.htm
THREAD_LINK_RE = re.compile(
    r'href="(/hfr/[^"]+sujet_(\d+)_(\d+)\.htm)"'
)

# 작성자: <b class="s2">NAME</b>
AUTHOR_RE = re.compile(r'<b class="s2">([^<]+)</b>')

# 'Posté le DD-MM-YYYY à HH:MM:SS' (entity 형태 '&nbsp;à&nbsp;' = 13자 가 끼어듦)
POSTED_RE = re.compile(
    r'Post[ée]\s*le\s*([0-9]{2})-([0-9]{2})-([0-9]{4})'
    r'(?:[^0-9]{0,30})([0-9]{2}):([0-9]{2}):([0-9]{2})'
)

# 메시지 셀 분리 — messCase1 (헤더: 작성자) / messCase2 (본문 + toolbar 날짜)
MESS_CELL_RE = re.compile(
    r'<td class="messCase([12])"[^>]*>(.*?)</td>',
    re.DOTALL,
)

# 본문 컨테이너 — <div id="paraNNN">…</div>.  종료 div 매칭이 어려워(중첩 div
# 포함) — 단순히 다음 메시지 셀 직전까지 잡는 lazy 매칭으로 충분.
PARA_RE = re.compile(r'<div id="para(\d+)">(.*)', re.DOTALL)

# 인용 영역 (quote/edit/notification 류) 제거용
QUOTE_BLOCK_RE = re.compile(
    r'<div class="(?:citation|edited)"[^>]*>.*?</div>',
    re.DOTALL | re.IGNORECASE,
)

# <title>...</title>
TITLE_RE = re.compile(r"<title>([^<]+)</title>", re.IGNORECASE)

GALAXY_KEYWORDS = (
    "galaxy", "samsung",
    "s27", "s26", "s25", "s24", "s23", "s22",
    "fold", "flip",
    "buds", "watch", "tab", "ring",
    "exynos", "one ui", "oneui", "bixby",
    # Harvest 3 P3 — 프랑스어 표기/일반 명사 보강
    "tablette samsung", "tab s",
    "z fold", "z flip",
)


class HardwareFRCrawler(BaseCrawler):
    """forum.hardware.fr 의 Samsung/Galaxy thread post 크롤러."""

    MIN_DELAY = 2.0
    MAX_DELAY = 4.0

    def __init__(self, platform_code: str = "hardware_fr", **kwargs):
        super().__init__(platform_code=platform_code, **kwargs)

    # ── 메인 진입점 ────────────────────────────────────────────────────────
    async def crawl(self) -> List[RawVOC]:
        items: List[RawVOC] = []
        seen_post_ids: set = set()
        threads: List[Tuple[str, str, str]] = []  # (url, topic_id, last_page)

        async with self._make_httpx_client() as client:
            # 1) 각 카테고리 listing → Samsung/Galaxy 스레드 후보 수집
            for cat in CATEGORY_PATHS:
                for page in range(1, LIST_PAGES + 1):
                    url = f"{BASE_URL}{cat}liste_sujet-{page}.htm"
                    html = await self._get_html(client, url, referer=BASE_URL + "/")
                    if not html:
                        continue
                    found = self._extract_galaxy_threads(html)
                    threads.extend(found)
                    logger.info(
                        f"  HardwareFR list {cat}p{page}: 후보 {len(found)}"
                    )
                    await self._random_delay()

            # 중복 thread 제거 (topic_id 기준, 마지막 page 최댓값 유지)
            best: dict = {}
            for url, topic_id, last_page in threads:
                lp = int(last_page) if last_page.isdigit() else 1
                if topic_id not in best or lp > int(best[topic_id][2]):
                    best[topic_id] = (url, topic_id, str(lp))
            uniq = list(best.values())[:MAX_THREADS]
            logger.info(f"  HardwareFR: {len(uniq)} 스레드 채집 예정")

            # 2) 각 thread 의 마지막 페이지 → 게시글 파싱
            for thread_url, topic_id, last_page_str in uniq:
                last_page = int(last_page_str) if last_page_str.isdigit() else 1
                pages_to_fetch = list(
                    range(max(1, last_page - THREAD_PAGES_PER + 1), last_page + 1)
                )
                for p in pages_to_fetch:
                    page_url = self._thread_page_url(thread_url, p)
                    html = await self._get_html(
                        client, page_url, referer=BASE_URL + "/"
                    )
                    if not html:
                        continue
                    title = self._extract_title(html)
                    posts = self._parse_thread_page(
                        html, page_url=page_url, topic_id=topic_id, title=title
                    )
                    for post in posts:
                        if post.external_id in seen_post_ids:
                            continue
                        seen_post_ids.add(post.external_id)
                        if self._is_galaxy_related(post, title):
                            items.append(post)
                    logger.info(
                        f"  HardwareFR thread {topic_id} p{p}: "
                        f"{len(posts)} post → filtered 누적 {len(items)}"
                    )
                    await self._random_delay()
                    if len(items) >= MAX_POSTS:
                        break
                if len(items) >= MAX_POSTS:
                    break

        items.sort(
            key=lambda p: p.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = items[:MAX_POSTS]
        logger.info(f"HardwareFR 수집 완료: {len(result)}건")
        return result

    # ── 단위 1: 카테고리 listing → Galaxy thread URL ─────────────────────────
    @staticmethod
    def _extract_galaxy_threads(list_html: str) -> List[Tuple[str, str, str]]:
        """카테고리 listing HTML → [(thread_url, topic_id, last_page), ...]
        thread title 텍스트에 Samsung/Galaxy 가 들어가는 항목만.

        listing 페이지의 행 단위(<tr> … </tr>)로 끊어 본 다음, 그 행 안에 sujet
        링크가 존재하면 같은 행의 텍스트에서 키워드를 확인.  (이전 구현은 ±300자
        고정 윈도우로 인접 행의 키워드가 끼어들어 오탐 발생.)"""
        per_topic: dict = {}
        # <tr 부터 </tr> 까지를 하나의 행으로 본다 (fallback: 전체).
        rows = re.findall(r"<tr[\s>].*?</tr>", list_html, re.DOTALL | re.IGNORECASE)
        if not rows:
            rows = [list_html]
        for row in rows:
            row_lower = row.lower()
            if not any(kw in row_lower for kw in GALAXY_KEYWORDS):
                continue
            for m in THREAD_LINK_RE.finditer(row):
                path, topic_id, page = m.group(1), m.group(2), m.group(3)
                full_url = BASE_URL + path
                cur_page = int(page) if page.isdigit() else 1
                prior = per_topic.get(topic_id)
                if not prior or cur_page > int(prior[2]):
                    per_topic[topic_id] = (full_url, topic_id, str(cur_page))
        return list(per_topic.values())

    # ── 단위 2: thread URL 의 page 부분 교체 ─────────────────────────────────
    @staticmethod
    def _thread_page_url(thread_url: str, page: int) -> str:
        # 끝의 _N.htm 을 _<page>.htm 으로 치환
        return re.sub(r"_(\d+)\.htm$", f"_{page}.htm", thread_url)

    # ── 단위 3: thread page HTML → 게시글 목록 ───────────────────────────────
    def _parse_thread_page(
        self,
        html: str,
        page_url: str,
        topic_id: str,
        title: Optional[str] = None,
    ) -> List[RawVOC]:
        """messCase1 (author) + messCase2 (body) 쌍을 묶어 RawVOC 생성."""
        cells = MESS_CELL_RE.findall(html)
        # cells: [('1', html1), ('2', html2), ('1', html3), ('2', html4), ...]
        author: Optional[str] = None
        result: List[RawVOC] = []
        for kind, body_html in cells:
            if kind == "1":
                m = AUTHOR_RE.search(body_html)
                author = (m.group(1).strip() if m else None) or None
                continue
            # kind == "2" → 본문 셀
            para_match = PARA_RE.search(body_html)
            if not para_match:
                continue
            msg_id = para_match.group(1)
            inner = para_match.group(2)
            inner_no_quote = QUOTE_BLOCK_RE.sub(" ", inner)
            body_text = self._strip_html(inner_no_quote)
            if not body_text or len(body_text) < 20:
                continue

            published_at = self._parse_posted_date(body_html)
            full_content = (title + "\n" + body_text) if title else body_text
            if len(full_content) > 4000:
                full_content = full_content[:4000]

            external_id = hashlib.md5(
                f"hwfr#{topic_id}#{msg_id}".encode()
            ).hexdigest()[:16]

            result.append(RawVOC(
                external_id=external_id,
                content=full_content,
                source_url=f"{page_url}#t{msg_id}",
                author_name=author,
                published_at=published_at,
                comments_count=0,
                country_code="FR",
                meta={
                    "topic_id": topic_id,
                    "msg_id": msg_id,
                    "title": title or "",
                    "source": "forum_html",
                },
            ))
        return result

    # ── 보조: HTTP GET + UA rotation + redirect graceful ──────────────────────
    async def _get_html(
        self, client: httpx.AsyncClient, url: str, referer: Optional[str] = None
    ) -> Optional[str]:
        extra = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        if referer:
            extra["Referer"] = referer
        # 회전 UA + Accept-Language (en/fr 다양화)
        client.headers["Accept-Language"] = "fr-FR,fr;q=0.9,en-US;q=0.7,en;q=0.6"
        try:
            resp = await self.fetch_with_rotated_ua(
                client, url, extra_headers=extra,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"HardwareFR GET 예외 {url}: {e}")
            return None
        if resp is None or resp.status_code != 200:
            logger.debug(
                f"HardwareFR GET {url} → "
                f"HTTP {getattr(resp, 'status_code', 'None')}"
            )
            return None
        # charset: ISO-8859-1 응답이 자주 옴 — httpx 가 헤더 charset 으로 디코드
        return resp.text

    # ── 보조: 파싱 헬퍼 ───────────────────────────────────────────────────────
    @staticmethod
    def _extract_title(html: str) -> Optional[str]:
        m = TITLE_RE.search(html)
        if not m:
            return None
        title = html_lib.unescape(m.group(1)).strip()
        # ' - Page : N - <카테고리> - FORUM HardWare.fr' 류 후행 제거
        title = re.sub(r"\s*-\s*Page\s*:\s*\d+.*$", "", title)
        title = re.sub(r"\s*-\s*FORUM HardWare\.fr.*$", "", title, flags=re.I)
        return title.strip() or None

    @staticmethod
    def _strip_html(s: str) -> str:
        if not s:
            return ""
        decoded = html_lib.unescape(s)
        decoded = re.sub(
            r"<(script|style)[^>]*>.*?</\1>", " ",
            decoded, flags=re.DOTALL | re.IGNORECASE,
        )
        # <br> → 공백
        decoded = re.sub(r"<br\s*/?>", " ", decoded, flags=re.IGNORECASE)
        no_tags = re.sub(r"<[^>]+>", " ", decoded)
        no_tags = re.sub(r"\s+", " ", no_tags).strip()
        return no_tags

    @staticmethod
    def _parse_posted_date(body_html: str) -> Optional[datetime]:
        """'Posté le DD-MM-YYYY à HH:MM:SS' (CEST naive) → UTC."""
        m = POSTED_RE.search(body_html)
        if not m:
            return None
        try:
            d, mo, y, hh, mm, ss = [int(x) for x in m.groups()]
            dt = datetime(y, mo, d, hh, mm, ss, tzinfo=CEST)
            return dt.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_galaxy_related(voc: RawVOC, thread_title: Optional[str]) -> bool:
        """본문 + 스레드 제목 합쳐 한 번이라도 키워드 매치되면 통과."""
        haystack = (voc.content or "").lower()
        if thread_title:
            haystack = (thread_title.lower() + " " + haystack)
        if not haystack.strip():
            return False
        return any(kw in haystack for kw in GALAXY_KEYWORDS)
