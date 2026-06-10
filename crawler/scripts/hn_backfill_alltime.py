"""HN Algolia 전기간 backfill — 옛 디바이스 (S1~S21, Note, Z Fold/Flip 1~4, Watch 1~5 등) 커버리지 확보.

기존 hackernews 크롤러는 numericFilters=created_at_i>cutoff (90일) 로 최근 글만 가져온다.
이 스크립트는 같은 검색어 풀을 numericFilters 없이 (즉 전기간) 1회 일괄 수집해
HN 의 옛 글을 DB 로 backfill 한다.

전략:
- 80+ 검색어 (최신 모델 + 옛 모델 + 액세서리 + SW + 키워드)
- tags=story / tags=comment 각각 호출
- hitsPerPage=1000 (Algolia 최대) * page 0..MAX_PAGES-1 pagination
- objectID set dedup
- ON CONFLICT (platform_id, external_id) DO NOTHING → 기존 row 와 중복 회피
- rate limit: 매 요청 후 sleep BETWEEN_REQUEST_SLEEP

환경변수:
  DATABASE_URL          (필수)
  BACKFILL_DRY_RUN      '1'/'true' 면 INSERT 안 함 (count 만 출력)
  BACKFILL_MAX_PAGES    검색어/태그 별 최대 페이지 수 (기본 3 → 최대 3000 hit)
  BACKFILL_HITS_PER_PAGE  기본 1000 (Algolia 최대)
  BACKFILL_SLEEP        요청 사이 sleep 초 (기본 1.0)
  BACKFILL_TERMS        쉼표 구분 — 지정 시 이 검색어만 사용 (테스트 용)

실행:
  cd crawler && python -m scripts.hn_backfill_alltime
"""
import asyncio
import hashlib
import logging
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)

from base.crawler import RawVOC  # noqa: E402
from platforms.hackernews import QUERY_TERMS as HN_QUERY_TERMS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("hn_backfill_alltime")

ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM_URL = "https://news.ycombinator.com/item?id="

DATABASE_URL = os.getenv("DATABASE_URL", "")
DRY_RUN = os.getenv("BACKFILL_DRY_RUN", "").lower() in ("1", "true", "yes")
MAX_PAGES = int(os.getenv("BACKFILL_MAX_PAGES", "3"))
HITS_PER_PAGE = int(os.getenv("BACKFILL_HITS_PER_PAGE", "1000"))
BETWEEN_REQUEST_SLEEP = float(os.getenv("BACKFILL_SLEEP", "1.0"))

# r6 Stage 2c (2026-06-10): 연도 슬라이싱 — Algolia 쿼리당 1,000-hit 캡을
# created_at_i 연도 윈도우 × 검색어로 쪼개 우회. 두 env 모두 설정 시에만 활성.
# 예: BACKFILL_YEAR_FROM=2010 BACKFILL_YEAR_TO=2021 → 연도별 12회 검색.
YEAR_FROM = int(os.getenv("BACKFILL_YEAR_FROM", "0"))
YEAR_TO = int(os.getenv("BACKFILL_YEAR_TO", "0"))

# R12 (2026-06-04): hackernews.QUERY_TERMS (200+) 와 동기화 — 한 곳에서 풀 관리.
# 추가로 backfill 만 쓰는 옛 키워드도 합치고 dedup.
_LEGACY_BACKFILL_TERMS: List[str] = [
    # ----- Galaxy S 시리즈 전기간 (S1 ~ S26) -----
    "Galaxy S26",
    "Galaxy S25",
    "Galaxy S25 Ultra",
    "Galaxy S24",
    "Galaxy S23",
    "Galaxy S22",
    "Galaxy S21",
    "Galaxy S20",
    "Galaxy S10",
    "Galaxy S9",
    "Galaxy S8",
    "Galaxy S7",
    "Galaxy S6",
    "Galaxy S5",
    "Galaxy S4",
    "Galaxy S III",
    "Galaxy S3",
    "Galaxy S II",
    "Galaxy S2",
    "Galaxy S smartphone",
    # ----- Note 시리즈 -----
    "Galaxy Note",
    "Galaxy Note 20",
    "Galaxy Note 10",
    "Galaxy Note 9",
    "Galaxy Note 8",
    "Galaxy Note 7",
    "Note 7 recall",
    "Galaxy Note 5",
    "Galaxy Note 4",
    "Galaxy Note 3",
    "Galaxy Note 2",
    # ----- Z Fold / Flip 전기간 -----
    "Galaxy Z Fold",
    "Galaxy Z Fold 7",
    "Galaxy Z Fold 6",
    "Galaxy Z Fold 5",
    "Galaxy Z Fold 4",
    "Galaxy Z Fold 3",
    "Galaxy Z Fold 2",
    "Galaxy Fold",
    "Galaxy Z Flip",
    "Galaxy Z Flip 7",
    "Galaxy Z Flip 6",
    "Galaxy Z Flip 5",
    "Galaxy Z Flip 4",
    "Galaxy Z Flip 3",
    # ----- Watch / Buds / Ring 전기간 -----
    "Galaxy Watch",
    "Galaxy Watch Ultra",
    "Galaxy Watch 7",
    "Galaxy Watch 6",
    "Galaxy Watch 5",
    "Galaxy Watch 4",
    "Galaxy Watch 3",
    "Galaxy Gear",
    "Gear S",
    "Galaxy Buds",
    "Galaxy Buds Pro",
    "Galaxy Buds 3",
    "Galaxy Ring",
    # ----- A 시리즈 / Tab -----
    "Galaxy A series",
    "Galaxy A55",
    "Galaxy A54",
    "Galaxy Tab",
    "Galaxy Tab S10",
    # ----- SW / 생태계 -----
    "One UI",
    "TouchWiz",
    "Tizen",
    "Bixby",
    "Galaxy AI",
    "Samsung DeX",
    "Samsung Pay",
    "Samsung Knox",
    "Samsung Internet",
    "Samsung Health",
    "SmartThings",
    "Galaxy Store",
    # ----- 키워드 -----
    "samsung",
    "samsung galaxy",
    "Samsung phone",
    "Galaxy battery",
    "Galaxy camera",
    "Galaxy display",
    "Galaxy foldable",
    "Snapdragon Galaxy",
    "Exynos Galaxy",
]


def _merge_unique(*lists: List[str]) -> List[str]:
    """순서 보존 + 중복 제거 머지."""
    seen: set = set()
    out: List[str] = []
    for lst in lists:
        for t in lst:
            tk = t.strip()
            if not tk or tk in seen:
                continue
            seen.add(tk)
            out.append(tk)
    return out


# 최종 풀: hackernews.QUERY_TERMS (R12 200+) ∪ 옛 backfill 풀
DEFAULT_TERMS: List[str] = _merge_unique(HN_QUERY_TERMS, _LEGACY_BACKFILL_TERMS)


def _load_terms() -> List[str]:
    raw = os.getenv("BACKFILL_TERMS", "").strip()
    if not raw:
        return list(DEFAULT_TERMS)
    return [t.strip() for t in raw.split(",") if t.strip()]


def _story_hit_to_voc(hit: dict) -> Optional[RawVOC]:
    sid = hit.get("objectID")
    if not sid:
        return None
    title = (hit.get("title") or "").strip()
    story_text = (hit.get("story_text") or "").strip()
    if "<" in story_text and ">" in story_text:
        try:
            from bs4 import BeautifulSoup
            story_text = BeautifulSoup(story_text, "html.parser").get_text(" ", strip=True)
        except Exception:
            pass
    content = f"{title}\n{story_text}".strip() if story_text else title
    if not content:
        return None
    created_at_i = hit.get("created_at_i")
    published_at = (
        datetime.fromtimestamp(created_at_i, tz=timezone.utc) if created_at_i else None
    )
    article_url = hit.get("url") or f"{HN_ITEM_URL}{sid}"
    return RawVOC(
        external_id=hashlib.md5(f"hn_{sid}".encode()).hexdigest()[:16],
        content=content,
        source_url=article_url,
        author_name=hit.get("author") or None,
        published_at=published_at,
        likes_count=int(hit.get("points") or 0),
        comments_count=int(hit.get("num_comments") or 0),
        country_code="US",
        meta={"hn_id": sid, "hn_item_url": f"{HN_ITEM_URL}{sid}", "kind": "story"},
    )


def _comment_hit_to_voc(hit: dict) -> Optional[RawVOC]:
    cid = hit.get("objectID")
    if not cid:
        return None
    text_html = hit.get("comment_text") or ""
    if not text_html:
        return None
    if "<" in text_html and ">" in text_html:
        try:
            from bs4 import BeautifulSoup
            body = BeautifulSoup(text_html, "html.parser").get_text(" ", strip=True)
        except Exception:
            body = text_html
    else:
        body = text_html
    body = body.strip()
    if not body:
        return None
    created_at_i = hit.get("created_at_i")
    cdate = (
        datetime.fromtimestamp(created_at_i, tz=timezone.utc) if created_at_i else None
    )
    story_id = hit.get("story_id") or hit.get("parent_id")
    return RawVOC(
        external_id=hashlib.md5(f"hn_{cid}".encode()).hexdigest()[:16],
        content=body,
        source_url=f"{HN_ITEM_URL}{cid}",
        author_name=hit.get("author") or None,
        published_at=cdate,
        likes_count=0,
        country_code="US",
        meta={
            "hn_id": cid,
            "parent_story": str(story_id) if story_id else None,
            "kind": "comment",
        },
    )


def _year_windows() -> List[Optional[str]]:
    """r6 2c: 연도 슬라이싱 활성 시 numericFilters 목록, 아니면 [None] (전기간 1회)."""
    if not (YEAR_FROM and YEAR_TO and YEAR_FROM <= YEAR_TO):
        return [None]
    windows: List[Optional[str]] = []
    for y in range(YEAR_FROM, YEAR_TO + 1):
        start = int(datetime(y, 1, 1, tzinfo=timezone.utc).timestamp())
        end = int(datetime(y + 1, 1, 1, tzinfo=timezone.utc).timestamp())
        windows.append(f"created_at_i>={start},created_at_i<{end}")
    return windows


async def _search_paged(
    client: httpx.AsyncClient,
    query: str,
    tags: str,
    hits_per_page: int,
    max_pages: int,
    numeric_filters: Optional[str] = None,
) -> List[dict]:
    """전기간 (또는 numericFilters 연도 윈도우) 검색 + page 0..max_pages-1 pagination.

    Algolia 응답에 nbPages 가 있으면 그것과 max_pages 의 min 만큼만 순회.
    numeric_filters (r6 2c): "created_at_i>=X,created_at_i<Y" — 쿼리당
    1,000-hit 캡을 연도 윈도우로 쪼개 옛 글을 추가로 푼다.
    """
    all_hits: List[dict] = []
    for page in range(max_pages):
        params = {
            "query": query,
            "tags": tags,
            "hitsPerPage": hits_per_page,
            "page": page,
        }
        if numeric_filters:
            params["numericFilters"] = numeric_filters
        resp = await client.get(ALGOLIA_SEARCH, params=params)
        resp.raise_for_status()
        payload = resp.json()
        hits = payload.get("hits") or []
        all_hits.extend(hits)
        nb_pages = payload.get("nbPages")
        if nb_pages is not None and page + 1 >= nb_pages:
            break
        if not hits:
            break
        await asyncio.sleep(BETWEEN_REQUEST_SLEEP)
    return all_hits


async def collect_all_hits(
    terms: List[str],
    max_pages: int,
    hits_per_page: int,
    client_factory=None,
) -> tuple[List[RawVOC], dict]:
    """검색어별 story + comment 전기간 수집 후 RawVOC 리스트 반환.

    client_factory: 테스트용 — async context manager 를 반환하는 callable.
    """
    seen_story_ids: set = set()
    seen_comment_ids: set = set()
    raw_vocs: List[RawVOC] = []
    stats = {"story_hits": 0, "comment_hits": 0, "story_voc": 0, "comment_voc": 0}

    if client_factory is None:
        client_factory = lambda: httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    windows = _year_windows()  # r6 2c: [None] (전기간) 또는 연도별 numericFilters
    if windows != [None]:
        log.info("연도 슬라이싱 활성: %d~%d (%d windows)", YEAR_FROM, YEAR_TO, len(windows))

    async with client_factory() as client:
        for i, q in enumerate(terms, 1):
            for nf in windows:
                win_label = f" [{nf}]" if nf else ""
                try:
                    story_hits = await _search_paged(
                        client, q, "story", hits_per_page, max_pages, numeric_filters=nf
                    )
                    stats["story_hits"] += len(story_hits)
                    new = 0
                    for h in story_hits:
                        sid = h.get("objectID")
                        if not sid or sid in seen_story_ids:
                            continue
                        seen_story_ids.add(sid)
                        voc = _story_hit_to_voc(h)
                        if voc:
                            raw_vocs.append(voc)
                            stats["story_voc"] += 1
                            new += 1
                    log.info(
                        "  [%d/%d] story '%s'%s fetched=%d new=%d",
                        i, len(terms), q, win_label, len(story_hits), new,
                    )
                except Exception as e:
                    log.warning("  story '%s'%s 실패: %s", q, win_label, e)
                await asyncio.sleep(BETWEEN_REQUEST_SLEEP)

                try:
                    comment_hits = await _search_paged(
                        client, q, "comment", hits_per_page, max_pages, numeric_filters=nf
                    )
                    stats["comment_hits"] += len(comment_hits)
                    new = 0
                    for h in comment_hits:
                        cid = h.get("objectID")
                        if not cid or cid in seen_comment_ids:
                            continue
                        seen_comment_ids.add(cid)
                        voc = _comment_hit_to_voc(h)
                        if voc:
                            raw_vocs.append(voc)
                            stats["comment_voc"] += 1
                            new += 1
                    log.info(
                        "  [%d/%d] comment '%s'%s fetched=%d new=%d",
                        i, len(terms), q, win_label, len(comment_hits), new,
                    )
                except Exception as e:
                    log.warning("  comment '%s'%s 실패: %s", q, win_label, e)
                await asyncio.sleep(BETWEEN_REQUEST_SLEEP)

    return raw_vocs, stats


async def _insert_vocs(db: AsyncSession, platform_id: int, vocs: List[RawVOC]) -> int:
    """voc_records 로 INSERT … ON CONFLICT DO NOTHING. 신규 INSERT 행 수 반환.

    NLP 처리는 후행 backfill_categories 가 담당 — 여기선 원본만 적재.
    """
    inserted = 0
    stmt = text("""
        INSERT INTO voc_records (
            product_id, platform_id, external_id, source_url, author_name,
            content_original, country_code,
            likes_count, comments_count, shares_count,
            published_at, collected_at
        ) VALUES (
            NULL, :platform_id, :external_id, :source_url, :author_name,
            :content_original, :country_code,
            :likes_count, :comments_count, 0,
            :published_at, NOW()
        )
        ON CONFLICT (platform_id, external_id) DO NOTHING
    """)
    for v in vocs:
        try:
            result = await db.execute(stmt, {
                "platform_id": platform_id,
                "external_id": v.external_id,
                "source_url": v.source_url,
                "author_name": v.author_name,
                "content_original": v.content,
                "country_code": v.country_code,
                "likes_count": v.likes_count,
                "comments_count": v.comments_count,
                "published_at": v.published_at,
            })
            if result.rowcount:
                inserted += 1
        except Exception as e:
            log.warning("INSERT 실패 (%s): %s", v.external_id, e)
    await db.commit()
    return inserted


async def main() -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL 미설정")
        sys.exit(2)

    terms = _load_terms()
    log.info(
        "HN backfill 시작: terms=%d max_pages=%d hits_per_page=%d sleep=%.1fs DRY_RUN=%s",
        len(terms), MAX_PAGES, HITS_PER_PAGE, BETWEEN_REQUEST_SLEEP, DRY_RUN,
    )

    raw_vocs, stats = await collect_all_hits(terms, MAX_PAGES, HITS_PER_PAGE)
    log.info(
        "수집 완료: story_hits=%d comment_hits=%d -> RawVOC story=%d comment=%d total=%d",
        stats["story_hits"], stats["comment_hits"],
        stats["story_voc"], stats["comment_voc"], len(raw_vocs),
    )

    if DRY_RUN:
        log.info("DRY_RUN — INSERT 생략. 종료.")
        return

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        row = (await db.execute(
            text("SELECT id FROM platforms WHERE code = 'hackernews'")
        )).one_or_none()
        if not row:
            log.error("platform 'hackernews' 없음 — 종료")
            await engine.dispose()
            sys.exit(3)
        platform_id = row[0]
        log.info("hackernews platform_id=%d", platform_id)

        # baseline count
        before = (await db.execute(
            text("SELECT count(*) FROM voc_records WHERE platform_id = :pid"),
            {"pid": platform_id},
        )).scalar_one()

        inserted = await _insert_vocs(db, platform_id, raw_vocs)

        after = (await db.execute(
            text("SELECT count(*) FROM voc_records WHERE platform_id = :pid"),
            {"pid": platform_id},
        )).scalar_one()

    await engine.dispose()
    log.info(
        "=== backfill 완료 ===  vocs_attempted=%d  inserted=%d  HN total %d -> %d",
        len(raw_vocs), inserted, before, after,
    )


if __name__ == "__main__":
    asyncio.run(main())
