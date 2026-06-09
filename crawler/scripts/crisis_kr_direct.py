"""R24 트랙 D — Crisis 한국어 검색어 직접 본런 (clien / dcinside).

배경
====
R22~R23 의 Crisis Direct 본런은 모두 영문 매체 4개 (9to5google / engadget /
theverge / androidcentral) 만 다뤘다.  Discovery (R24) 가 식별한 한국어
Crisis 키워드 풀과 사이트별 검색 엔드포인트를 사용해 *한국* 사이트에서도
Crisis 본런을 직접 실행한다.

R18 트랙 C 의 ``crisis_kr_backfill.py`` 는 dcinside 갤러리 *목록* 페이지를
``s_keyword`` 로 직접 필터하는 *갤러리 내부 검색* 만 다뤘다.  본 R24 스크립트는

  * Clien 의 *사이트 통합 검색* (``service/search?q=<kw>``)
  * DCInside 의 *전체 갤러리 통합 검색* (``search.dcinside.com/post/q/<kw>``)

두 엔드포인트를 사용해 갤러리/게시판 경계를 넘는 Crisis 키워드 매칭을
시도한다.  검색 결과 → post URL → 기존 ``ClienCrawler._fetch_post_detail``
/ ``DCInsideCrawler._fetch_post_detail`` 재사용으로 본문/댓글 파싱 + RawVOC
생성을 위임한다 (새 파서 작성 불필요).

안전 장치
=========
- ``CKD_DRY_RUN=1`` (기본): 검색 URL 추출까지만, fetch + save 스킵
- ``CKD_PRESERVE_EXISTING=1`` (기본): BaseCrawler.save 의 ON CONFLICT DO
  NOTHING 으로 기존 voc external_id 자동 보존
- 실행 1회당 ``reports/backfill_audit.jsonl`` 1줄 append (round=R24)

환경변수
========
DATABASE_URL                  필수 (NLP pipeline + DB 적재)
CKD_DRY_RUN                   기본 '1' — 검색까지만, fetch/save 스킵
CKD_PRESERVE_EXISTING         기본 '1' — 기존 voc 보존
CKD_PER_KEYWORD_MAX           기본 8   — keyword 1개당 fetch 할 post 상한
CKD_MAX_PAGES                 기본 2   — 검색 페이지 수
CKD_SITES                     기본 'clien,dcinside'  (콤마 구분, 'all' 도 지원)
CKD_CYCLES                    기본 2   — 사이트별 cycle 수 (페이지 반복)
CKD_AUDIT_ROUND               기본 'R24' — audit JSONL round 라벨

검증
====
- crawler/tests/test_crisis_kr_direct.py — 검색 URL 빌드 + URL 추출 단위

산출
====
- Crisis 5건 * 한국 키워드 (Discovery R24) → clien/dcinside post URL
- DRY_RUN=0 본런 시: 신규 voc + Crisis 기간 voc 변화 + 한국 사이트 voc 증가
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
from datetime import date, datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, quote_plus, urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base.crawler import RawVOC  # noqa: E402
from insight.backfill_audit import record_run  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("crisis_kr_direct")


# ─────────────────────────── 환경 ───────────────────────────
DRY_RUN = os.getenv("CKD_DRY_RUN", "1") == "1"
PRESERVE_EXISTING = os.getenv("CKD_PRESERVE_EXISTING", "1") == "1"
PER_KEYWORD_MAX = int(os.getenv("CKD_PER_KEYWORD_MAX", "8"))
MAX_PAGES = int(os.getenv("CKD_MAX_PAGES", "2"))
SITES_ARG = os.getenv("CKD_SITES", "clien,dcinside").strip().lower()
CYCLES = int(os.getenv("CKD_CYCLES", "2"))
AUDIT_ROUND = os.getenv("CKD_AUDIT_ROUND", "R24")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
)

# ─────────────────────────── crisis window + 키워드 ───────────────────────────
CRISIS_WINDOWS: Dict[str, Tuple[date, date]] = {
    "GN7":   (date(2016, 8, 19), date(2016, 12, 31)),
    "GZF1":  (date(2019, 4, 15), date(2019, 12, 31)),
    "GS22U": (date(2022, 2, 25), date(2022, 6, 30)),
    "GZFL3": (date(2021, 8, 1),  date(2022, 3, 31)),
    "GS20":  (date(2020, 2, 1),  date(2020, 12, 31)),
}

# Discovery R24 결과 — 한국어 Crisis 키워드 (Crisis 5건 x ~5개)
CRISIS_KR_KEYWORDS: Dict[str, List[str]] = {
    "GN7": [
        "노트7 발화", "노트7 리콜", "노트7 폭발",
        "갤노트7 사태", "Note7 배터리",
    ],
    "GZF1": [
        "폴드1 결함", "폴드1 액정 들뜸", "갤폴드1 사태",
        "갤럭시 폴드 화면", "Fold1 힌지",
    ],
    "GS22U": [
        "S22 게임옵티마이저", "GoS 사태 보상", "갤S22 GoS",
        "S22 울트라 성능제한", "GoS 집단소송",
    ],
    "GZFL3": [
        "Z 플립3 힌지", "플립3 액정 들뜸", "갤플립3 액정",
        "Flip3 디스플레이", "플립3 보호필름",
    ],
    "GS20": [
        "S20 5G 가격 논란", "갤S20 가격", "S20 카메라 결함",
        "갤S20 발열", "S20 GPU 다운",
    ],
}


def _in_window(d: date, code: str) -> bool:
    s, e = CRISIS_WINDOWS[code]
    return s <= d <= e


# ═════════════════════════════════════════════════════════════════════════
# Clien 통합검색 — service/search?q=<kw>
# ═════════════════════════════════════════════════════════════════════════
_CLIEN_BASE = "https://www.clien.net"
_CLIEN_POST_RE = re.compile(
    r'href="(/service/board/([a-zA-Z_]+)/(\d+))[^"]*"'
)


def _clien_search_url(keyword: str, page: int) -> str:
    """Clien 통합검색 URL — page 0-indexed.

    Discovery 노출 URL:
      service/search?q={kw}&sort=recency&boardCd=&isBoard=false
    페이징은 ``&p={page}``.
    """
    q = quote_plus(keyword)
    if page <= 0:
        return f"{_CLIEN_BASE}/service/search?q={q}&sort=recency&boardCd=&isBoard=false"
    return (
        f"{_CLIEN_BASE}/service/search"
        f"?q={q}&sort=recency&boardCd=&isBoard=false&p={page}"
    )


def _clien_extract_post_urls(html: str) -> List[str]:
    """검색 결과 HTML → ``/service/board/<board>/<id>`` post URL dedupe.

    - 쿼리스트링 / fragment(#comment-point) 제거 → (board, id) 기준 unique
    - 절대 URL 정규화
    """
    seen: set = set()
    out: List[str] = []
    for m in _CLIEN_POST_RE.finditer(html or ""):
        path = m.group(1)   # /service/board/park/19197880
        board = m.group(2)
        post_id = m.group(3)
        key = (board, post_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(f"{_CLIEN_BASE}{path}")
    return out


async def _clien_search_post_urls(
    client: httpx.AsyncClient, crisis_code: str
) -> List[Tuple[str, str]]:
    """Clien crisis 키워드 풀 → [(post_url, keyword), ...]."""
    found: List[Tuple[str, str]] = []
    seen_urls: set = set()
    for kw in CRISIS_KR_KEYWORDS[crisis_code]:
        for page in range(0, MAX_PAGES):
            url = _clien_search_url(kw, page)
            try:
                r = await client.get(url, timeout=20.0)
                if r.status_code != 200:
                    log.warning("  [clien] '%s' p%d status=%d", kw, page, r.status_code)
                    break
                urls = _clien_extract_post_urls(r.text)
                added = 0
                for u in urls:
                    if u in seen_urls:
                        continue
                    seen_urls.add(u)
                    found.append((u, kw))
                    added += 1
                log.info("  [clien] '%s' p%d: +%d (총 %d)",
                         kw, page, added, len(found))
                if added == 0:
                    break
                await asyncio.sleep(1.2)
            except Exception as e:
                log.warning("  [clien] '%s' p%d 실패: %s", kw, page, e)
                break
    return found


# ═════════════════════════════════════════════════════════════════════════
# DCInside 통합검색 — search.dcinside.com/post/q/<kw>
# ═════════════════════════════════════════════════════════════════════════
_DC_BASE = "https://search.dcinside.com"
_DC_GALL_BASE = "https://gall.dcinside.com"
_DC_POST_RE = re.compile(
    r'href="(https://gall\.dcinside\.com/(?:mgallery/)?board/view/?\?id=([^&"]+)&no=(\d+))[^"]*"'
)


def _dc_search_url(keyword: str, page: int) -> str:
    """DCInside 통합검색 URL — page 1-indexed (Discovery 패턴 ``?p={page}``).

    base:  search.dcinside.com/post/q/{kw}
    paging: ?p={page}

    주의: path 세그먼트(``/post/q/<kw>``) 이므로 공백을 ``+`` 로 인코딩하면
    400 Bad Request.  ``%20`` (``quote``) 로 인코딩해야 한다.
    """
    q = quote(keyword, safe="")
    if page <= 1:
        return f"{_DC_BASE}/post/q/{q}"
    return f"{_DC_BASE}/post/q/{q}?p={page}"


def _dc_extract_post_urls(html: str) -> List[str]:
    """검색 결과 HTML → ``gall.dcinside.com/.../board/view`` post URL dedupe.

    - (id, no) 기준 unique
    - mgallery / 일반 board 모두 포함
    - Mustache 템플릿(``{{if ...}}``) 행 제외 (실제 데이터 없음)
    """
    seen: set = set()
    out: List[str] = []
    for m in _DC_POST_RE.finditer(html or ""):
        full = m.group(1)
        gid = m.group(2)
        no = m.group(3)
        if "{{" in full or "}}" in full:
            continue
        key = (gid, no)
        if key in seen:
            continue
        seen.add(key)
        out.append(full)
    return out


async def _dc_search_post_urls(
    client: httpx.AsyncClient, crisis_code: str
) -> List[Tuple[str, str]]:
    found: List[Tuple[str, str]] = []
    seen_urls: set = set()
    for kw in CRISIS_KR_KEYWORDS[crisis_code]:
        for page in range(1, MAX_PAGES + 1):
            url = _dc_search_url(kw, page)
            try:
                r = await client.get(url, timeout=20.0,
                                     headers={"Referer": _DC_BASE + "/"})
                if r.status_code != 200:
                    log.warning("  [dcinside] '%s' p%d status=%d",
                                kw, page, r.status_code)
                    break
                urls = _dc_extract_post_urls(r.text)
                added = 0
                for u in urls:
                    if u in seen_urls:
                        continue
                    seen_urls.add(u)
                    found.append((u, kw))
                    added += 1
                log.info("  [dcinside] '%s' p%d: +%d (총 %d)",
                         kw, page, added, len(found))
                if added == 0:
                    break
                await asyncio.sleep(1.2)
            except Exception as e:
                log.warning("  [dcinside] '%s' p%d 실패: %s",
                            kw, page, e)
                break
    return found


# ═════════════════════════════════════════════════════════════════════════
# 사이트 어댑터 — _fetch_post_detail 재사용
# ═════════════════════════════════════════════════════════════════════════
SearchFn = Callable[[httpx.AsyncClient, str], Awaitable[List[Tuple[str, str]]]]


class SiteAdapter:
    def __init__(
        self,
        code: str,
        crawler_module: str,
        crawler_class: str,
        search_fn: SearchFn,
    ):
        self.code = code
        self.crawler_module = crawler_module
        self.crawler_class = crawler_class
        self.search_fn = search_fn

    def make_crawler(self):
        mod = __import__(self.crawler_module, fromlist=[self.crawler_class])
        return getattr(mod, self.crawler_class)()


SITE_ADAPTERS: Dict[str, SiteAdapter] = {
    "clien": SiteAdapter(
        "clien", "platforms.clien", "ClienCrawler",
        _clien_search_post_urls,
    ),
    "dcinside": SiteAdapter(
        "dcinside", "platforms.dcinside", "DCInsideCrawler",
        _dc_search_post_urls,
    ),
}


def _make_stub(post_url: str, country: str = "KR") -> RawVOC:
    """detail 호출 직전 stub — _fetch_post_detail 가 content 를 덮어쓴다."""
    uid = hashlib.md5(post_url.encode()).hexdigest()[:16]
    return RawVOC(
        external_id=uid,
        content="",
        source_url=post_url,
        author_name=None,
        published_at=None,
        country_code=country,
    )


async def _fetch_post(crawler, client: httpx.AsyncClient,
                      post_url: str) -> List[RawVOC]:
    stub = _make_stub(post_url)
    try:
        return await crawler._fetch_post_detail(client, stub)
    except Exception as e:
        log.debug("  detail 실패 %s: %s", post_url, e)
        return []


def _filter_in_window(vocs: List[RawVOC], crisis_code: str) -> List[RawVOC]:
    """body voc 의 published_at 으로 윈도우 필터.

    detail 결과의 첫 voc 는 body (post 전체) — 같은 source_url 의 voc 들은
    같은 윈도우 판정을 공유.  body 가 윈도우 밖이면 댓글까지 모두 drop.
    """
    by_url: Dict[str, List[RawVOC]] = {}
    for v in vocs:
        by_url.setdefault(v.source_url, []).append(v)

    kept: List[RawVOC] = []
    for url, group in by_url.items():
        # body 식별 — 같은 url md5 prefix
        body_uid = hashlib.md5(url.encode()).hexdigest()[:16]
        body = next((v for v in group if v.external_id == body_uid), group[0])
        pub = body.published_at
        if pub is None:
            # 날짜 미상 → 보수적으로 drop (Crisis 윈도우 보장 불가)
            continue
        d = pub.astimezone(timezone.utc).date() if pub.tzinfo else pub.date()
        if _in_window(d, crisis_code):
            kept.extend(group)
    return kept


# ═════════════════════════════════════════════════════════════════════════
# DB 측정 (영문 트랙과 동일 SQL)
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
# R25 트랙 D — save 전후 voc_records.id 추적 (drift cross-check)
# ═════════════════════════════════════════════════════════════════════════
async def _max_voc_id() -> int:
    """voc_records.id 의 현재 max — save 직전 시점.  실패 시 0."""
    if DRY_RUN or not os.getenv("DATABASE_URL"):
        return 0
    engine = None
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
        if engine is not None:
            try:
                await engine.dispose()
            except Exception:
                pass


async def _query_inserted_ids(
    platform_code: str, external_ids: List[str], pre_max_id: int,
) -> List[int]:
    """save 직후 (platform_code, external_id) AND id > pre_max_id 로
    이번 save 에서 INSERT 된 PK 만 추출."""
    if DRY_RUN or not external_ids or not os.getenv("DATABASE_URL"):
        return []
    engine = None
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
        if engine is not None:
            try:
                await engine.dispose()
            except Exception:
                pass


# ═════════════════════════════════════════════════════════════════════════
# 사이트 1개 × cycle 실행
# ═════════════════════════════════════════════════════════════════════════
async def _run_site(adapter: SiteAdapter, audit) -> None:
    log.info("--- site=%s 시작 (cycles=%d) ---", adapter.code, CYCLES)
    crawler = adapter.make_crawler()

    all_raw: List[RawVOC] = []
    per_code: Dict[str, int] = {}

    async with crawler._make_httpx_client() as client:
        client.headers.update({
            "User-Agent": UA,
            "Accept-Language": "ko,en-US;q=0.7",
        })
        for cycle in range(1, CYCLES + 1):
            log.info("[%s] cycle %d/%d", adapter.code, cycle, CYCLES)
            for code in CRISIS_KR_KEYWORDS:
                pairs = await adapter.search_fn(client, code)
                audit.bump(f"{adapter.code}.matched", len(pairs))
                cap = PER_KEYWORD_MAX * len(CRISIS_KR_KEYWORDS[code])
                targets = pairs[:cap]
                per_code[f"{code}.cycle{cycle}"] = len(targets)

                if DRY_RUN:
                    continue

                for u, _kw in targets:
                    voc_list = await _fetch_post(crawler, client, u)
                    if voc_list:
                        kept = _filter_in_window(voc_list, code)
                        all_raw.extend(kept)
                        audit.bump(f"{adapter.code}.in_window", len(kept))
                        audit.bump(f"{adapter.code}.out_of_window",
                                   len(voc_list) - len(kept))
                        audit.bump(f"{adapter.code}.fetched", 1)
                    else:
                        audit.bump(f"{adapter.code}.fetch_failed", 1)
                    await asyncio.sleep(1.2)

    log.info("[%s] cycles 종료 per_code=%s raw=%d",
             adapter.code, per_code, len(all_raw))
    audit.note(f"{adapter.code} per_code={per_code} raw={len(all_raw)}")

    if DRY_RUN or not all_raw:
        return

    # dedup external_id
    seen: set = set()
    uniq: List[RawVOC] = []
    for v in all_raw:
        if v.external_id in seen:
            continue
        seen.add(v.external_id)
        uniq.append(v)
    log.info("[%s] dedup: %d → %d", adapter.code, len(all_raw), len(uniq))

    from nlp.pipeline import process_voc_list  # noqa: E402
    std = [crawler.normalize(r) for r in uniq]
    processed = await process_voc_list(std)

    # R25 트랙 D — save 전 max(id) snapshot.
    pre_max_id = await _max_voc_id()
    saved = await crawler.save(processed)
    audit.bump(f"{adapter.code}.saved", saved)
    log.info("[%s] → save: %d / %d", adapter.code, saved, len(processed))

    # R25 트랙 D — 신규 INSERT 된 voc PK 캡처 후 audit archive 누적.
    inserted_ids = await _query_inserted_ids(
        crawler.platform_code, [p.external_id for p in processed], pre_max_id,
    )
    audit.add_affected_ids(f"{adapter.code}.voc_inserted", inserted_ids)


# ═════════════════════════════════════════════════════════════════════════
# 메인
# ═════════════════════════════════════════════════════════════════════════
async def main():
    log.info("=== Crisis KR Direct (R24) 시작 ===")
    log.info("  sites=%s dry_run=%s preserve=%s per_kw_max=%d pages=%d cycles=%d round=%s",
             SITES_ARG, DRY_RUN, PRESERVE_EXISTING,
             PER_KEYWORD_MAX, MAX_PAGES, CYCLES, AUDIT_ROUND)

    if not DRY_RUN and not os.getenv("DATABASE_URL"):
        log.error("DATABASE_URL 미설정 (실 save 모드 필수)")
        sys.exit(2)

    if SITES_ARG == "all":
        adapters = list(SITE_ADAPTERS.values())
    else:
        adapters = []
        for s in SITES_ARG.split(","):
            s = s.strip()
            if not s:
                continue
            if s not in SITE_ADAPTERS:
                log.error("미지원 site: %s (지원: %s, all)", s, list(SITE_ADAPTERS))
                sys.exit(2)
            adapters.append(SITE_ADAPTERS[s])

    with record_run(
        script="crisis_kr_direct",
        mode="dry_run" if DRY_RUN else ("preserve" if PRESERVE_EXISTING else "full"),
        env={
            # 표준키 — backfill_audit_monitor 호환
            "DRY_RUN": bool(DRY_RUN),
            "PRESERVE_EXISTING": True,
            "BACKUP_BEFORE": True,
            # 도구별 키
            "CKD_DRY_RUN": int(DRY_RUN),
            "CKD_PRESERVE_EXISTING": int(PRESERVE_EXISTING),
            "CKD_PER_KEYWORD_MAX": PER_KEYWORD_MAX,
            "CKD_MAX_PAGES": MAX_PAGES,
            "CKD_SITES": SITES_ARG,
            "CKD_CYCLES": CYCLES,
            "round": AUDIT_ROUND,
        },
    ) as audit:
        audit.note(f"crisis codes={list(CRISIS_KR_KEYWORDS)} "
                   f"sites={[a.code for a in adapters]} round={AUDIT_ROUND}")

        before = _crisis_by_platform()
        audit.note(f"[before-platform] {before[:10]}")
        log.info("[before-platform] %s", before)

        t0 = time.time()
        for adapter in adapters:
            await _run_site(adapter, audit)

        elapsed = int(time.time() - t0)
        log.info("=== Direct KR 종료 (%ds) ===", elapsed)

        if not DRY_RUN:
            after = _crisis_by_platform()
            log.info("[after-platform] %s", after)
            for adapter in adapters:
                name_map = {"clien": "Clien", "dcinside": "DCInside"}
                target_name = name_map.get(adapter.code, adapter.code)
                b = next((n for nm, n in before if target_name.lower() in nm.lower()), 0)
                a = next((n for nm, n in after  if target_name.lower() in nm.lower()), 0)
                log.info("  %s in-crisis: %d → %d (+%d)",
                         adapter.code, b, a, a - b)
                audit.note(f"{adapter.code} crisis voc: {b} → {a}")


if __name__ == "__main__":
    asyncio.run(main())
