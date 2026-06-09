"""archive.org Wayback CDX 기반 한국 사이트 옛 글 백카탈로그.

R14 Track B — 2018-2022 기간 한국 커뮤니티 옛 글을 Wayback Machine 에서
수집해 옛 모델(S8/S9/S10/Note8/Note9/Fold/Flip1/2) 매칭 데이터를 보강한다.

원리:
  1. CDX API 로 도메인 × 연도별 (timestamp, original_url) 쌍 수집
  2. dry-run 모드 (기본): URL 수만 카운트, fetch 없음
  3. fetch 모드 (--fetch): Wayback snapshot 다운로드 → 사이트별 parser 재사용
                          → BaseCrawler.save() 로 멱등 적재

대상 도메인 (Discovery 결과 기반):
  - clien      (clien.net/service/board/cm_andro/*) — 검증 완료
  - dcinside   (gall.dcinside.com/mgallery/board/lists)
  - ppomppu    (m.ppomppu.co.kr/zboard)
  - fmkorea    (www.fmkorea.com)

한계:
  - dcinside / ppomppu / fmkorea 는 CDX 에 list 페이지만 인덱싱된 경우가 많아
    개별 post URL 수집이 제한적. clien 만 안정적으로 post URL 수집 가능.
  - Wayback fetch 는 1-3 req/s 권장 (예의), 대량 fetch 는 시간 소요 큼.
  - 한국 사이트 동적 페이지는 archive 가 일부만 캡처 — 댓글/본문 누락 가능.

환경변수:
  DATABASE_URL              (fetch 모드에서 필수)
  WAYBACK_FROM_YEAR         기본 2018
  WAYBACK_TO_YEAR           기본 2022
  WAYBACK_PER_QUERY_LIMIT   기본 100  (CDX 각 query 결과 상한)
  WAYBACK_FETCH_LIMIT       기본 50   (실제 fetch 할 snapshot 수, --fetch 모드)
  WAYBACK_SITES             기본 "clien"  (쉼표 구분)
  WAYBACK_USER_AGENT        기본 SignalForge-Wayback-Backfill/1.0

실행:
  # dry-run (URL 수 측정만)
  cd crawler && DATABASE_URL=... .venv/bin/python scripts/wayback_kr_backfill.py

  # 시범 fetch (50 snapshot, CDX 경유)
  cd crawler && DATABASE_URL=... .venv/bin/python scripts/wayback_kr_backfill.py --fetch

  # CDX API 가 503 등으로 죽었을 때: 시드 파일에서 (timestamp,original_url) 직접 fetch
  # 파일 형식: 라인당 "TIMESTAMP,ORIGINAL_URL" (#로 주석 가능)
  cd crawler && DATABASE_URL=... .venv/bin/python scripts/wayback_kr_backfill.py \
      --fetch --seed-urls scripts/wayback_seed_clien.csv
"""
import argparse
import asyncio
import hashlib
import logging
import os
import sys
import time
from typing import List, Optional, Tuple

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wayback_kr")

CDX_BASE = "http://web.archive.org/cdx/search/cdx"
WB_BASE = "http://web.archive.org/web"

FROM_YEAR = int(os.getenv("WAYBACK_FROM_YEAR", "2018"))
TO_YEAR = int(os.getenv("WAYBACK_TO_YEAR", "2022"))
PER_QUERY_LIMIT = int(os.getenv("WAYBACK_PER_QUERY_LIMIT", "100"))
FETCH_LIMIT = int(os.getenv("WAYBACK_FETCH_LIMIT", "50"))
USER_AGENT = os.getenv("WAYBACK_USER_AGENT", "SignalForge-Wayback-Backfill/1.0")

# 사이트별 CDX 질의 URL 패턴 (* = 모든 하위 path)
# Discovery 결과: clien 만 post URL 인덱싱 풍부. 나머지는 list 페이지 위주.
SITE_QUERIES = {
    "clien":    "clien.net/service/board/cm_andro/*",
    "dcinside": "gall.dcinside.com/mgallery/board/lists",
    "ppomppu":  "m.ppomppu.co.kr/zboard",
    "fmkorea":  "www.fmkorea.com",
}


async def _cdx_query(
    client: httpx.AsyncClient, url_pattern: str, year: int, limit: int
) -> List[Tuple[str, str]]:
    """CDX API 1회 질의 → [(timestamp, original_url), ...]."""
    params = {
        "url": url_pattern,
        "output": "json",
        "from": f"{year}0101",
        "to": f"{year}1231",
        "filter": "statuscode:200",
        "limit": str(limit),
        "collapse": "urlkey",  # urlkey 중복 제거 — 동일 URL 의 여러 snapshot 중 첫 것만
    }
    try:
        r = await client.get(CDX_BASE, params=params, timeout=60.0)
        r.raise_for_status()
        rows = r.json()
        if not rows or len(rows) <= 1:
            return []
        # rows[0] 는 header. (timestamp=1, original=2)
        return [(row[1], row[2]) for row in rows[1:]]
    except Exception as e:
        log.warning("  CDX 실패 %s (%d): %s", url_pattern, year, e)
        return []


def _build_wayback_url(timestamp: str, original: str) -> str:
    return f"{WB_BASE}/{timestamp}/{original}"


async def _fetch_snapshot(
    client: httpx.AsyncClient, timestamp: str, original: str
) -> Optional[str]:
    url = _build_wayback_url(timestamp, original)
    try:
        r = await client.get(url, timeout=30.0, follow_redirects=True)
        if r.status_code != 200:
            return None
        return r.text
    except Exception as e:
        log.debug("  fetch 실패 %s: %s", url, e)
        return None


def _parse_clien_snapshot(html: str, source_url: str):
    """archived clien 페이지 HTML → (body_voc, comment_vocs) 추출.

    실시간 ClienCrawler._fetch_post_detail 와 동일 selector 사용.
    실시간과 차이: BeautifulSoup parsing 만, 네트워크 X.
    원 글 작성일은 .post_author 첫번째 'YYYY-MM-DD HH:MM:SS' 패턴에서 추출
    (archive timestamp 가 아니라 본문에 박혀있는 실제 글 시각).
    """
    import re
    from datetime import datetime, timezone, timedelta
    from bs4 import BeautifulSoup
    from base.crawler import RawVOC

    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one(".post_subject") or soup.select_one(".post_title")
    title = title_el.get_text(strip=True) if title_el else ""
    body_el = soup.select_one(".post_content") or soup.select_one(".post_article")
    body_text = body_el.get_text("\n", strip=True) if body_el else ""

    # 원 글 시각 추출 (KST → UTC)
    published_at = None
    author_el = soup.select_one(".post_author")
    if author_el:
        m = re.search(r"\b(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\b", author_el.get_text(" ", strip=True))
        if m:
            try:
                kst = timezone(timedelta(hours=9))
                published_at = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") \
                    .replace(tzinfo=kst).astimezone(timezone.utc)
            except Exception:
                pass

    body_voc = RawVOC(
        external_id=hashlib.md5(source_url.encode()).hexdigest()[:16],
        content=f"{title}\n{body_text}".strip(),
        source_url=source_url,
        author_name=None,
        published_at=published_at,
        country_code="KR",
    )

    comments: List = []
    for idx, row in enumerate(soup.select(".comment_row")):
        row_classes = row.get("class") or []
        if "blocked" in row_classes or "deleted" in row_classes:
            continue
        view_el = row.select_one(".comment_view")
        if not view_el:
            continue
        ctext = view_el.get_text("\n", strip=True)
        if not ctext or len(ctext) < 5:
            continue
        csn = row.get("data-comment-sn") or f"i{idx}"
        comments.append(RawVOC(
            external_id=hashlib.md5(f"{source_url}#c{csn}".encode()).hexdigest()[:16],
            content=ctext,
            source_url=source_url,
            country_code="KR",
        ))

    return body_voc, comments


async def _dry_run(client: httpx.AsyncClient, sites: List[str]) -> dict:
    """fetch 없이 사이트 × 연도별 URL 수만 측정."""
    summary = {}
    for site in sites:
        pattern = SITE_QUERIES[site]
        per_year = {}
        total = 0
        for year in range(FROM_YEAR, TO_YEAR + 1):
            rows = await _cdx_query(client, pattern, year, PER_QUERY_LIMIT)
            per_year[year] = len(rows)
            total += len(rows)
            log.info("  [dry] %-9s %d: %d URL", site, year, len(rows))
            await asyncio.sleep(1.0)  # CDX 예의
        summary[site] = {"per_year": per_year, "total": total}
        log.info("  [dry] %s 합계: %d URL", site, total)
    return summary


def _load_seed_urls(path: str) -> List[Tuple[str, str]]:
    """시드 CSV 파일에서 (timestamp, original_url) 로드.

    형식: 라인당 "TIMESTAMP,ORIGINAL_URL", '#' 시작 라인은 주석.
    """
    out: List[Tuple[str, str]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = [p.strip() for p in s.split(",", 1)]
            if len(parts) != 2 or not parts[0].isdigit():
                log.warning("  seed 잘못된 라인 skip: %s", s[:80])
                continue
            out.append((parts[0], parts[1]))
    return out


async def _fetch_and_save(
    client: httpx.AsyncClient, sites: List[str], limit: int,
    seed_urls: Optional[List[Tuple[str, str]]] = None,
) -> dict:
    """clien 시범 fetch + parse + save. 다른 사이트는 parser 부재로 skip 보고.

    seed_urls 가 주어지면 CDX 건너뛰고 해당 URL 들을 직접 fetch.
    """
    from platforms.clien import ClienCrawler

    results = {}
    for site in sites:
        if site != "clien":
            log.info("  [fetch] %s: parser 미구현 — skip (dry-run 으로 URL 수만 확인)", site)
            results[site] = {"fetched": 0, "saved": 0, "skipped": True}
            continue

        if seed_urls:
            rows = seed_urls
            log.info("  [fetch] clien seed: %d URL (CDX 우회)", len(rows))
        else:
            # CDX 로 시범 URL 수집 (2020년만, limit 만큼)
            rows = await _cdx_query(client, SITE_QUERIES[site], 2020, limit)
            log.info("  [fetch] clien 2020: %d URL 수집", len(rows))
        if not rows:
            results[site] = {"fetched": 0, "saved": 0}
            continue

        # individual post URL 만 (URL path 에 숫자 ID 포함) 필터
        post_rows = [
            (ts, url) for ts, url in rows
            if "/cm_andro/" in url and url.rstrip("/").split("/")[-1].split("?")[0].isdigit()
        ]
        log.info("  [fetch] post URL %d (list 페이지 제외)", len(post_rows))

        # parse 후 BaseCrawler.save() 사용을 위해 ClienCrawler instance 생성
        crawler = ClienCrawler()
        all_vocs = []
        fetched = 0
        for ts, original in post_rows[:limit]:
            html = await _fetch_snapshot(client, ts, original)
            if not html:
                continue
            fetched += 1
            try:
                body_voc, comment_vocs = _parse_clien_snapshot(html, original)
                # 본문 30자 미만이면 skip
                if len(body_voc.content) >= 30:
                    all_vocs.append(body_voc)
                all_vocs.extend(comment_vocs)
            except Exception as e:
                log.debug("  parse 실패 %s: %s", original, e)
            await asyncio.sleep(1.5)  # archive.org 예의 (1 req/1.5s)

        log.info("  [fetch] %d snapshot → %d voc 추출", fetched, len(all_vocs))

        # normalize + NLP 처리 + save
        saved = 0
        if all_vocs:
            try:
                from nlp.pipeline import process_voc_list
                std = [crawler.normalize(v) for v in all_vocs]
                processed = await process_voc_list(std)
                saved = await crawler.save(processed)
                log.info("  [fetch] 신규 저장: %d", saved)
            except Exception as e:
                log.warning("  save 실패: %s", e)

        results[site] = {"fetched": fetched, "voc": len(all_vocs), "saved": saved}
    return results


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true",
                        help="실제 snapshot fetch + DB 저장 (기본은 dry-run)")
    parser.add_argument("--limit", type=int, default=FETCH_LIMIT,
                        help=f"fetch snapshot 수 상한 (기본 {FETCH_LIMIT})")
    parser.add_argument("--seed-urls", type=str, default=None,
                        help="CDX 대신 시드 CSV (timestamp,original_url) 에서 URL 로드")
    parser.add_argument("--skip-dry-run", action="store_true",
                        help="dry-run CDX 측정 건너뜀 (CDX 죽었을 때)")
    args = parser.parse_args()

    sites_env = os.getenv("WAYBACK_SITES", "clien").strip()
    sites = [s.strip() for s in sites_env.split(",") if s.strip() in SITE_QUERIES]
    if not sites:
        log.error("WAYBACK_SITES 잘못됨. 가능: %s", list(SITE_QUERIES))
        sys.exit(2)

    if args.fetch and not os.getenv("DATABASE_URL"):
        log.error("--fetch 모드에는 DATABASE_URL 필수")
        sys.exit(2)

    log.info("=== Wayback CDX 백카탈로그 시작 ===")
    log.info("  mode=%s sites=%s years=%d-%d per_query=%d",
             "fetch" if args.fetch else "dry-run",
             sites, FROM_YEAR, TO_YEAR, PER_QUERY_LIMIT)

    seed_urls = None
    if args.seed_urls:
        seed_urls = _load_seed_urls(args.seed_urls)
        log.info("  seed-urls=%s → %d 라인 로드", args.seed_urls, len(seed_urls))

    t0 = time.time()
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        if not args.skip_dry_run:
            # dry-run 으로 URL 수 측정
            summary = await _dry_run(client, sites)
            log.info("=== dry-run 합계 ===")
            for s, info in summary.items():
                log.info("  %s: %d URL (yearly=%s)", s, info["total"], info["per_year"])
        else:
            log.info("  --skip-dry-run: CDX 측정 건너뜀")

        if args.fetch:
            log.info("=== 시범 fetch 시작 (limit=%d, seed=%s) ===",
                     args.limit, "yes" if seed_urls else "no")
            fetch_res = await _fetch_and_save(
                client, sites, args.limit, seed_urls=seed_urls
            )
            log.info("=== fetch 결과 ===")
            for s, info in fetch_res.items():
                log.info("  %s: %s", s, info)

    log.info("=== 종료 (%ds) ===", int(time.time() - t0))


if __name__ == "__main__":
    asyncio.run(main())
