# Arctic Shift 기간 검색으로 Reddit 과거 글을 소급 수집한다 (연도 1개씩).
"""reddit_arctic_backfill — Reddit 역사 수집.

배경:
  reddit_rss 는 `/new/.rss` 와 `new.json` 으로 **최신 100건까지만** 본다.
  실측(2026-09-15) — reddit_rss 26,739건 중 2026년이 26,720건, 그 이전은 19건.
  사실상 올해치만 있다.

  공식 API 는 막혔다. 2025-11-11 Responsible Builder Policy 로 self-service
  발급이 닫혀 신규 OAuth 토큰은 사전 승인이 필요하고, 우리 `reddit` 크롤러는
  2026-05-30 이후 0건이다.

  Arctic Shift(https://arctic-shift.photon-reddit.com) 는 Reddit 공개 덤프를
  기간 검색으로 제공한다. after/before 로 과거 구간이 정상 반환되는 것을
  실측 확인했다(2023-01~2023-04 r/samsung).

설계 — "천천히, 하지만 확실하게":
  · 한 실행에 **연도 하나**만 훑되, sub 당 페이지 상한이 있다. 상한에 걸리면
    **그 지점의 시각 커서를 저장**해 다음 실행이 이어받는다. 이게 없으면 다음
    실행이 다음 연도로 넘어가 그 해의 나머지를 영영 놓친다 — r/samsung 은 한 해에
    수만 건이라 40페이지(4,000건)로는 꼬리만 긁고 만다. 한 연도의 모든 sub 가
    소진돼야 러너가 연도를 내린다(`--year-done` 종료코드로 알린다).
  · 창 안에서는 keyset 페이지네이션 — 받은 것 중 가장 오래된 시각을 다음
    `before` 로 삼아 거슬러 올라간다. offset 이 아니라 시각 커서라 누락이 없다.
  · 요청 사이에 딜레이를 둔다. 레이트리밋을 유발하면 그 소스를 통째로 잃는다
    (실측으로 이미 두 번 자초했다 — kaskus 403 9회·computerbase 429).
  · 429/5xx 는 지수 백오프 후 재시도하고, 그래도 안 되면 그 sub 를 건너뛴다.
    다음 실행이 같은 창을 다시 돌므로 잃는 것이 없다.
  · 저장은 크롤러의 save() 를 그대로 쓴다 — external_id + content_hash 2단
    중복 차단이 이미 있어 같은 창을 다시 돌아도 손해가 없다(멱등).

env:
  REDDIT_BACKFILL_YEAR      대상 연도 (필수, 러너가 주입)
  REDDIT_BACKFILL_SUBS      콤마 구분 — 생략 시 reddit_rss 의 기본 목록
  REDDIT_ARCTIC_DELAY       요청 간 딜레이 초 (기본 3.0)
  REDDIT_ARCTIC_PAGE_LIMIT  한 요청 건수 (기본 100)
  REDDIT_ARCTIC_MAX_PAGES   sub 당 한 실행 최대 페이지 (기본 40)
  DRY_RUN=1                 저장하지 않고 건수만 센다
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms.reddit_rss import (  # noqa: E402
    ARCTIC_BASE,
    SUBREDDITS,
    RedditRSSCrawler,
    arctic_items_to_posts,
    post_to_rawvoc,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("reddit_arctic")

DELAY = float(os.getenv("REDDIT_ARCTIC_DELAY", "3.0"))
PAGE_LIMIT = int(os.getenv("REDDIT_ARCTIC_PAGE_LIMIT", "100"))
MAX_PAGES = int(os.getenv("REDDIT_ARCTIC_MAX_PAGES", "40"))
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
UA = "SignalForge/1.0 archive backfill"

_RETRY_STATUS = (429, 500, 502, 503, 504)
_MAX_RETRY = 4

# 백필은 **번역을 건너뛴다.** 대량 수집이 번역 서비스를 두드리면 레이트리밋을
# 유발해(실측: 이 스크립트 1회 실행에 MyMemory 실패 23건) 실시간 파이프라인의
# 번역 할당량까지 갉아먹는다. 원문은 그대로 저장되고, 12시간 주기
# translation_reprocess(nlp.reprocess.translate_backlog)가
# `content_translated = content_original` 인 행을 골라 나중에 메운다.
# 한국어 감성은 원문에서 직접 하므로 분석에도 지장이 없다.
# BACKFILL_TRANSLATE=1 로 켤 수 있다.
SKIP_TRANSLATE = os.getenv("BACKFILL_TRANSLATE", "0") != "1"


def _nlp_deadline():
    """번역을 건너뛰려면 이미 지난 마감을 넘긴다(nlp.pipeline 이 해석)."""
    return (time.monotonic() - 1) if SKIP_TRANSLATE else None


# 창 안 진행 상태 — {"2023": {"samsung": "2023-06-14T02:11:00" | "done"}}
STATE_PATH = os.getenv(
    "REDDIT_ARCTIC_STATE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "..", "logs", "reddit-backfill-state.json"))


def _load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(STATE_PATH)), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, STATE_PATH)      # 원자적 교체 — 중단돼도 깨지지 않는다
    except Exception as e:
        log.warning("상태 저장 실패: %s", e)


def _subs():
    raw = os.getenv("REDDIT_BACKFILL_SUBS", "").strip()
    if raw:
        return [s.strip() for s in raw.split(",") if s.strip()]
    return list(SUBREDDITS)


def _year_window(year: int):
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    return start, end


async def _get(client: httpx.AsyncClient, params: dict):
    """429/5xx 는 백오프 재시도. 끝내 실패하면 None — 호출자가 그 sub 를 접는다."""
    for attempt in range(1, _MAX_RETRY + 1):
        try:
            r = await client.get(ARCTIC_BASE, params=params, timeout=60.0)
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            if attempt >= _MAX_RETRY:
                log.warning("  통신 실패 — %s", type(e).__name__)
                return None
            await asyncio.sleep(min(2 ** attempt * DELAY, 60))
            continue
        if r.status_code in _RETRY_STATUS:
            if attempt >= _MAX_RETRY:
                log.warning("  status=%d 지속 — 이 구간 포기", r.status_code)
                return None
            back = min(2 ** attempt * DELAY, 90)
            log.info("  status=%d — %.0fs 후 재시도 (%d/%d)",
                     r.status_code, back, attempt, _MAX_RETRY)
            await asyncio.sleep(back)
            continue
        if r.status_code != 200:
            log.warning("  status=%d — 건너뜀", r.status_code)
            return None
        try:
            data = r.json()
        except Exception:
            return None
        return data.get("data") if isinstance(data, dict) else data
    return None


async def _walk_sub(client, sub: str, start: datetime, end: datetime):
    """창 [start, end) 를 뒤에서부터 거슬러 훑는다.

    반환 (posts, next_cursor) — next_cursor 가 None 이면 이 창을 다 훑은 것이다.
    상한에 걸려 중단되면 그 시각을 돌려주고, 다음 실행이 거기서 이어받는다.
    """
    posts, seen_ids = [], set()
    cursor = end
    exhausted = False
    for page in range(1, MAX_PAGES + 1):
        if cursor <= start:
            break
        params = {
            "subreddit": sub,
            "limit": PAGE_LIMIT,
            "sort": "desc",
            "after": start.strftime("%Y-%m-%d"),
            "before": cursor.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        items = await _get(client, params)
        if items is None:
            break                      # 실패 — 커서를 남겨 다음 실행이 재시도
        got = arctic_items_to_posts(items, sub)
        fresh = [p for p in got if p.reddit_id not in seen_ids]
        for p in fresh:
            seen_ids.add(p.reddit_id)
        posts.extend(fresh)

        dated = [p.published for p in got if p.published]
        if not got or not dated:
            exhausted = True           # 더 없다 = 이 창 소진
            break
        oldest = min(dated)
        # 진전이 없으면(전부 같은 시각) 1초 당겨 무한루프를 막는다
        cursor = oldest if oldest < cursor else cursor - timedelta(seconds=1)
        if not fresh:
            exhausted = True           # 같은 것만 돌아온다 = 소진
            break
        log.info("  r/%s p%d: +%d (누적 %d, 커서 %s)",
                 sub, page, len(fresh), len(posts), oldest.date())
        await asyncio.sleep(DELAY)
    else:
        # for 를 다 돌았다 = 페이지 상한에 걸림. 커서를 남긴다.
        return posts, cursor
    if cursor <= start:
        exhausted = True
    return posts, (None if exhausted else cursor)


async def main():
    year_raw = os.getenv("REDDIT_BACKFILL_YEAR", "").strip()
    if not year_raw.isdigit():
        log.error("REDDIT_BACKFILL_YEAR 미설정 — 러너가 주입해야 한다")
        return 2
    year = int(year_raw)
    start, end = _year_window(year)
    subs = _subs()

    state = _load_state()
    ystate = state.setdefault(str(year), {})

    pending = [s for s in subs if ystate.get(s) != "done"]
    log.info("Reddit 역사 백필 %d년 — sub %d/%d 남음, 딜레이 %.1fs, "
             "sub당 최대 %d페이지%s",
             year, len(pending), len(subs), DELAY, MAX_PAGES,
             " [DRY_RUN]" if DRY_RUN else "")
    if not pending:
        log.info("%d년은 이미 전부 소진 — 러너에 연도 완료를 알린다", year)
        return 3                       # 3 = year done

    crawler = RedditRSSCrawler()
    crawler.CRAWL_BUDGET_SEC = float(os.getenv("REDDIT_ARCTIC_BUDGET", "100000"))
    crawler.RUN_BUDGET_SEC = crawler.CRAWL_BUDGET_SEC

    total_fetched = total_saved = 0
    async with httpx.AsyncClient(headers={"User-Agent": UA},
                                 follow_redirects=True) as client:
        for sub in pending:
            # 이어받기 — 저장된 커서가 있으면 거기서부터
            resume = ystate.get(sub)
            win_end = end
            if resume:
                try:
                    win_end = datetime.fromisoformat(resume)
                    if win_end.tzinfo is None:
                        win_end = win_end.replace(tzinfo=timezone.utc)
                    log.info("r/%s — %s 부터 이어받는다", sub, win_end.date())
                except ValueError:
                    log.warning("r/%s 커서 해석 실패(%r) — 창 끝부터", sub, resume)

            posts, next_cursor = await _walk_sub(client, sub, start, win_end)
            total_fetched += len(posts)

            if not DRY_RUN and posts:
                from nlp.pipeline import process_voc_list
                raws = [post_to_rawvoc(p) for p in posts]
                saved = 0
                for i in range(0, len(raws), crawler.NLP_CHUNK):
                    part = raws[i:i + crawler.NLP_CHUNK]
                    processed = await process_voc_list(
                        [crawler.normalize(r) for r in part],
                        translate_deadline=_nlp_deadline())
                    saved += await crawler.save(processed)
                total_saved += saved
                log.info("r/%s %d년: %d건 수집 → %d건 신규 저장",
                         sub, year, len(posts), saved)
            else:
                log.info("r/%s %d년: %d건%s", sub, year, len(posts),
                         " (DRY_RUN — 저장 안 함)" if DRY_RUN else "")

            # **저장 후에 상태를 옮긴다.** 저장 전에 옮기면 중단 시 그 구간을 잃는다.
            if not DRY_RUN:
                ystate[sub] = "done" if next_cursor is None else next_cursor.isoformat()
                _save_state(state)

            if posts:
                await asyncio.sleep(DELAY)

    remaining = [s for s in subs if ystate.get(s) != "done"]
    log.info("완료 — %d년 수집 %d건 / 신규 저장 %d건 / 남은 sub %d개",
             year, total_fetched, total_saved, len(remaining))
    return 3 if not remaining else 0    # 3 = 이 연도 소진


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
