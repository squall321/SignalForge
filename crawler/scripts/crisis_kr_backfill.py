"""R18 트랙 C — Crisis 한국어 검색어 본격 백필 (dcinside / ppomppu).

목적
====
crisis-cases 5건 중 GS22U(2) / GZFL3(8) 의 voc 빈약 → 한국 사이트
검색 엔드포인트로 본격 보강.

전략
====
- 사이트별 SEARCH URL 패턴을 사용해 위기 키워드별 게시물 URL 수집
- 각 URL 에 대해 기존 platforms.{site}._fetch_post_detail() 재사용
  (본문/댓글 파싱 + RawVOC 생성 로직 재사용 — 새 파서 작성 불필요)
- BaseCrawler.normalize() + save() 로 NLP + DB 적재 (멱등 — ON CONFLICT)
- clien 은 search 가 JS-rendered (검색결과 빈 페이지) → 제외, 기존 historical
  backfill 로 커버

검색어 매트릭스
==============
- GoS / 게옵스 / 게임 최적화                 → GS22U 보강
- 플립3 / Z Flip3 / 힌지                     → GZFL3 보강
- 노트7 / Note7 / 발화                       → GN7 보강
- 폴드 결함 / 폴드 화면                       → GZF1 보강
- S20 가격 / S20 5G                          → GS20 보강

검증
====
- crawler/tests/test_crisis_kr.py — search URL 빌드/파싱 2 케이스
- 실행 후 PGPASSWORD=... psql 로 crisis 기간 voc 변화 측정

환경변수
========
DATABASE_URL                필수
CRISIS_KR_PER_QUERY_LIMIT   기본 20  검색 결과 첫 N건만 상세 수집
CRISIS_KR_MAX_PAGES         기본 3   각 검색어 페이지 수
CRISIS_KR_SITES             기본 "dcinside,ppomppu"
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
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC  # noqa: E402
from platforms.dcinside import DCInsideCrawler  # noqa: E402
from platforms.ppomppu import PpomppuCrawler  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("crisis_kr")


PER_QUERY_LIMIT = int(os.getenv("CRISIS_KR_PER_QUERY_LIMIT", "20"))
MAX_PAGES = int(os.getenv("CRISIS_KR_MAX_PAGES", "3"))
SITES = [s.strip() for s in os.getenv("CRISIS_KR_SITES", "dcinside,ppomppu").split(",")]


# 위기 사례별 검색어 묶음 (Discovery 결과 + DC 실측 hit 키워드)
CRISIS_QUERIES: Dict[str, List[str]] = {
    "GN7":   ["노트7", "Note7", "갤럭시 노트7"],            # 발화 단독은 hit 부진 → 모델명 위주
    "GZF1":  ["갤럭시 폴드 결함", "폴드 화면", "Fold 결함"],
    "GS22U": ["GOS", "GoS", "게옵스", "게임 최적화"],
    "GZFL3": ["플립3", "Z Flip3", "Z플립3"],
    "GS20":  ["S20 가격", "S20 5G", "갤럭시 S20"],
}


# ───────────────────────────── DCInside 검색 ─────────────────────────────
DC_GALLERIES_SEARCH = [
    ("mgallery/board", "galaxy"),
    ("board",          "smartphone"),
]


def _dc_build_search_url(prefix: str, gid: str, keyword: str, page: int) -> str:
    """DC 검색 URL — s_type=search_subject_memo (제목+본문)"""
    # urlencode 는 httpx params 가 처리
    return f"https://gall.dcinside.com/{prefix}/lists/"


async def dc_search_urls(client: httpx.AsyncClient, keyword: str) -> List[Tuple[str, RawVOC]]:
    """DC 검색 → 검색 결과 페이지에서 (post_url, list-level RawVOC) 추출.

    이미 DCInsideCrawler._parse_list 가 tr.ub-content → RawVOC 로 변환하므로
    그대로 재사용. 광고/공지(.gall_num != 숫자) 는 자동 필터.
    """
    crawler = DCInsideCrawler()
    found: List[Tuple[str, RawVOC]] = []
    seen_ids: set = set()

    for prefix, gid in DC_GALLERIES_SEARCH:
        url = _dc_build_search_url(prefix, gid, keyword, 1)
        for page in range(1, MAX_PAGES + 1):
            try:
                resp = await client.get(
                    url,
                    params={
                        "id": gid,
                        "s_type": "search_subject_memo",
                        "s_keyword": keyword,
                        "page": str(page),
                    },
                    headers={"Referer": "https://gall.dcinside.com/"},
                )
                if resp.status_code != 200:
                    log.debug("  dc %s/%s p%d: status=%d", gid, keyword, page, resp.status_code)
                    continue
                # DCInsideCrawler._parse_list 재사용 — 광고 자동 필터
                rows = crawler._parse_list(resp.text)
                # post URL 그대로
                added = 0
                for r in rows:
                    if r.external_id in seen_ids:
                        continue
                    seen_ids.add(r.external_id)
                    found.append((r.source_url, r))
                    added += 1
                log.info("  dc %s/'%s' p%d: +%d (총 %d)", gid, keyword, page, added, len(found))
                if added == 0:
                    # 더 이상 결과 없음
                    break
                await asyncio.sleep(1.5)
            except Exception as e:
                log.warning("  dc %s/'%s' p%d 실패: %s", gid, keyword, page, e)

    return found


# ───────────────────────────── Ppomppu 검색 ─────────────────────────────
def _ppomppu_extract_post_urls(html: str) -> List[str]:
    """ppomppu search_bbs.php 결과에서 view.php URL 추출.

    - 광고/공지/외부 게시판 페이지(regulation) 제외
    - id=phone OR id=ppomppu OR id=freeboard OR id=review 만 유지
    - keyword 쿼리 제거하고 (id, no) 기준 unique
    """
    soup = BeautifulSoup(html, "lxml")
    urls: List[str] = []
    seen: set = set()
    base = "https://www.ppomppu.co.kr"

    ALLOWED_BBS = {"phone", "ppomppu", "freeboard", "review", "humor"}

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "view.php" not in href:
            continue
        # 절대 URL 정규화
        full = href if href.startswith("http") else (
            f"{base}{href}" if href.startswith("/") else f"{base}/zboard/{href}"
        )
        try:
            q = parse_qs(urlparse(full).query)
        except Exception:
            continue
        bbs_id = (q.get("id") or [""])[0]
        no = (q.get("no") or [""])[0]
        if not bbs_id or not no or not no.isdigit():
            continue
        if bbs_id not in ALLOWED_BBS:
            continue
        key = (bbs_id, no)
        if key in seen:
            continue
        seen.add(key)
        # keyword/divpage 등 제거한 canonical URL
        clean = f"{base}/zboard/view.php?id={bbs_id}&no={no}"
        urls.append(clean)
    return urls


async def ppomppu_search_urls(client: httpx.AsyncClient, keyword: str) -> List[Tuple[str, RawVOC]]:
    found: List[Tuple[str, RawVOC]] = []
    seen_ids: set = set()

    for page in range(1, MAX_PAGES + 1):
        try:
            resp = await client.get(
                "https://www.ppomppu.co.kr/search_bbs.php",
                params={
                    "page_size": "20",
                    "keyword": keyword,
                    "page_no": str(page),
                    "order_type": "date",
                },
                headers={"Referer": "https://www.ppomppu.co.kr/"},
            )
            if resp.status_code != 200:
                log.debug("  pp '%s' p%d: status=%d", keyword, page, resp.status_code)
                continue
            html = resp.content.decode("euc-kr", "ignore")
            urls = _ppomppu_extract_post_urls(html)
            added = 0
            for u in urls:
                uid = hashlib.md5(u.encode()).hexdigest()[:16]
                if uid in seen_ids:
                    continue
                seen_ids.add(uid)
                stub = RawVOC(
                    external_id=uid,
                    content=keyword,           # 제목 미상 → 키워드 stub (detail 에서 덮어씀)
                    source_url=u,
                    author_name=None,
                    published_at=None,
                    country_code="KR",
                )
                found.append((u, stub))
                added += 1
            log.info("  pp '%s' p%d: +%d (총 %d)", keyword, page, added, len(found))
            if added == 0:
                break
            await asyncio.sleep(1.5)
        except Exception as e:
            log.warning("  pp '%s' p%d 실패: %s", keyword, page, e)

    return found


# ───────────────────────────── Detail fetch 재사용 ─────────────────────────────
async def _fetch_with_crawler(
    crawler, client: httpx.AsyncClient, stubs: List[RawVOC]
) -> List[RawVOC]:
    out: List[RawVOC] = []
    # 두 사이트 모두 동기/비동기 모두 _fetch_post_detail 시그니처 동일
    for stub in stubs[:PER_QUERY_LIMIT]:
        try:
            recs = await crawler._fetch_post_detail(client, stub)
            out.extend(recs)
            await asyncio.sleep(1.5)
        except Exception as e:
            log.warning("  detail 실패 %s: %s", stub.source_url, e)
    return out


# ───────────────────────────── DB 헬퍼 ─────────────────────────────
def _psql_scalar(sql: str) -> str:
    try:
        out = subprocess.run(
            ["psql", "-h", "127.0.0.1", "-p", "5434", "-U", "signalforge",
             "-d", "signalforge", "-tA", "-c", sql],
            env={**os.environ, "PGPASSWORD": "signalforge_pass"},
            capture_output=True, text=True, timeout=30,
        )
        return out.stdout.strip() or "?"
    except Exception as e:
        return f"err({e})"


def _crisis_counts() -> Dict[str, Tuple[int, int]]:
    """{code: (total_in_period, neg_in_period)}"""
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
    raw = _psql_scalar(sql)
    out: Dict[str, Tuple[int, int]] = {}
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) >= 3:
            try:
                out[parts[0]] = (int(parts[1]), int(parts[2]))
            except ValueError:
                pass
    return out


# ───────────────────────────── 메인 파이프라인 ─────────────────────────────
async def run_site(site: str) -> Dict[str, int]:
    """사이트 1개 처리. 반환: {keyword: saved_count}"""
    if site == "dcinside":
        crawler = DCInsideCrawler()
        search_fn = dc_search_urls
    elif site == "ppomppu":
        crawler = PpomppuCrawler()
        search_fn = ppomppu_search_urls
    else:
        log.warning("  unknown site: %s", site)
        return {}

    # crisis 키워드 평탄화 — code 별 dict 유지 (saved 분석용)
    keyword_pairs: List[Tuple[str, str]] = []
    for code, queries in CRISIS_QUERIES.items():
        for q in queries:
            keyword_pairs.append((code, q))

    per_keyword_saved: Dict[str, int] = {}
    all_raw: List[RawVOC] = []

    async with crawler._make_httpx_client() as client:
        client.headers.update({"User-Agent": crawler._random_ua()})
        for code, q in keyword_pairs:
            log.info("[%s] 검색: %s (%s)", site, q, code)
            try:
                stubs_w_url = await search_fn(client, q)
            except Exception as e:
                log.warning("  search 실패 %s: %s", q, e)
                stubs_w_url = []
            stubs = [s for _, s in stubs_w_url]
            if not stubs:
                per_keyword_saved[f"{code}/{q}"] = 0
                continue

            detail_vocs = await _fetch_with_crawler(crawler, client, stubs)
            log.info("  → detail %d건 추출", len(detail_vocs))
            all_raw.extend(detail_vocs)
            per_keyword_saved[f"{code}/{q}"] = len(detail_vocs)

    # 중복 제거 (external_id)
    seen: set = set()
    unique: List[RawVOC] = []
    for v in all_raw:
        if v.external_id in seen:
            continue
        seen.add(v.external_id)
        unique.append(v)
    log.info("[%s] dedup: %d → %d", site, len(all_raw), len(unique))

    # NLP + DB 저장 (BaseCrawler.run 의 후반부만 직접 호출)
    if not unique:
        return per_keyword_saved

    from nlp.pipeline import process_voc_list  # noqa: E402
    std_vocs = [crawler.normalize(r) for r in unique]
    processed = await process_voc_list(std_vocs)
    saved = await crawler.save(processed)
    log.info("[%s] 신규 저장: %d / %d", site, saved, len(processed))
    per_keyword_saved["__saved__"] = saved
    per_keyword_saved["__processed__"] = len(processed)
    return per_keyword_saved


async def main():
    if not os.getenv("DATABASE_URL"):
        log.error("DATABASE_URL 미설정")
        sys.exit(2)

    log.info("=== Crisis KR Backfill 시작 — sites=%s per_query=%d pages=%d ===",
             SITES, PER_QUERY_LIMIT, MAX_PAGES)
    before = _crisis_counts()
    log.info("[before] %s", before)

    t0 = time.time()
    results: Dict[str, Dict[str, int]] = {}
    for site in SITES:
        site = site.strip()
        if not site:
            continue
        ts = time.time()
        try:
            r = await run_site(site)
            results[site] = r
            log.info("[%s] 완료 (%ds)", site, int(time.time() - ts))
        except Exception as e:
            log.exception("[%s] 실패: %s", site, e)
            results[site] = {"__error__": -1}

    after = _crisis_counts()
    log.info("=== Crisis KR Backfill 종료 (%ds) ===", int(time.time() - t0))
    log.info("[after]  %s", after)
    for code in sorted(set(before) | set(after)):
        b = before.get(code, (0, 0))
        a = after.get(code, (0, 0))
        log.info("  %s: total %d → %d (+%d) | neg %d → %d (+%d)",
                 code, b[0], a[0], a[0] - b[0], b[1], a[1], a[1] - b[1])
    log.info("[per-keyword] %s", results)


if __name__ == "__main__":
    asyncio.run(main())
