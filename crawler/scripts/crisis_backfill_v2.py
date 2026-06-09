"""R19 트랙 C — Crisis voc 추가 보강 (HN deeper + bobaedream / ruliweb / fmkorea).

배경
====
R18 결과 운영 endpoint: GN7 218 / GZF1 107 / GS22U 2 / GZFL3 8 / GS20 38.
GS22U / GZFL3 / GS20 여전히 신호 빈약 → R12 200+ 키워드 다음 단계로:
  1. HN 더 깊은 세부 키워드 (Note 7 explosion video 같은 long-tail)
  2. KR 사이트 search endpoint 직접 사용 (bobaedream / ruliweb / fmkorea)

기존 ``crisis_kr_backfill.py`` 는 dcinside / ppomppu 만 다룸 — 본 스크립트는
추가 사이트와 검색어 매트릭스를 보강한다.

전략
====
- 모든 사이트의 검색 결과 페이지에서 (post_url, list-level stub) 수집
- 사이트별 기존 ``_fetch_post_detail()`` 재사용 (본문/댓글 파싱 + RawVOC 생성)
- BaseCrawler.normalize() + save() 로 NLP + DB 적재 (멱등 — ON CONFLICT)
- DRY_RUN 모드 — 검색 단계까지만 실행, save() 스킵 (R18 사고 예방 가이드 준수)

검색어 매트릭스 (R19 신규)
============================
HN (long-tail):
  GN7   — "Note 7 explosion video", "Note 7 explosion story"
  GZF1  — "Galaxy Fold initial review", "Fold display lift"
  GS22U — "Galaxy S22 GoS lawsuit", "Game Optimizing Service throttling"
  GZFL3 — "Galaxy Z Flip 3 hinge gap", "Z Flip 3 broken screen"
  GS20  — "Galaxy S20 5G price uk", "Galaxy S20 launch price uk"

KR 사이트:
  GN7   — "노트7 발화", "노트7 리콜", "Galaxy Note7"
  GZF1  — "폴드1 액정", "폴드1 결함", "갤럭시 폴드 화면"
  GS22U — "S22 게옵스", "GoS 사태", "게임 최적화 서비스"
  GZFL3 — "플립3 힌지", "Z Flip 3 액정", "플립3 결함"
  GS20  — "S20 가격", "S20 5G", "갤럭시 S20"

환경변수
========
DATABASE_URL                 필수
CRISIS_V2_PER_QUERY_LIMIT    기본 25  검색 결과 첫 N건만 상세 수집
CRISIS_V2_MAX_PAGES          기본 3   각 검색어 페이지 수
CRISIS_V2_SITES              기본 "hackernews,bobaedream,ruliweb"
                              (fmkorea 는 cf-bot challenge 잦아 기본 제외)
CRISIS_V2_DRY_RUN            '1' 이면 save() 스킵 — 검색만 (기본 '0')
CRISIS_V2_SAVE_PER_SITE      '1' 이면 사이트별 직후 save (메모리 절약, 기본 '1')

검증
====
- crawler/tests/test_crisis_v2.py — search URL 빌더 / parser 케이스
- 실행 후 PGPASSWORD=... psql 로 crisis 기간 voc 변화 측정 (before/after)
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
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("crisis_v2")


PER_QUERY_LIMIT = int(os.getenv("CRISIS_V2_PER_QUERY_LIMIT", "25"))
MAX_PAGES = int(os.getenv("CRISIS_V2_MAX_PAGES", "3"))
SITES = [s.strip() for s in os.getenv(
    "CRISIS_V2_SITES", "hackernews,bobaedream,ruliweb"
).split(",") if s.strip()]
DRY_RUN = os.getenv("CRISIS_V2_DRY_RUN", "0") == "1"
SAVE_PER_SITE = os.getenv("CRISIS_V2_SAVE_PER_SITE", "1") == "1"


# ─────────────────────────── 검색어 매트릭스 ───────────────────────────
HN_QUERIES: Dict[str, List[str]] = {
    "GN7":   ["Note 7 explosion video", "Note 7 explosion story",
              "Note 7 fire airplane"],
    "GZF1":  ["Galaxy Fold initial review", "Fold display lift",
              "Galaxy Fold screen broken"],
    "GS22U": ["Galaxy S22 GoS lawsuit", "Game Optimizing Service throttling",
              "Samsung GoS benchmark"],
    "GZFL3": ["Galaxy Z Flip 3 hinge gap", "Z Flip 3 broken screen",
              "Z Flip 3 durability"],
    "GS20":  ["Galaxy S20 5G price uk", "Galaxy S20 launch price uk",
              "Galaxy S20 price drop"],
}

KR_QUERIES: Dict[str, List[str]] = {
    "GN7":   ["노트7 발화", "노트7 리콜", "Galaxy Note7", "노트7 폭발"],
    "GZF1":  ["폴드1 액정", "폴드1 결함", "갤럭시 폴드 화면", "폴드 들뜸"],
    "GS22U": ["S22 게옵스", "GoS 사태", "게임 최적화 서비스", "S22 GOS"],
    "GZFL3": ["플립3 힌지", "Z Flip 3 액정", "플립3 결함", "플립3 깨짐"],
    "GS20":  ["S20 가격", "S20 5G", "갤럭시 S20", "S20 단점"],
}


# ═══════════════════════════════════════════════════════════════════════
# 1) Hacker News — Algolia API
# ═══════════════════════════════════════════════════════════════════════
ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM_URL = "https://news.ycombinator.com/item?id="


async def hn_search_all(client: httpx.AsyncClient) -> Tuple[List[RawVOC], Dict[str, int]]:
    """HN Algolia 로 long-tail 검색어 실행. 반환: (raw_vocs, per_keyword_count)"""
    from platforms.hackernews import HackerNewsCrawler  # noqa: E402
    hn = HackerNewsCrawler()
    raw: List[RawVOC] = []
    seen_ids: set = set()
    counts: Dict[str, int] = {}

    for code, queries in HN_QUERIES.items():
        for q in queries:
            try:
                # story + comment 두 풀 모두 시도
                story_hits = await hn._search(
                    client, query=q, tags="story", hits_per_page=50,
                )
                comment_hits = await hn._search(
                    client, query=q, tags="comment", hits_per_page=50,
                )
            except Exception as e:
                log.warning("  hn '%s' 검색 실패: %s", q, e)
                counts[f"{code}/{q}"] = 0
                continue

            added = 0
            for hit in story_hits[:PER_QUERY_LIMIT]:
                v = hn._story_hit_to_voc(hit)
                if v and v.external_id not in seen_ids:
                    seen_ids.add(v.external_id)
                    raw.append(v)
                    added += 1
            for hit in comment_hits[:PER_QUERY_LIMIT]:
                v = hn._comment_hit_to_voc(hit)
                if v and v.external_id not in seen_ids:
                    seen_ids.add(v.external_id)
                    raw.append(v)
                    added += 1
            log.info("  hn '%s' (%s): +%d (story=%d, comment=%d)",
                     q, code, added, len(story_hits), len(comment_hits))
            counts[f"{code}/{q}"] = added
            await asyncio.sleep(0.6)  # Algolia free tier ~ 1 rps

    return raw, counts


# ═══════════════════════════════════════════════════════════════════════
# 2) Bobaedream — search endpoint
# ═══════════════════════════════════════════════════════════════════════
BOBAE_BASE = "https://www.bobaedream.co.kr"
BOBAE_ALLOWED = {"strange", "freeb", "national", "best", "ask"}


def _bobae_extract_post_urls(html: str) -> List[str]:
    """bobaedream 검색결과에서 view URL 추출.

    형식: href="/view?code=strange&No=6926299"
    """
    out: List[str] = []
    seen: set = set()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "view?" not in href:
            continue
        # 절대 URL 정규화
        full = href if href.startswith("http") else f"{BOBAE_BASE}{href}"
        try:
            q = parse_qs(urlparse(full).query)
        except Exception:
            continue
        code = (q.get("code") or [""])[0]
        no = (q.get("no") or q.get("No") or [""])[0]
        if not code or not no or not no.isdigit():
            continue
        if code not in BOBAE_ALLOWED:
            # 다른 board (e.g. mall) 는 제외 — 광고/상품 게시판
            continue
        key = (code, no)
        if key in seen:
            continue
        seen.add(key)
        out.append(f"{BOBAE_BASE}/view?code={code}&No={no}")
    return out


_BOBAE_SESSION_SEEDED = False


async def _bobae_seed_session(client: httpx.AsyncClient) -> None:
    """bobaedream search 는 세션 쿠키 필수 — 미요청 시 JS redirect 페이지(63 byte)
    만 반환. 첫 호출 전에 홈페이지를 1회 GET 해서 PHPSESSID 등 쿠키 확보."""
    global _BOBAE_SESSION_SEEDED
    if _BOBAE_SESSION_SEEDED:
        return
    try:
        await client.get(f"{BOBAE_BASE}/", headers={"Referer": BOBAE_BASE})
        _BOBAE_SESSION_SEEDED = True
        await asyncio.sleep(0.5)
    except Exception as e:
        log.warning("  bobae 세션 시드 실패: %s", e)


async def bobae_search(client: httpx.AsyncClient, keyword: str) -> List[RawVOC]:
    """bobaedream 검색 → 결과 URL 들을 list-level stub 으로 반환."""
    await _bobae_seed_session(client)
    stubs: List[RawVOC] = []
    seen_ids: set = set()
    for page in range(1, MAX_PAGES + 1):
        try:
            resp = await client.get(
                f"{BOBAE_BASE}/search",
                params={"keyword": keyword, "type": "0", "page": str(page)},
                headers={"Referer": f"{BOBAE_BASE}/"},
            )
            if resp.status_code != 200:
                log.debug("  bobae '%s' p%d: status=%d", keyword, page, resp.status_code)
                continue
            urls = _bobae_extract_post_urls(resp.text)
            added = 0
            for u in urls:
                uid = hashlib.md5(u.encode()).hexdigest()[:16]
                if uid in seen_ids:
                    continue
                seen_ids.add(uid)
                stubs.append(RawVOC(
                    external_id=uid,
                    content=keyword,
                    source_url=u,
                    author_name=None,
                    published_at=None,
                    country_code="KR",
                ))
                added += 1
            log.info("  bobae '%s' p%d: +%d (총 %d)", keyword, page, added, len(stubs))
            if added == 0:
                break
            await asyncio.sleep(1.5)
        except Exception as e:
            log.warning("  bobae '%s' p%d 실패: %s", keyword, page, e)
    return stubs


# ═══════════════════════════════════════════════════════════════════════
# 3) Ruliweb — search endpoint
# ═══════════════════════════════════════════════════════════════════════
RULI_BASE = "https://bbs.ruliweb.com"
RULI_POST_RE = re.compile(r'^/(?:[a-z]+/)?board/(\d+)/read/(\d+)$')


def _ruli_extract_post_urls(html: str) -> List[str]:
    """ruliweb 검색결과에서 read URL 추출.

    유효 패턴: https://bbs.ruliweb.com/<section>/board/<bid>/read/<rid>
    section: community / mobile / etcs / news / market / ...
    """
    out: List[str] = []
    seen: set = set()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/read/" not in href:
            continue
        # 절대 URL 정규화
        full = href if href.startswith("http") else f"{RULI_BASE}{href}"
        try:
            path = urlparse(full).path
        except Exception:
            continue
        # 정확한 패턴 — /<section>/board/<bid>/read/<rid>
        m = re.match(r'^/([a-z]+)/board/(\d+)/read/(\d+)$', path)
        if not m:
            continue
        section, bid, rid = m.group(1), m.group(2), m.group(3)
        if section in {"market"}:  # 거래 게시판 제외
            continue
        key = (bid, rid)
        if key in seen:
            continue
        seen.add(key)
        out.append(f"{RULI_BASE}/{section}/board/{bid}/read/{rid}")
    return out


async def ruli_search(client: httpx.AsyncClient, keyword: str) -> List[RawVOC]:
    """ruliweb 검색 → 결과 URL → stub."""
    stubs: List[RawVOC] = []
    seen_ids: set = set()
    for page in range(1, MAX_PAGES + 1):
        try:
            resp = await client.get(
                f"{RULI_BASE}/search",
                params={"q": keyword, "page": str(page)},
                headers={"Referer": f"{RULI_BASE}/"},
            )
            if resp.status_code != 200:
                log.debug("  ruli '%s' p%d: status=%d", keyword, page, resp.status_code)
                continue
            urls = _ruli_extract_post_urls(resp.text)
            added = 0
            for u in urls:
                uid = hashlib.md5(u.encode()).hexdigest()[:16]
                if uid in seen_ids:
                    continue
                seen_ids.add(uid)
                stubs.append(RawVOC(
                    external_id=uid,
                    content=keyword,
                    source_url=u,
                    author_name=None,
                    published_at=None,
                    country_code="KR",
                ))
                added += 1
            log.info("  ruli '%s' p%d: +%d (총 %d)", keyword, page, added, len(stubs))
            if added == 0:
                break
            await asyncio.sleep(1.5)
        except Exception as e:
            log.warning("  ruli '%s' p%d 실패: %s", keyword, page, e)
    return stubs


# ═══════════════════════════════════════════════════════════════════════
# 4) FMKorea — search endpoint (cf-bot challenge 가능성 — best-effort)
# ═══════════════════════════════════════════════════════════════════════
FMK_BASE = "https://www.fmkorea.com"


def _fmk_extract_post_urls(html: str) -> List[str]:
    out: List[str] = []
    seen: set = set()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"document_srl=(\d+)", href)
        if not m:
            continue
        srl = m.group(1)
        if srl in seen:
            continue
        seen.add(srl)
        full = href if href.startswith("http") else f"{FMK_BASE}{href}"
        out.append(full)
    return out


async def fmk_search(client: httpx.AsyncClient, keyword: str) -> List[RawVOC]:
    stubs: List[RawVOC] = []
    seen_ids: set = set()
    for page in range(1, MAX_PAGES + 1):
        try:
            resp = await client.get(
                f"{FMK_BASE}/index.php",
                params={
                    "mid": "best",
                    "search_target": "title_content",
                    "search_keyword": keyword,
                    "page": str(page),
                },
                headers={"Referer": f"{FMK_BASE}/"},
            )
            if resp.status_code != 200:
                log.debug("  fmk '%s' p%d: status=%d", keyword, page, resp.status_code)
                continue
            # cf-bot challenge 감지
            if "challenges.cloudflare" in resp.text or "cf-browser-verification" in resp.text:
                log.warning("  fmk '%s' p%d: cf-challenge", keyword, page)
                break
            urls = _fmk_extract_post_urls(resp.text)
            added = 0
            for u in urls:
                uid = hashlib.md5(u.encode()).hexdigest()[:16]
                if uid in seen_ids:
                    continue
                seen_ids.add(uid)
                stubs.append(RawVOC(
                    external_id=uid,
                    content=keyword,
                    source_url=u,
                    author_name=None,
                    published_at=None,
                    country_code="KR",
                ))
                added += 1
            log.info("  fmk '%s' p%d: +%d (총 %d)", keyword, page, added, len(stubs))
            if added == 0:
                break
            await asyncio.sleep(2.0)
        except Exception as e:
            log.warning("  fmk '%s' p%d 실패: %s", keyword, page, e)
    return stubs


# ═══════════════════════════════════════════════════════════════════════
# 5) Detail fetch + save 파이프라인
# ═══════════════════════════════════════════════════════════════════════
async def _fetch_details(crawler, client: httpx.AsyncClient,
                         stubs: List[RawVOC]) -> List[RawVOC]:
    """stub URL 들에 대해 platform._fetch_post_detail 호출.

    - PER_QUERY_LIMIT 까지만 시도 (dead URL 누적 대비)
    - 연속 5회 실패 → 키워드 중단 (검색 결과 페이지가 stale 한 경우)
    """
    out: List[RawVOC] = []
    consecutive_fail = 0
    for stub in stubs[:PER_QUERY_LIMIT]:
        try:
            recs = await asyncio.wait_for(
                crawler._fetch_post_detail(client, stub), timeout=8.0
            )
            out.extend(recs)
            consecutive_fail = 0
            await asyncio.sleep(0.8)
        except (asyncio.TimeoutError, Exception) as e:
            consecutive_fail += 1
            log.warning("  detail 실패 %s: %s", stub.source_url, type(e).__name__)
            if consecutive_fail >= 5:
                log.warning("  연속 5회 실패 → 키워드 중단")
                break
            await asyncio.sleep(0.3)
    return out


async def _save_via_crawler(crawler, raw: List[RawVOC]) -> Dict[str, int]:
    """BaseCrawler.normalize() + NLP + save() — 멱등."""
    if not raw or DRY_RUN:
        return {"saved": 0, "processed": 0, "dry_run": int(DRY_RUN)}
    # external_id dedup
    seen: set = set()
    unique: List[RawVOC] = []
    for v in raw:
        if v.external_id in seen:
            continue
        seen.add(v.external_id)
        unique.append(v)
    log.info("  dedup: %d → %d", len(raw), len(unique))

    from nlp.pipeline import process_voc_list  # noqa: E402
    std = [crawler.normalize(r) for r in unique]
    processed = await process_voc_list(std)
    saved = await crawler.save(processed)
    log.info("  → save: %d / %d", saved, len(processed))
    return {"saved": saved, "processed": len(processed), "dry_run": 0}


# ═══════════════════════════════════════════════════════════════════════
# 6) 사이트별 실행 — 검색 + 상세 + (옵션)저장
# ═══════════════════════════════════════════════════════════════════════
async def run_hackernews() -> Dict:
    from platforms.hackernews import HackerNewsCrawler  # noqa: E402
    hn = HackerNewsCrawler()
    async with hn._make_httpx_client() as client:
        raw, kw_counts = await hn_search_all(client)
    saved_info = await _save_via_crawler(hn, raw) if SAVE_PER_SITE else {"deferred": len(raw)}
    return {"raw_count": len(raw), "per_keyword": kw_counts, "save": saved_info, "raw": raw}


async def run_bobae() -> Dict:
    from platforms.bobaedream import BobaeDreamCrawler  # noqa: E402
    crawler = BobaeDreamCrawler()
    all_raw: List[RawVOC] = []
    kw_counts: Dict[str, int] = {}
    async with crawler._make_httpx_client() as client:
        client.headers.update({"User-Agent": crawler._random_ua()})
        for code, queries in KR_QUERIES.items():
            for q in queries:
                log.info("[bobae] 검색: %s (%s)", q, code)
                stubs = await bobae_search(client, q)
                if not stubs:
                    kw_counts[f"{code}/{q}"] = 0
                    continue
                detail = await _fetch_details(crawler, client, stubs)
                log.info("  → detail %d건", len(detail))
                kw_counts[f"{code}/{q}"] = len(detail)
                all_raw.extend(detail)
    saved_info = await _save_via_crawler(crawler, all_raw) if SAVE_PER_SITE else {"deferred": len(all_raw)}
    return {"raw_count": len(all_raw), "per_keyword": kw_counts, "save": saved_info, "raw": all_raw}


async def run_ruli() -> Dict:
    from platforms.ruliweb import RuliwebCrawler  # noqa: E402
    crawler = RuliwebCrawler()
    all_raw: List[RawVOC] = []
    kw_counts: Dict[str, int] = {}
    async with crawler._make_httpx_client() as client:
        client.headers.update({"User-Agent": crawler._random_ua()})
        for code, queries in KR_QUERIES.items():
            for q in queries:
                log.info("[ruli] 검색: %s (%s)", q, code)
                stubs = await ruli_search(client, q)
                if not stubs:
                    kw_counts[f"{code}/{q}"] = 0
                    continue
                detail = await _fetch_details(crawler, client, stubs)
                log.info("  → detail %d건", len(detail))
                kw_counts[f"{code}/{q}"] = len(detail)
                all_raw.extend(detail)
    saved_info = await _save_via_crawler(crawler, all_raw) if SAVE_PER_SITE else {"deferred": len(all_raw)}
    return {"raw_count": len(all_raw), "per_keyword": kw_counts, "save": saved_info, "raw": all_raw}


async def run_fmk() -> Dict:
    from platforms.fmkorea import FMKoreaCrawler  # noqa: E402
    crawler = FMKoreaCrawler()
    all_raw: List[RawVOC] = []
    kw_counts: Dict[str, int] = {}
    async with crawler._make_httpx_client() as client:
        client.headers.update({"User-Agent": crawler._random_ua()})
        for code, queries in KR_QUERIES.items():
            for q in queries:
                log.info("[fmk] 검색: %s (%s)", q, code)
                stubs = await fmk_search(client, q)
                if not stubs:
                    kw_counts[f"{code}/{q}"] = 0
                    continue
                detail = await _fetch_details(crawler, client, stubs)
                log.info("  → detail %d건", len(detail))
                kw_counts[f"{code}/{q}"] = len(detail)
                all_raw.extend(detail)
    saved_info = await _save_via_crawler(crawler, all_raw) if SAVE_PER_SITE else {"deferred": len(all_raw)}
    return {"raw_count": len(all_raw), "per_keyword": kw_counts, "save": saved_info, "raw": all_raw}


SITE_RUNNERS = {
    "hackernews": run_hackernews,
    "bobaedream": run_bobae,
    "ruliweb":    run_ruli,
    "fmkorea":    run_fmk,
}


# ═══════════════════════════════════════════════════════════════════════
# 7) DB 카운터 (before / after)
# ═══════════════════════════════════════════════════════════════════════
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


def _crisis_counts() -> Dict[str, Tuple[int, int]]:
    sql = """
SELECT p.code,
       COUNT(*) AS n,
       SUM(CASE WHEN vr.sentiment_label='negative' THEN 1 ELSE 0 END) AS neg
FROM products p
JOIN voc_records vr ON vr.product_id = p.id
WHERE (p.code='GN7'   AND vr.published_at::date BETWEEN '2016-08-19' AND '2016-12-31')
   OR (p.code='GZF1'  AND vr.published_at::date BETWEEN '2019-04-15' AND '2019-12-31')
   OR (p.code='GS22U' AND vr.published_at::date BETWEEN '2022-02-25' AND '2022-06-30')
   OR (p.code='GZFL3' AND vr.published_at::date BETWEEN '2021-08-01' AND '2022-03-31')
   OR (p.code='GS20'  AND vr.published_at::date BETWEEN '2020-02-01' AND '2020-12-31')
GROUP BY p.code ORDER BY p.code;
"""
    out: Dict[str, Tuple[int, int]] = {}
    for line in _psql(sql).splitlines():
        parts = line.split("|")
        if len(parts) >= 3:
            try:
                out[parts[0]] = (int(parts[1]), int(parts[2]))
            except ValueError:
                pass
    return out


def _crisis_by_platform() -> List[Tuple[str, int]]:
    sql = """
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
    out: List[Tuple[str, int]] = []
    for line in _psql(sql).splitlines():
        parts = line.split("|")
        if len(parts) >= 2:
            try:
                out.append((parts[0], int(parts[1])))
            except ValueError:
                pass
    return out


# ═══════════════════════════════════════════════════════════════════════
# 8) 메인
# ═══════════════════════════════════════════════════════════════════════
async def main():
    if not os.getenv("DATABASE_URL") and not DRY_RUN:
        log.error("DATABASE_URL 미설정 (DRY_RUN=1 로 검색만 시도하려면 환경변수 설정)")
        sys.exit(2)

    log.info("=== Crisis Backfill v2 시작 ===")
    log.info("  sites=%s per_query=%d pages=%d dry_run=%s save_per_site=%s",
             SITES, PER_QUERY_LIMIT, MAX_PAGES, DRY_RUN, SAVE_PER_SITE)

    before = _crisis_counts() if not DRY_RUN else {}
    before_plat = _crisis_by_platform() if not DRY_RUN else []
    log.info("[before] %s", before)

    t0 = time.time()
    results: Dict[str, Dict] = {}
    pending: List[Tuple[object, List[RawVOC]]] = []

    for site in SITES:
        runner = SITE_RUNNERS.get(site)
        if not runner:
            log.warning("  unknown site: %s — skip", site)
            continue
        ts = time.time()
        try:
            r = await runner()
            # 'raw' 는 SAVE_PER_SITE=0 일 때 보관용 — 결과 요약에는 제외
            summary = {k: v for k, v in r.items() if k != "raw"}
            results[site] = summary
            log.info("[%s] 완료 (%ds) — raw=%d", site, int(time.time() - ts), r["raw_count"])
        except Exception as e:
            log.exception("[%s] 실패: %s", site, e)
            results[site] = {"error": str(e)}

    elapsed = int(time.time() - t0)
    log.info("=== Crisis Backfill v2 종료 (%ds) ===", elapsed)

    if not DRY_RUN:
        after = _crisis_counts()
        after_plat = _crisis_by_platform()
        log.info("[after]  %s", after)
        for code in sorted(set(before) | set(after)):
            b = before.get(code, (0, 0))
            a = after.get(code, (0, 0))
            log.info("  %s: total %d → %d (+%d) | neg %d → %d (+%d)",
                     code, b[0], a[0], a[0] - b[0], b[1], a[1], a[1] - b[1])
        log.info("[platform before] %s", before_plat)
        log.info("[platform after]  %s", after_plat)

    log.info("[per-site] %s", results)


if __name__ == "__main__":
    asyncio.run(main())
