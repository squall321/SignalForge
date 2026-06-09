"""R22 트랙 D — Crisis 직접 매칭 (멀티 platform).

배경
====
R21 까지 9to5Google 단일 platform 으로 GN7 218/GZF1 107/GZFL3 8/GS22U 2/GS20 38
확보. 영문 매체 다양화를 위해 본 스크립트는 다음 4 platform 을 모두 지원한다:

  * ``9to5google``     — site search ``?s=<kw>``, URL 날짜 ``/YYYY/MM/DD/slug/``
  * ``engadget``       — legacy-sitemap{44..50}.xml, URL 날짜 ``/YYYY-MM-DD-slug.html``
  * ``theverge``       — ``/sitemaps/entries/YYYY/M``, URL 날짜 ``/YYYY/M/D/...``
  * ``androidcentral`` — ``/sitemap-YYYY-MM.xml``, 날짜는 ``<lastmod>`` 기반

각 platform 별로 (search/sitemap → URL+date) → 윈도우 필터 → fetch → save 의
공통 파이프라인을 사용하지만, 검색·파서·날짜 추출은 platform 어댑터로 분리.

안전 장치
=========
- DRY_RUN=1 (기본): 검색/sitemap 까지만, fetch + save 스킵
- PRESERVE_EXISTING=1 (기본): BaseCrawler.save 는 ON CONFLICT DO NOTHING 으로
  기존 voc external_id 자동 보존
- 실행 1회당 ``reports/backfill_audit.jsonl`` 1줄 append

환경변수
========
DATABASE_URL                  필수 (DRY_RUN=1 일 때도 NLP pipeline 위해 필요)
CPD_DRY_RUN                   기본 '1' — search 까지만, fetch/save 스킵
CPD_PRESERVE_EXISTING         기본 '1' — 기존 voc 보존 (Save 의 ON CONFLICT 활용)
CPD_PER_KEYWORD_MAX           기본 5   — keyword 1개당 가져올 article 상한
CPD_MAX_PAGES                 기본 2   — search 페이지 개수 (9to5G 전용)
CPD_PLATFORM                  기본 '9to5google'
                              — 9to5google | engadget | theverge | androidcentral
                              — 'all' 입력시 모든 platform 순차 실행

검증
====
- crawler/tests/test_crisis_direct.py — 9to5G URL 날짜 + 윈도우 단위
- crawler/tests/test_crisis_multi.py  — 4 platform 어댑터 단위
- 실행 후 psql 로 voc 사이트 분포 변화 측정

산출
====
- 각 platform 별 신규 voc + Crisis 5건 사이트 분포 변화
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, date
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC  # noqa: E402
from insight.backfill_audit import record_run  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("crisis_direct")


# ─────────────────────────── 환경 ───────────────────────────
DRY_RUN = os.getenv("CPD_DRY_RUN", "1") == "1"
PRESERVE_EXISTING = os.getenv("CPD_PRESERVE_EXISTING", "1") == "1"
PER_KEYWORD_MAX = int(os.getenv("CPD_PER_KEYWORD_MAX", "5"))
MAX_PAGES = int(os.getenv("CPD_MAX_PAGES", "2"))
PLATFORM_ARG = os.getenv("CPD_PLATFORM", "9to5google").strip().lower()

SAFARI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
)

# ─────────────────────────── crisis window + 키워드 ───────────────────────────
# (start_date, end_date) UTC 기준 — 기존 crisis_kr/v2/platform_backfill 과 동일
CRISIS_WINDOWS: Dict[str, Tuple[date, date]] = {
    "GN7":   (date(2016, 8, 19), date(2016, 12, 31)),
    "GZF1":  (date(2019, 4, 15), date(2019, 12, 31)),
    "GS22U": (date(2022, 2, 25), date(2022, 6, 30)),
    "GZFL3": (date(2021, 8, 1),  date(2022, 3, 31)),
    "GS20":  (date(2020, 2, 1),  date(2020, 12, 31)),
}

# 영문 매체용 키워드 (9to5G 와 호환 — 다른 platform 도 동일 풀 사용)
CRISIS_KEYWORDS: Dict[str, List[str]] = {
    "GN7":   ["Note 7 fire", "Note 7 recall", "Galaxy Note 7 explosion"],
    "GZF1":  ["Galaxy Fold review delay", "Galaxy Fold screen broken",
              "Fold display lift"],
    "GS22U": ["Galaxy S22 GOS", "Game Optimizing Service throttling",
              "Samsung GoS lawsuit"],
    "GZFL3": ["Z Flip 3 hinge", "Z Flip 3 broken screen", "Z Flip 3 durability"],
    "GS20":  ["Galaxy S20 5G price", "Galaxy S20 launch", "Galaxy S20 review"],
}


def _kw_pattern(kws: List[str]) -> re.Pattern:
    """키워드 리스트 → URL slug 매칭용 정규식.

    슬러그 (예: ``galaxy-note-7-explosion``) 가 ``-`` 로 구분되므로 키워드 안의
    공백을 단어 사이 구분자(공백/하이픈/언더바/슬래시)로 흡수.
    """
    parts = []
    for kw in kws:
        parts.append(r"\b" + r"[\s\-_/]+".join(re.escape(t) for t in kw.split()) + r"\b")
    return re.compile("|".join(parts), re.IGNORECASE)


# 위기 별 *핵심 단일 토큰* — 슬러그 매칭 보강용. 정확도 위해 모델/사건명만 사용.
# 예: 'note-7' 단일로 GN7 매칭 (recalls/explosion/fire/lawsuit 등 동사변형 흡수)
CRISIS_TOKENS: Dict[str, List[str]] = {
    "GN7":   ["note 7", "note7", "galaxy note 7"],
    "GZF1":  ["galaxy fold", "galaxy-fold"],
    "GS22U": ["galaxy s22", "gos throttling", "samsung gos", "game optimizing"],
    "GZFL3": ["z flip 3", "z-flip-3", "galaxy z flip 3"],
    "GS20":  ["galaxy s20", "s20 5g"],
}


def _token_pattern(toks: List[str]) -> re.Pattern:
    parts = []
    for t in toks:
        parts.append(r"\b" + r"[\s\-_/]+".join(re.escape(x) for x in t.split()) + r"\b")
    return re.compile("|".join(parts), re.IGNORECASE)


CRISIS_KW_PATTERNS: Dict[str, re.Pattern] = {
    code: _token_pattern(CRISIS_TOKENS[code] + CRISIS_KEYWORDS[code])
    for code in CRISIS_KEYWORDS
}


# ═════════════════════════════════════════════════════════════════════════
# 공통 유틸
# ═════════════════════════════════════════════════════════════════════════
def _in_window(d: date, code: str) -> bool:
    s, e = CRISIS_WINDOWS[code]
    return s <= d <= e


def _truncate_body(body: str, limit: int = 4000) -> str:
    return body if len(body) <= limit else body[:limit]


# ═════════════════════════════════════════════════════════════════════════
# Platform 1: 9to5Google  — site search + URL 날짜
# ═════════════════════════════════════════════════════════════════════════
_NINE_BASE = "https://9to5google.com"
_NINE_URL_DATE_RE = re.compile(
    r"^https://9to5google\.com/(\d{4})/(\d{2})/(\d{2})/[^/]+/?$"
)


def _extract_date(url: str) -> Optional[date]:
    """레거시 호환 — 9to5G URL 날짜 추출.

    기존 ``test_crisis_direct.py`` 가 이 이름을 import 하므로 유지.
    """
    m = _NINE_URL_DATE_RE.match(url.strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


async def _nine_search_urls(
    client: httpx.AsyncClient, crisis_code: str
) -> List[Tuple[str, date, str]]:
    """9to5G ?s=<keyword> 다중 페이지 dedup → (url, date, kw)."""
    seen: set = set()
    out: List[Tuple[str, date, str]] = []
    for kw in CRISIS_KEYWORDS[crisis_code]:
        q = quote_plus(kw)
        for page in range(1, MAX_PAGES + 1):
            url = f"{_NINE_BASE}/?s={q}" if page == 1 else f"{_NINE_BASE}/page/{page}/?s={q}"
            try:
                r = await client.get(url, timeout=20.0)
                if r.status_code != 200:
                    break
                for m in re.finditer(
                    r'href="(https://9to5google\.com/\d{4}/\d{2}/\d{2}/[^"]+)"',
                    r.text,
                ):
                    u = m.group(1)
                    if u in seen:
                        continue
                    seen.add(u)
                    d = _extract_date(u)
                    if d and _in_window(d, crisis_code):
                        out.append((u, d, kw))
                await asyncio.sleep(1.0)
            except Exception as e:
                log.warning("  [9to5g] search '%s' p%d 실패: %s", kw, page, e)
                break
    return out


def _nine_parse(html: str, url: str, crisis_code: str) -> Optional[RawVOC]:
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("h1.article-title") or soup.select_one("h1")
    title = title_el.get_text(strip=True) if title_el else ""
    body_el = (
        soup.select_one("div.article-content")
        or soup.select_one("article")
        or soup.select_one("div.post-content")
    )
    body = body_el.get_text("\n", strip=True) if body_el else ""
    body = _truncate_body(body)
    full = f"{title}\n{body}".strip()
    if len(full) < 60:
        return None
    author_el = soup.select_one(".author__link a") or soup.select_one("[rel=author]")
    author = author_el.get_text(strip=True) if author_el else "9to5Google"

    pub: Optional[datetime] = None
    time_el = soup.select_one("time[datetime]")
    if time_el:
        dt_raw = (time_el.get("datetime") or "").replace("Z", "+00:00")
        try:
            pub = datetime.fromisoformat(dt_raw)
        except ValueError:
            pub = None
    if pub is None:
        d = _extract_date(url)
        if d:
            pub = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
    return RawVOC(
        external_id=hashlib.md5(url.encode()).hexdigest()[:16],
        content=full,
        source_url=url,
        author_name=author,
        published_at=pub,
        country_code="US",
        meta={"kind": "article", "source": "crisis_direct",
              "product_code": crisis_code, "crisis_code": crisis_code},
    )


# ═════════════════════════════════════════════════════════════════════════
# Platform 2: Engadget — legacy sitemap + URL 날짜 (/YYYY-MM-DD-slug.html)
# ═════════════════════════════════════════════════════════════════════════
_EG_BASE = "https://www.engadget.com"
_EG_URL_DATE_RE = re.compile(
    r"^https://www\.engadget\.com/(\d{4})-(\d{2})-(\d{2})-[^/]+\.html$"
)
# crisis 별 legacy sitemap (사전 측정 — 각 sitemap 의 *URL 범위* 기준).
#   각 sitemap 의 lastmod 는 *마지막 entry* 날짜라 직전 sitemap 도 포함해야 함.
#   sitemap44: 2016-01 ~ 2016-09  (lastmod 2016-09-27)
#   sitemap45: 2016-09 ~ 2017-07  (lastmod 2017-07-07)
#   sitemap47: 2018-04 ~ 2019-02  (lastmod 2019-02-20)
#   sitemap48: 2019-02 ~ 2020-01  (lastmod 2020-01-07)
#   sitemap49: 2020-01 ~ 2021-01  (lastmod 2021-01-11)
#   sitemap50: 2021-01 ~ 2022-03  (lastmod 2022-03-15)
#   sitemap51: 2022-03 ~ 2023-09  (lastmod 2023-09-07)
_EG_LEGACY_FOR: Dict[str, List[int]] = {
    "GN7":   [44, 45],        # 2016-08 ~ 2016-12 → sitemap44 + 45
    "GZF1":  [47, 48],        # 2019-04 ~ 2019-12 → sitemap47 (2019-02~) + 48
    "GS22U": [50, 51],        # 2022-02 ~ 2022-06 → sitemap50 (..2022-03) + 51
    "GZFL3": [50],            # 2021-08 ~ 2022-03 → sitemap50
    "GS20":  [48, 49],        # 2020-02 ~ 2020-12 → sitemap48 (..2020-01) + 49
}


def _eg_url_date(url: str) -> Optional[date]:
    m = _EG_URL_DATE_RE.match(url.strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


async def _eg_search_urls(
    client: httpx.AsyncClient, crisis_code: str
) -> List[Tuple[str, date, str]]:
    """Engadget legacy sitemap → 날짜 + 키워드 매칭.

    2020-말 이전 URL: ``/YYYY-MM-DD-slug.html`` (URL 자체에 날짜)
    2021-초 이후 URL: ``/slug-<id>.html`` (URL 에 날짜 없음 → <lastmod> 사용)
    """
    pat = CRISIS_KW_PATTERNS[crisis_code]
    found: Dict[str, Tuple[date, str]] = {}
    sitemap_ids = _EG_LEGACY_FOR.get(crisis_code, [])
    for sid in sitemap_ids:
        url = f"{_EG_BASE}/legacy-sitemap{sid}.xml"
        try:
            r = await client.get(url, timeout=30.0)
            if r.status_code != 200:
                log.warning("  [engadget] sitemap %s: status=%d", url, r.status_code)
                continue
            # <url><loc>...</loc><lastmod>...</lastmod></url> 쌍 매칭
            for mm in re.finditer(
                r"<url>\s*<loc>(https://www\.engadget\.com/[^<]+\.html)</loc>"
                r"\s*<lastmod>([^<]+)</lastmod>",
                r.text,
            ):
                u, lm = mm.group(1), mm.group(2)
                # 1) URL 자체에서 날짜 시도 (구 포맷)
                d = _eg_url_date(u)
                # 2) 없으면 lastmod 폴백 (신 포맷 + 변경된 구 글)
                if d is None:
                    d = _ac_parse_lastmod_date(lm)
                if not d or not _in_window(d, crisis_code):
                    continue
                slug = u.rsplit("/", 1)[-1].replace(".html", "").replace("-", " ")
                # 신 포맷은 끝에 ID 숫자 — 슬러그 매칭에서 제외
                slug = re.sub(r"\s+\d{6,}\s*$", "", slug)
                kw_m = pat.search(slug)
                if kw_m and u not in found:
                    found[u] = (d, kw_m.group(0))
            await asyncio.sleep(1.0)
        except Exception as e:
            log.warning("  [engadget] sitemap fetch 실패 %s: %s", url, e)
    return [(u, d, kw) for u, (d, kw) in found.items()]


def _eg_parse(html: str, url: str, crisis_code: str) -> Optional[RawVOC]:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""

    body_text = ""
    for na in soup.find_all("div", class_="news-article"):
        paras = [p.get_text(" ", strip=True) for p in na.find_all("p")]
        joined = "\n".join(p for p in paras if p)
        if len(joined) > len(body_text):
            body_text = joined
    if len(body_text) < 100:
        art = soup.find("article", class_="news-post")
        if art:
            paras = [p.get_text(" ", strip=True) for p in art.find_all("p")]
            body_text = "\n".join(p for p in paras if p)
    body_text = _truncate_body(body_text)
    content = f"{title}\n{body_text}".strip()
    if len(content) < 60:
        return None

    pub: Optional[datetime] = None
    pt = soup.find("meta", property="article:published_time")
    if pt and pt.get("content"):
        try:
            pub = datetime.fromisoformat(pt["content"].strip().replace("Z", "+00:00"))
        except ValueError:
            pub = None
    if pub is None:
        d = _eg_url_date(url)
        if d:
            pub = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)

    author = "Engadget"
    am = soup.find("meta", attrs={"name": "author"})
    if am and am.get("content"):
        author = am["content"].strip()

    m = re.search(r"/(\d{4}-\d{2}-\d{2}-[^/]+)\.html$", url)
    aid = m.group(1) if m else hashlib.md5(url.encode()).hexdigest()[:12]
    return RawVOC(
        external_id=hashlib.md5(f"{url}#{aid}".encode()).hexdigest()[:16],
        content=content,
        source_url=url,
        author_name=author,
        published_at=pub,
        country_code="US",
        meta={"kind": "article", "source": "crisis_direct",
              "product_code": crisis_code, "crisis_code": crisis_code,
              "article_id": aid},
    )


# ═════════════════════════════════════════════════════════════════════════
# Platform 3: TheVerge — /sitemaps/entries/YYYY/M, URL date /YYYY/M/D/.../slug
# ═════════════════════════════════════════════════════════════════════════
_TV_BASE = "https://www.theverge.com"
_TV_URL_DATE_RE = re.compile(
    r"^https://www\.theverge\.com/(?:[a-z-]+/)?(\d{4})/(\d{1,2})/(\d{1,2})/\d+(?:/[^/?#]+)?/?$"
)


def _tv_url_date(url: str) -> Optional[date]:
    m = _TV_URL_DATE_RE.match(url.strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _tv_months_for(crisis_code: str) -> List[Tuple[int, int]]:
    """crisis 윈도우에 걸치는 (year, month) 목록 — 월 단위 sitemap 키."""
    s, e = CRISIS_WINDOWS[crisis_code]
    out: List[Tuple[int, int]] = []
    y, m = s.year, s.month
    while (y, m) <= (e.year, e.month):
        out.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


async def _tv_search_urls(
    client: httpx.AsyncClient, crisis_code: str
) -> List[Tuple[str, date, str]]:
    pat = CRISIS_KW_PATTERNS[crisis_code]
    found: Dict[str, Tuple[date, str]] = {}
    for y, m in _tv_months_for(crisis_code):
        url = f"{_TV_BASE}/sitemaps/entries/{y}/{m}"
        try:
            r = await client.get(url, timeout=30.0)
            if r.status_code != 200:
                log.warning("  [theverge] sitemap %s: status=%d", url, r.status_code)
                continue
            for mm in re.finditer(
                r"<loc>(https://www\.theverge\.com/[^<]+?)</loc>",
                r.text,
            ):
                u = mm.group(1)
                d = _tv_url_date(u)
                if not d or not _in_window(d, crisis_code):
                    continue
                slug = u.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
                kw_m = pat.search(slug)
                if kw_m and u not in found:
                    found[u] = (d, kw_m.group(0))
            await asyncio.sleep(1.0)
        except Exception as e:
            log.warning("  [theverge] sitemap fetch 실패 %s: %s", url, e)
    return [(u, d, kw) for u, (d, kw) in found.items()]


def _tv_parse(html: str, url: str, crisis_code: str) -> Optional[RawVOC]:
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.find("meta", attrs={"property": "og:title"})
    title = title_el.get("content", "").strip() if title_el else ""
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else ""

    body_parts: List[str] = []
    for el in soup.select(
        "div.duet--article--article-body-component p, "
        "div.duet--article--article-body-component li"
    ):
        txt = el.get_text(" ", strip=True)
        if txt:
            body_parts.append(txt)
    body = "\n".join(body_parts).strip()
    if not body:
        desc_el = soup.find("meta", attrs={"property": "og:description"})
        body = desc_el.get("content", "").strip() if desc_el else ""
    body = _truncate_body(body)
    content = f"{title}\n{body}".strip() if body else title
    if len(content) < 60:
        return None

    pub: Optional[datetime] = None
    pub_el = soup.find("meta", attrs={"property": "article:published_time"})
    if pub_el and pub_el.get("content"):
        try:
            pub = datetime.fromisoformat(
                pub_el["content"].strip().replace("Z", "+00:00")
            )
        except ValueError:
            pub = None
    if pub is None:
        d = _tv_url_date(url)
        if d:
            pub = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)

    author_el = soup.find("meta", attrs={"name": "author"})
    author = author_el.get("content", "").strip() if author_el else "The Verge"

    m = re.search(r"/(\d{4,})/", url)
    aid = m.group(1) if m else hashlib.md5(url.encode()).hexdigest()[:12]
    return RawVOC(
        external_id=hashlib.md5(f"{url}#{aid}".encode()).hexdigest()[:16],
        content=content,
        source_url=url,
        author_name=author,
        published_at=pub,
        country_code="US",
        meta={"kind": "article", "source": "crisis_direct",
              "product_code": crisis_code, "crisis_code": crisis_code,
              "article_id": aid},
    )


# ═════════════════════════════════════════════════════════════════════════
# Platform 4: AndroidCentral — /sitemap-YYYY-MM.xml, <lastmod> 날짜
# ═════════════════════════════════════════════════════════════════════════
_AC_BASE = "https://www.androidcentral.com"


def _ac_months_for(crisis_code: str) -> List[Tuple[int, int]]:
    return _tv_months_for(crisis_code)  # 동일 로직


def _ac_parse_lastmod_date(text: str) -> Optional[date]:
    text = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt.date()
    except ValueError:
        return None


async def _ac_search_urls(
    client: httpx.AsyncClient, crisis_code: str
) -> List[Tuple[str, date, str]]:
    pat = CRISIS_KW_PATTERNS[crisis_code]
    found: Dict[str, Tuple[date, str]] = {}
    for y, m in _ac_months_for(crisis_code):
        url = f"{_AC_BASE}/sitemap-{y:04d}-{m:02d}.xml"
        try:
            r = await client.get(url, timeout=30.0)
            if r.status_code != 200:
                log.warning("  [androidcentral] sitemap %s: status=%d", url, r.status_code)
                continue
            for mm in re.finditer(
                r"<url>\s*<loc>(https://www\.androidcentral\.com/[^<]+)</loc>"
                r"\s*<lastmod>([^<]+)</lastmod>",
                r.text,
            ):
                u, lm = mm.group(1), mm.group(2)
                d = _ac_parse_lastmod_date(lm)
                if not d or not _in_window(d, crisis_code):
                    continue
                slug = u.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
                kw_m = pat.search(slug)
                if kw_m and u not in found:
                    found[u] = (d, kw_m.group(0))
            await asyncio.sleep(1.0)
        except Exception as e:
            log.warning("  [androidcentral] sitemap fetch 실패 %s: %s", url, e)
    return [(u, d, kw) for u, (d, kw) in found.items()]


def _ac_parse(html: str, url: str, crisis_code: str) -> Optional[RawVOC]:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""
    if not title:
        og = soup.find("meta", attrs={"property": "og:title"})
        if og:
            title = og.get("content", "").strip()

    body_parts: List[str] = []
    for sel in [
        "div#article-body p",
        "div.news-article p",
        "section.article-body p",
        "article p",
    ]:
        for el in soup.select(sel):
            txt = el.get_text(" ", strip=True)
            if txt and len(txt) > 20:
                body_parts.append(txt)
        if body_parts:
            break
    body = "\n".join(body_parts).strip()
    if not body:
        desc_el = soup.find("meta", attrs={"property": "og:description"})
        body = desc_el.get("content", "").strip() if desc_el else ""
    body = _truncate_body(body)
    content = f"{title}\n{body}".strip() if body else title
    if len(content) < 60:
        return None

    pub: Optional[datetime] = None
    for prop in ("article:published_time", "datePublished"):
        el = soup.find("meta", attrs={"property": prop}) or soup.find(
            "meta", attrs={"itemprop": prop}
        )
        if el and el.get("content"):
            try:
                pub = datetime.fromisoformat(
                    el["content"].strip().replace("Z", "+00:00")
                )
                break
            except ValueError:
                pass

    author_el = soup.find("meta", attrs={"name": "author"})
    author = author_el.get("content", "").strip() if author_el else "Android Central"

    slug = url.rstrip("/").rsplit("/", 1)[-1]
    aid = slug or hashlib.md5(url.encode()).hexdigest()[:12]
    return RawVOC(
        external_id=hashlib.md5(f"{url}#{aid}".encode()).hexdigest()[:16],
        content=content,
        source_url=url,
        author_name=author,
        published_at=pub,
        country_code="US",
        meta={"kind": "article", "source": "crisis_direct",
              "product_code": crisis_code, "crisis_code": crisis_code,
              "article_id": aid},
    )


# ═════════════════════════════════════════════════════════════════════════
# Platform 어댑터 레지스트리
# ═════════════════════════════════════════════════════════════════════════
SearchFn = Callable[[httpx.AsyncClient, str], Awaitable[List[Tuple[str, date, str]]]]
ParseFn = Callable[[str, str, str], Optional[RawVOC]]


class PlatformAdapter:
    def __init__(
        self,
        code: str,
        crawler_module: str,
        crawler_class: str,
        search_fn: SearchFn,
        parse_fn: ParseFn,
    ):
        self.code = code
        self.crawler_module = crawler_module
        self.crawler_class = crawler_class
        self.search_fn = search_fn
        self.parse_fn = parse_fn

    def make_crawler(self):
        mod = __import__(self.crawler_module, fromlist=[self.crawler_class])
        return getattr(mod, self.crawler_class)()


PLATFORM_ADAPTERS: Dict[str, PlatformAdapter] = {
    "9to5google": PlatformAdapter(
        "9to5google", "platforms.nineto5google", "NineTo5GoogleCrawler",
        _nine_search_urls, _nine_parse,
    ),
    "engadget": PlatformAdapter(
        "engadget", "platforms.engadget", "EngadgetCrawler",
        _eg_search_urls, _eg_parse,
    ),
    "theverge": PlatformAdapter(
        "theverge", "platforms.theverge", "TheVergeCrawler",
        _tv_search_urls, _tv_parse,
    ),
    "androidcentral": PlatformAdapter(
        "androidcentral", "platforms.androidcentral", "AndroidCentralCrawler",
        _ac_search_urls, _ac_parse,
    ),
}


# ═════════════════════════════════════════════════════════════════════════
# fetch + save 공통 파이프라인
# ═════════════════════════════════════════════════════════════════════════
async def _fetch_article(
    client: httpx.AsyncClient,
    url: str,
    crisis_code: str,
    parse_fn: ParseFn,
) -> Optional[RawVOC]:
    try:
        r = await client.get(url, timeout=25.0)
        if r.status_code != 200:
            return None
        return parse_fn(r.text, url, crisis_code)
    except Exception as e:
        log.debug("  fetch 실패 %s: %s", url, e)
        return None


async def _save_via_crawler(crawler, raw: List[RawVOC]) -> Dict[str, Any]:
    """R25 트랙 D — 반환값에 ``inserted_ids`` (list[int]) 포함.

    crawler.save() 는 int 만 반환하므로, save 전 max(id) 를 잡고
    save 후 platform_id + 신규 external_id 조합으로 PK 를 재조회.
    """
    if not raw or DRY_RUN:
        return {"saved": 0, "processed": 0, "dry_run": int(DRY_RUN),
                "preserve_existing": int(PRESERVE_EXISTING),
                "inserted_ids": []}
    seen: set = set()
    uniq: List[RawVOC] = []
    for v in raw:
        if v.external_id in seen:
            continue
        seen.add(v.external_id)
        uniq.append(v)
    log.info("  dedup: %d → %d", len(raw), len(uniq))

    from nlp.pipeline import process_voc_list  # noqa: E402
    std = [crawler.normalize(r) for r in uniq]
    processed = await process_voc_list(std)

    # R25 트랙 D — save 직전 max(id) snapshot.
    pre_max_id = await _max_voc_id()
    saved = await crawler.save(processed)
    log.info("  → save: %d / %d", saved, len(processed))

    inserted_ids = await _query_inserted_ids(
        crawler.platform_code, [p.external_id for p in processed], pre_max_id,
    )
    return {
        "saved": saved,
        "processed": len(processed),
        "dry_run": 0,
        "inserted_ids": inserted_ids,
    }


async def _max_voc_id() -> int:
    """voc_records.id 의 현재 max — save 전 시점.  실패 시 0."""
    if DRY_RUN or not os.getenv("DATABASE_URL"):
        return 0
    try:
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        from sqlalchemy import text
        engine = create_async_engine(os.getenv("DATABASE_URL"), pool_pre_ping=True)
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with Session() as db:
            row = (await db.execute(text(
                "SELECT COALESCE(MAX(id), 0) FROM voc_records"
            ))).one()
            return int(row[0] or 0)
    except Exception as e:
        log.warning("max_voc_id 조회 실패: %s", e)
        return 0
    finally:
        try:
            await engine.dispose()  # type: ignore[name-defined]
        except Exception:
            pass


async def _query_inserted_ids(
    platform_code: str, external_ids: List[str], pre_max_id: int,
) -> List[int]:
    """save 직후 (platform_code, external_id IN ...) AND id > pre_max_id 로
    *이번 save 에서 새로 들어간* PK 만 정확히 추출."""
    if DRY_RUN or not external_ids or not os.getenv("DATABASE_URL"):
        return []
    try:
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        from sqlalchemy import text
        engine = create_async_engine(os.getenv("DATABASE_URL"), pool_pre_ping=True)
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with Session() as db:
            rows = (await db.execute(text(
                """
                SELECT v.id FROM voc_records v
                JOIN platforms p ON p.id = v.platform_id
                WHERE p.code = :pcode
                  AND v.external_id = ANY(:eids)
                  AND v.id > :pre
                """
            ), {"pcode": platform_code, "eids": external_ids,
                "pre": int(pre_max_id)})).all()
            return [int(r[0]) for r in rows]
    except Exception as e:
        log.warning("inserted_ids 조회 실패: %s", e)
        return []
    finally:
        try:
            await engine.dispose()  # type: ignore[name-defined]
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════
# DB 측정
# ═════════════════════════════════════════════════════════════════════════
def _psql(sql: str) -> str:
    try:
        out = subprocess.run(
            ["psql", "-h", "127.0.0.1", "-p", "5434", "-U", "signalforge",
             "-d", "signalforge", "-tA", "-c", sql],
            env={**os.environ, "PGPASSWORD": "signalforge_pass"},
            capture_output=True, text=True, timeout=30,
        )
        return out.stdout.strip()
    except Exception as e:
        return f"err({e})"


_CRISIS_BY_PLATFORM_SQL = """
SELECT pl.name, COUNT(*) AS n
FROM voc_records vr
JOIN platforms pl ON pl.id = vr.platform_id
JOIN products p ON p.id = vr.product_id
WHERE (p.code='GN7'   AND vr.published_at::date BETWEEN '2016-08-19' AND '2016-12-31')
   OR (p.code='GZF1'  AND vr.published_at::date BETWEEN '2019-04-15' AND '2019-12-31')
   OR (p.code='GS22U' AND vr.published_at::date BETWEEN '2022-02-25' AND '2022-06-30')
   OR (p.code='GZFL3' AND vr.published_at::date BETWEEN '2021-08-01' AND '2022-03-31')
   OR (p.code='GS20'  AND vr.published_at::date BETWEEN '2020-02-01' AND '2020-12-31')
GROUP BY pl.name ORDER BY n DESC;
"""


def _crisis_by_platform() -> List[Tuple[str, int]]:
    out: List[Tuple[str, int]] = []
    for line in _psql(_CRISIS_BY_PLATFORM_SQL).splitlines():
        parts = line.split("|")
        if len(parts) >= 2:
            try:
                out.append((parts[0], int(parts[1])))
            except ValueError:
                pass
    return out


# ═════════════════════════════════════════════════════════════════════════
# 메인 (platform 별 실행)
# ═════════════════════════════════════════════════════════════════════════
async def _run_platform(adapter: PlatformAdapter, audit):
    log.info("--- platform=%s 시작 ---", adapter.code)
    crawler = adapter.make_crawler()
    all_raw: List[RawVOC] = []
    per_code: Dict[str, int] = {}

    async with httpx.AsyncClient(
        headers={"User-Agent": SAFARI_UA, "Accept-Language": "en-US,en;q=0.9"},
        follow_redirects=True,
    ) as client:
        for code in CRISIS_KEYWORDS:
            log.info("[%s/%s] 검색 시작", adapter.code, code)
            pairs = await adapter.search_fn(client, code)
            log.info("  → %d 윈도우 내 매칭", len(pairs))
            audit.bump(f"{adapter.code}.matched", len(pairs))

            # 코드당 상한: PER_KEYWORD_MAX * (키워드 수) — 9to5G 기존 의미와 일치
            cap = PER_KEYWORD_MAX * len(CRISIS_KEYWORDS[code])
            targets = pairs[:cap]
            per_code[code] = len(targets)

            if DRY_RUN:
                continue
            for u, _d, _kw in targets:
                voc = await _fetch_article(client, u, code, adapter.parse_fn)
                if voc:
                    all_raw.append(voc)
                    audit.bump(f"{adapter.code}.fetched", 1)
                else:
                    audit.bump(f"{adapter.code}.fetch_failed", 1)
                await asyncio.sleep(1.5)

    save_info = await _save_via_crawler(crawler, all_raw)
    audit.bump(f"{adapter.code}.saved", save_info.get("saved", 0))
    log.info("[%s] per_code=%s raw=%d saved=%d",
             adapter.code, per_code, len(all_raw), save_info.get("saved", 0))
    audit.note(f"{adapter.code} per_code={per_code} raw={len(all_raw)}")
    # R25 트랙 D — 이번 save 로 신규 INSERT 된 voc_records.id 보관 (drift cross-check).
    audit.add_affected_ids(
        f"{adapter.code}.voc_inserted", save_info.get("inserted_ids", [])
    )


async def main():
    log.info("=== Crisis Platform Direct (Multi) 시작 ===")
    log.info("  platform=%s dry_run=%s preserve=%s per_kw_max=%d pages=%d",
             PLATFORM_ARG, DRY_RUN, PRESERVE_EXISTING, PER_KEYWORD_MAX, MAX_PAGES)

    if not DRY_RUN and not os.getenv("DATABASE_URL"):
        log.error("DATABASE_URL 미설정 (실 save 모드 필수)")
        sys.exit(2)

    if PLATFORM_ARG == "all":
        adapters = list(PLATFORM_ADAPTERS.values())
    elif PLATFORM_ARG in PLATFORM_ADAPTERS:
        adapters = [PLATFORM_ADAPTERS[PLATFORM_ARG]]
    else:
        log.error("미지원 platform: %s (지원: %s, all)",
                  PLATFORM_ARG, list(PLATFORM_ADAPTERS))
        sys.exit(2)

    with record_run(
        script="crisis_platform_direct",
        mode="dry_run" if DRY_RUN else ("preserve" if PRESERVE_EXISTING else "full"),
        # R23 트랙 E — 표준키 (DRY_RUN/PRESERVE_EXISTING/BACKUP_BEFORE) 와
        # 도구별 키 (CPD_*) 둘 다 emit.  backfill_audit_monitor 가 표준키로
        # 안전상태를 평가 — INSERT_ONLY 면제와 무관하게 정확한 평가 가능.
        # crisis_platform_direct 는 ON CONFLICT DO NOTHING 수집기 →
        # 기존 row 의 컬럼을 절대 수정하지 않으므로 PRESERVE 의미상 True.
        # 별도 백업 스냅샷은 불필요 (수정 자체가 없음) → BACKUP_BEFORE True.
        env={
            # 표준키 — backfill_audit_monitor 가 인식
            "DRY_RUN": bool(DRY_RUN),
            "PRESERVE_EXISTING": True,
            "BACKUP_BEFORE": True,
            # 도구별 키 — 기존 호환성 유지
            "CPD_DRY_RUN": int(DRY_RUN),
            "CPD_PRESERVE_EXISTING": int(PRESERVE_EXISTING),
            "CPD_PER_KEYWORD_MAX": PER_KEYWORD_MAX,
            "CPD_MAX_PAGES": MAX_PAGES,
            "CPD_PLATFORM": PLATFORM_ARG,
        },
    ) as audit:
        audit.note(f"crisis codes={list(CRISIS_KEYWORDS)} "
                   f"platforms={[a.code for a in adapters]}")

        before = _crisis_by_platform()
        audit.note(f"[before-platform] {before[:8]}")
        log.info("[before-platform] %s", before)

        t0 = time.time()
        for adapter in adapters:
            await _run_platform(adapter, audit)

        elapsed = int(time.time() - t0)
        log.info("=== Direct 종료 (%ds) ===", elapsed)

        if not DRY_RUN:
            after = _crisis_by_platform()
            log.info("[after-platform] %s", after)
            for adapter in adapters:
                target_name = {
                    "9to5google": "9to5",
                    "engadget":   "Engadget",
                    "theverge":   "Verge",
                    "androidcentral": "Android Central",
                }.get(adapter.code, adapter.code)
                b = next((n for nm, n in before if target_name.lower() in nm.lower()), 0)
                a = next((n for nm, n in after  if target_name.lower() in nm.lower()), 0)
                log.info("  %s in-crisis: %d → %d (+%d)",
                         adapter.code, b, a, a - b)
                audit.note(f"{adapter.code} crisis voc: {b} → {a}")


if __name__ == "__main__":
    asyncio.run(main())
