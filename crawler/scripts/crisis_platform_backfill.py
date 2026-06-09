"""R20 트랙 B — Crisis 한국 사이트 voc 보강 (확장 키워드 사전).

배경
====
R19 완료 시 5 위기 운영 endpoint (운영 환경 GS22U 2 / GZFL3 8 / GS20 38)은
v2 백필 이후로도 한국 사이트 voc 가 0건. crisis 기간 voc 가 거의 HN 단일 소스에
편중되어 있어 한국 커뮤니티 신호 (게옵스 사태, 플립 힌지 등)를 놓치고 있다.

본 스크립트는 ``crisis_backfill_v2.py`` 의 search 인프라(bobaedream, ruliweb,
fmkorea + HN)에 **MLB Park 검색** 을 추가하고 R19 Discovery 산출물 기반으로
키워드를 한 단계 확장한다.

전략
====
1. 사이트별 search endpoint 재사용:
   - dcinside / ppomppu  (crisis_kr_backfill v1 의 dc_search_urls, ppomppu_search_urls)
   - bobaedream / ruliweb / fmkorea  (crisis_backfill_v2 의 bobae_search, ruli_search, fmk_search)
   - mlbpark — 본 스크립트에서 신규 추가 (bullpen 검색)
2. 키워드 매트릭스 R20 — 위기별 long-tail / 모델명 / 영문 동치어 보강
3. BaseCrawler.normalize() + NLP + save() 멱등 적재
4. DRY_RUN + PRESERVE_EXISTING 기본값 — 검증 후 실행 시점에 끄도록

환경변수
========
DATABASE_URL                    필수 (DRY_RUN=1 일 때만 선택)
CPB_PER_QUERY_LIMIT             기본 20  검색 결과 첫 N건만 상세 수집
CPB_MAX_PAGES                   기본 3   각 검색어 페이지 수
CPB_SITES                       기본 "mlbpark,bobaedream,ruliweb"
                                 (dc/ppomppu/fmk 는 옵션 — 이미 v1/v2 커버)
CPB_DRY_RUN                     기본 '1' — search/parse 까지만, save() 스킵
CPB_PRESERVE_EXISTING           기본 '1' — 기존 voc external_id 보존 (BaseCrawler.save
                                는 ON CONFLICT DO NOTHING 이므로 자동 보존)
CPB_SAVE_PER_SITE               기본 '1' — 사이트별 직후 save

검증
====
- crawler/tests/test_crisis_platform.py — KR_QUERIES_V3 카버리지 + mlbpark search url
- 실행 후 psql 로 crisis 기간 voc 변화 측정 (before/after)

산출
====
- 신규 voc + Crisis 변화 + 사이트별 분포 (실행 로그)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import subprocess
import sys
import time
import urllib.parse
from typing import Dict, List, Tuple

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC  # noqa: E402

# v2/v1 search 인프라 재사용
from scripts.crisis_backfill_v2 import (  # noqa: E402
    bobae_search,
    ruli_search,
    fmk_search,
    _fetch_details,
    _save_via_crawler as _v2_save_via_crawler,
)
from scripts.crisis_kr_backfill import (  # noqa: E402
    dc_search_urls,
    ppomppu_search_urls,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("crisis_platform")


# ─────────────────────────── 환경변수 ───────────────────────────
PER_QUERY_LIMIT = int(os.getenv("CPB_PER_QUERY_LIMIT", "20"))
MAX_PAGES = int(os.getenv("CPB_MAX_PAGES", "3"))
SITES = [s.strip() for s in os.getenv(
    "CPB_SITES", "mlbpark,bobaedream,ruliweb"
).split(",") if s.strip()]
DRY_RUN = os.getenv("CPB_DRY_RUN", "1") == "1"
PRESERVE_EXISTING = os.getenv("CPB_PRESERVE_EXISTING", "1") == "1"
SAVE_PER_SITE = os.getenv("CPB_SAVE_PER_SITE", "1") == "1"


# ─────────────────────────── 키워드 매트릭스 R20 ───────────────────────────
# v2 KR_QUERIES 기반으로 long-tail 신규 키워드 추가 (Discovery 결과)
#
# 추가 원칙
#  - 모델명 영문/한글 동치어 (Note 7 / 노트7 / 갤노트7)
#  - 위기 사건 별칭 (게옵스 사태, 폴드 들뜸, 노트7 발화 동영상)
#  - 부작용 발견을 위한 결함 키워드 (불량, 결함, 액정, 깨짐, 발열)
KR_QUERIES_V3: Dict[str, List[str]] = {
    # GN7 (Note 7 발화·리콜, 2016)
    "GN7": [
        "노트7 발화",            # v2 root
        "Note7 발화",            # 영문 모델명
        "갤노트7 폭발",           # 한국 일반인 검색 패턴
        "노트7 리콜",
        "Note7 리콜",
        "갤럭시 노트7 단종",     # 단종 이슈
        "노트7 배터리 결함",     # 결함 키워드
    ],
    # GZF1 (Fold 1 액정 들뜸, 2019)
    "GZF1": [
        "폴드1 액정",            # v2 root
        "갤럭시 폴드 들뜸",      # 들뜸 사건 별칭
        "갤폴드 화면 불량",       # 일반인 검색
        "Galaxy Fold 결함",
        "폴드 화면 분리",         # 분리 현상 키워드
        "폴드1 리뷰 사고",        # 리뷰어 사건
    ],
    # GS22U (게임 최적화 게옵스 사태, 2022 — 2건뿐 → 강화)
    "GS22U": [
        "게옵스",                # v2 root (단독)
        "GoS 사태",
        "S22 게옵스",
        "S22 GOS",
        "갤럭시 S22 GOS",
        "게임 최적화 서비스",
        "S22 throttling",        # 영문 동치어
        "S22 발열",              # 동시 이슈
        "S22 성능 저하",
        "GOS 적용 기기",          # 일반인 검색 패턴
    ],
    # GZFL3 (Z Flip 3 힌지, 2021 — 8건뿐 → 강화)
    "GZFL3": [
        "플립3 힌지",            # v2 root
        "플립3 액정",
        "Z Flip 3 힌지",
        "Z플립3 깨짐",
        "갤럭시 플립3 결함",
        "플립3 내구성",
        "플립3 화면 깨짐",
        "Z Flip 3 broken",
        "Flip3 hinge",
    ],
    # GS20 (S20 가격·5G 이슈, 2020)
    "GS20": [
        "S20 가격",              # v2 root
        "S20 5G",
        "갤럭시 S20 단점",
        "S20 출시가",
        "갤럭시 S20 발열",
        "S20 카메라 결함",        # 카메라 이슈
    ],
}


# ═══════════════════════════════════════════════════════════════════════
# MLB Park 검색 — 신규 (R20)
# ═══════════════════════════════════════════════════════════════════════
MLBPARK_BASE = "https://mlbpark.donga.com"


def _mlbpark_build_search_url(keyword: str, p: int) -> str:
    """MLB Park bullpen 검색 URL — select=sct (제목+본문).

    p 는 1, 31, 61, ... (30 step). 본 함수는 1-base page 를 받아 변환.
    """
    p_offset = 1 + (p - 1) * 30
    q = urllib.parse.quote(keyword)
    return (
        f"{MLBPARK_BASE}/mp/b.php?b=bullpen&select=sct&query={q}"
        f"&m=search&p={p_offset}"
    )


async def mlbpark_search(client: httpx.AsyncClient, keyword: str) -> List[RawVOC]:
    """MLB Park 검색 → list-level RawVOC stub 추출.

    MLBParkCrawler._parse_search_list 를 재사용해서 검색 결과 row 를
    그대로 RawVOC 로 변환한다 (title / 작성자 / 댓글수 포함).
    """
    from platforms.mlbpark import MLBParkCrawler  # noqa: E402
    crawler = MLBParkCrawler()
    stubs: List[RawVOC] = []
    seen_ids: set = set()

    for page in range(1, MAX_PAGES + 1):
        url = _mlbpark_build_search_url(keyword, page)
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                log.debug("  mlbpark '%s' p%d: status=%d", keyword, page, resp.status_code)
                continue
            rows = crawler._parse_search_list(resp.text)
            added = 0
            for r in rows:
                if r.external_id in seen_ids:
                    continue
                seen_ids.add(r.external_id)
                stubs.append(r)
                added += 1
            log.info("  mlbpark '%s' p%d: +%d (총 %d)", keyword, page, added, len(stubs))
            if added == 0:
                break
            await asyncio.sleep(1.5)
        except Exception as e:
            log.warning("  mlbpark '%s' p%d 실패: %s", keyword, page, e)
            break

    return stubs


# ═══════════════════════════════════════════════════════════════════════
# Save wrapper — PRESERVE_EXISTING 가이드 강화
# ═══════════════════════════════════════════════════════════════════════
async def _save_via_crawler(crawler, raw: List[RawVOC]) -> Dict[str, int]:
    """BaseCrawler.normalize() + NLP + save() — 멱등.

    PRESERVE_EXISTING=1 (기본): BaseCrawler.save 는 ON CONFLICT DO NOTHING 이라
    기존 external_id 는 자동 보존됨. 추가 가드 없음 (확인용 변수만 로그).
    """
    if not raw or DRY_RUN:
        return {"saved": 0, "processed": 0, "dry_run": int(DRY_RUN),
                "preserve_existing": int(PRESERVE_EXISTING)}
    return await _v2_save_via_crawler(crawler, raw)


# ═══════════════════════════════════════════════════════════════════════
# 사이트별 실행 — 검색 + 상세 + (옵션)저장
# ═══════════════════════════════════════════════════════════════════════
async def run_dcinside() -> Dict:
    from platforms.dcinside import DCInsideCrawler  # noqa: E402
    crawler = DCInsideCrawler()
    all_raw: List[RawVOC] = []
    kw_counts: Dict[str, int] = {}
    async with crawler._make_httpx_client() as client:
        client.headers.update({"User-Agent": crawler._random_ua()})
        for code, queries in KR_QUERIES_V3.items():
            for q in queries:
                log.info("[dc] 검색: %s (%s)", q, code)
                try:
                    pairs = await dc_search_urls(client, q)
                except Exception as e:
                    log.warning("  dc '%s' 실패: %s", q, e)
                    kw_counts[f"{code}/{q}"] = 0
                    continue
                stubs = [s for _, s in pairs]
                if not stubs:
                    kw_counts[f"{code}/{q}"] = 0
                    continue
                detail = await _fetch_details(crawler, client, stubs)
                log.info("  → detail %d건", len(detail))
                kw_counts[f"{code}/{q}"] = len(detail)
                all_raw.extend(detail)
    saved_info = await _save_via_crawler(crawler, all_raw) if SAVE_PER_SITE else {"deferred": len(all_raw)}
    return {"raw_count": len(all_raw), "per_keyword": kw_counts, "save": saved_info, "raw": all_raw}


async def run_ppomppu() -> Dict:
    from platforms.ppomppu import PpomppuCrawler  # noqa: E402
    crawler = PpomppuCrawler()
    all_raw: List[RawVOC] = []
    kw_counts: Dict[str, int] = {}
    async with crawler._make_httpx_client() as client:
        client.headers.update({"User-Agent": crawler._random_ua()})
        for code, queries in KR_QUERIES_V3.items():
            for q in queries:
                log.info("[pp] 검색: %s (%s)", q, code)
                try:
                    pairs = await ppomppu_search_urls(client, q)
                except Exception as e:
                    log.warning("  pp '%s' 실패: %s", q, e)
                    kw_counts[f"{code}/{q}"] = 0
                    continue
                stubs = [s for _, s in pairs]
                if not stubs:
                    kw_counts[f"{code}/{q}"] = 0
                    continue
                detail = await _fetch_details(crawler, client, stubs)
                log.info("  → detail %d건", len(detail))
                kw_counts[f"{code}/{q}"] = len(detail)
                all_raw.extend(detail)
    saved_info = await _save_via_crawler(crawler, all_raw) if SAVE_PER_SITE else {"deferred": len(all_raw)}
    return {"raw_count": len(all_raw), "per_keyword": kw_counts, "save": saved_info, "raw": all_raw}


async def run_bobae() -> Dict:
    from platforms.bobaedream import BobaeDreamCrawler  # noqa: E402
    crawler = BobaeDreamCrawler()
    all_raw: List[RawVOC] = []
    kw_counts: Dict[str, int] = {}
    async with crawler._make_httpx_client() as client:
        client.headers.update({"User-Agent": crawler._random_ua()})
        for code, queries in KR_QUERIES_V3.items():
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
        for code, queries in KR_QUERIES_V3.items():
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
        for code, queries in KR_QUERIES_V3.items():
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


async def run_mlbpark() -> Dict:
    from platforms.mlbpark import MLBParkCrawler  # noqa: E402
    crawler = MLBParkCrawler()
    all_raw: List[RawVOC] = []
    kw_counts: Dict[str, int] = {}
    async with crawler._make_httpx_client() as client:
        client.headers.update({"User-Agent": crawler._random_ua()})
        for code, queries in KR_QUERIES_V3.items():
            for q in queries:
                log.info("[mlbpark] 검색: %s (%s)", q, code)
                stubs = await mlbpark_search(client, q)
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
    "dcinside":   run_dcinside,
    "ppomppu":    run_ppomppu,
    "bobaedream": run_bobae,
    "ruliweb":    run_ruli,
    "fmkorea":    run_fmk,
    "mlbpark":    run_mlbpark,
}


# ═══════════════════════════════════════════════════════════════════════
# DB counter (before/after)
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


CRISIS_WINDOW_SQL = """
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


def _crisis_counts() -> Dict[str, Tuple[int, int]]:
    out: Dict[str, Tuple[int, int]] = {}
    for line in _psql(CRISIS_WINDOW_SQL).splitlines():
        parts = line.split("|")
        if len(parts) >= 3:
            try:
                out[parts[0]] = (int(parts[1]), int(parts[2]))
            except ValueError:
                pass
    return out


CRISIS_BY_PLATFORM_SQL = """
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
    for line in _psql(CRISIS_BY_PLATFORM_SQL).splitlines():
        parts = line.split("|")
        if len(parts) >= 2:
            try:
                out.append((parts[0], int(parts[1])))
            except ValueError:
                pass
    return out


# ═══════════════════════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════════════════════
async def main():
    if not os.getenv("DATABASE_URL") and not DRY_RUN:
        log.error("DATABASE_URL 미설정 (DRY_RUN=1 로 검색만 시도하려면 환경변수 OK)")
        sys.exit(2)

    log.info("=== Crisis Platform Backfill R20 시작 ===")
    log.info("  sites=%s per_query=%d pages=%d dry_run=%s preserve=%s save_per_site=%s",
             SITES, PER_QUERY_LIMIT, MAX_PAGES, DRY_RUN, PRESERVE_EXISTING, SAVE_PER_SITE)
    log.info("  keywords: %d codes / %d queries",
             len(KR_QUERIES_V3),
             sum(len(v) for v in KR_QUERIES_V3.values()))

    before = _crisis_counts() if not DRY_RUN else {}
    before_plat = _crisis_by_platform() if not DRY_RUN else []
    if not DRY_RUN:
        log.info("[before] %s", before)
        log.info("[platform before] %s", before_plat)

    t0 = time.time()
    results: Dict[str, Dict] = {}

    for site in SITES:
        runner = SITE_RUNNERS.get(site)
        if not runner:
            log.warning("  unknown site: %s — skip", site)
            continue
        ts = time.time()
        try:
            r = await runner()
            summary = {k: v for k, v in r.items() if k != "raw"}
            results[site] = summary
            log.info("[%s] 완료 (%ds) — raw=%d", site, int(time.time() - ts), r["raw_count"])
        except Exception as e:
            log.exception("[%s] 실패: %s", site, e)
            results[site] = {"error": str(e)}

    elapsed = int(time.time() - t0)
    log.info("=== Crisis Platform Backfill R20 종료 (%ds) ===", elapsed)

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
