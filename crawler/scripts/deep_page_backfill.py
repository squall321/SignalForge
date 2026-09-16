# 목록 페이지를 매 실행 더 깊이 내려가며 과거 글을 소급 수집한다 (커서 방식).
"""deep_page_backfill — 포럼형 소스의 역사 수집.

배경:
  기존 historical_kr_backfill.py 는 매 실행 **같은 앞 50페이지**를 다시 긁었다.
  실측(2026-09-13) — 36분을 돌고 clien 0건·ppomppu 3건·dcinside 2건 저장.
  제자리걸음이다. 그 결과 dcinside 는 99,868건 중 2026년 이전이 16건뿐이다.

  Reddit 백필에서 겪은 것과 같은 함정이다. 창을 고정하면 꼬리만 긁는다.
  여기서는 **페이지 커서**를 남겨 매 실행 다음 구간으로 내려간다.

동작:
  site 별로 `<state>/deep-page-state.json` 에 다음 시작 페이지를 적는다.
    {"dcinside": {"page": 61, "empty_rounds": 0} , ...}
  한 실행은 site 당 PAGES_PER_RUN 페이지만 훑고 커서를 그만큼 전진시킨다.
  연속으로 EMPTY_LIMIT 번 신규 0건이면 바닥으로 보고 커서를 1 로 되돌린다
  (사이트가 과거를 더 안 내주거나 우리가 이미 다 가진 것이다).

  저장은 크롤러 save() 를 그대로 쓴다 — external_id + content_hash 2단 중복
  차단이 있어 창이 겹쳐도 손해가 없다(멱등).

env:
  DEEP_SITES            콤마 구분 (기본 SITES 전체)
  DEEP_PAGES_PER_RUN    실행당 site 당 페이지 수 (기본 12)
  DEEP_EMPTY_LIMIT      연속 0건 허용 횟수 (기본 3)
  DEEP_STATE            상태 파일 경로
  DEEP_BUDGET_SEC       site 당 시간 상한 (기본 900)
  DRY_RUN=1             저장하지 않는다
"""
import asyncio
import importlib
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("deep_page")

# (site, 모듈, 클래스, env 접두, 최소 페이지)
SITES = {
    "dcinside": ("platforms.dcinside", "DCInsideCrawler", "DCINSIDE", 1),
    "clien": ("platforms.clien", "ClienCrawler", "CLIEN", 0),   # 0-indexed
    "ppomppu": ("platforms.ppomppu", "PpomppuCrawler", "PPOMPPU", 1),
    "theqoo": ("platforms.theqoo", "TheqooCrawler", "THEQOO", 1),
    "instiz": ("platforms.instiz", "InstizCrawler", "INSTIZ", 1),
    "dogdrip": ("platforms.dogdrip", "DogdripCrawler", "DOGDRIP", 1),
    "ruliweb": ("platforms.ruliweb", "RuliwebCrawler", "RULIWEB", 1),
    "bobaedream": ("platforms.bobaedream", "BobaeDreamCrawler", "BOBAEDREAM", 1),
    # 글로벌 포럼
    "kaskus": ("platforms.kaskus", "KaskusCrawler", "KASKUS", 1),
    "lowyat": ("platforms.lowyat", "LowyatCrawler", "LOWYAT", 0),   # 0-indexed
    "donanimhaber": ("platforms.donanimhaber", "DonanimHaberCrawler",
                     "DONANIMHABER", 1),
    # 뉴스·포럼 — 목록 페이지네이션 보유(2026-09-15 전수 분류에서 식별)
    "phonearena": ("platforms.phonearena", "PhoneArenaCrawler", "PHONEARENA", 1),
    "tecnoblog": ("platforms.tecnoblog", "TecnoblogCrawler", "TECNOBLOG", 1),
    "telepolis": ("platforms.telepolis", "TelepolisCrawler", "TELEPOLIS", 1),
    "shiftdelete": ("platforms.shiftdelete", "ShiftDeleteCrawler", "SHIFTDELETE", 1),
    "frandroid": ("platforms.frandroid", "FrandroidCrawler", "FRANDROID", 1),
    "kompas": ("platforms.kompas", "KompasCrawler", "KOMPAS", 1),
    "phandroid": ("platforms.phandroid", "PhandroidCrawler", "PHANDROID", 1),
    "inside_handy": ("platforms.inside_handy", "InsideHandyCrawler",
                     "INSIDE_HANDY", 1),
    "droidsans": ("platforms.droidsans", "DroidSansCrawler", "DROIDSANS", 1),
}

# 이 틀에 못 넣는 것 — 억지로 만들지 않고 사유를 남긴다.
#   resetera       목록 페이지네이션이 없다(검색/피드 기반). 다른 수단이 필요하다.
#   gsmarena_forum 기기별 리뷰 구조라 '페이지'의 의미가 다르다
#                  (MAX_REVIEWS_PER_DEVICE 로 깊이가 정해진다). 별도 취급.
UNSUPPORTED = {
    "resetera": "목록 페이지네이션 없음 (검색/피드 기반)",
    "gsmarena_forum": "기기별 리뷰 구조 — 페이지 의미가 다름",
    "sweclockers": "LIST_PAGES 는 의례적 상한 — RSS 자체가 페이지네이션 미지원",
    "computerbase": "MAX_THREAD_PAGES(스레드 내부 페이지) — 목록 깊이와 의미가 다름",
    "fourchan_g": "4chan 은 스레드가 만료돼 사라진다 — 역사가 존재하지 않는다",
    "fmkorea": "Playwright 봇 챌린지에 막혀 있다 — 그게 풀려야 깊이가 의미 있다",
    # LIST_PAGES 상수는 있으나 **실제 page 루프가 없다.** 상수만 보고 '가능'으로
    # 분류하면 안 된다 — 코드 주석이 진실을 말한다(2026-09-16 재확인).
    "ithome": "RSS 단일 페이지(~60건)만 노출 — LIST_PAGES 는 의례적 상한",
    "tweakers": "피드 기반 — page 루프가 없다",
    "mobil_se": "실제 가용 ~50건 — LIST_PAGES 는 contract 준수용 상한",
    "4pda": "탐색 시 403 — 차단 우회가 먼저 필요하다",
    "macrumors": "RSS 3종뿐. /guide/ 는 제품 가이드라 VOC 성격이 다르다",
    "zdnet_kr": "검색이 &page 를 무시한다(응답이 바이트 단위로 동일)",
}

PAGES_PER_RUN = int(os.getenv("DEEP_PAGES_PER_RUN", "12"))
EMPTY_LIMIT = int(os.getenv("DEEP_EMPTY_LIMIT", "3"))
BUDGET_SEC = float(os.getenv("DEEP_BUDGET_SEC", "900"))
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
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


STATE_PATH = os.getenv(
    "DEEP_STATE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "..", "logs", "deep-page-state.json"))


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
        os.replace(tmp, STATE_PATH)
    except Exception as e:
        log.warning("상태 저장 실패: %s", e)


def _selected():
    raw = os.getenv("DEEP_SITES", "").strip()
    names = [s.strip() for s in raw.split(",") if s.strip()] if raw else list(SITES)
    return [n for n in names if n in SITES]


async def _run_site(site: str, start_page: int) -> int:
    """지정 구간을 훑어 신규 저장 건수를 돌려준다."""
    mod_path, cls_name, prefix, _ = SITES[site]
    # 크롤러 상수는 import 시점에 굳으므로 **import 전에** env 를 심는다.
    os.environ[f"{prefix}_PAGE_START"] = str(start_page)
    os.environ[f"{prefix}_BACKFILL_PAGES"] = str(PAGES_PER_RUN)
    mod = importlib.reload(importlib.import_module(mod_path))
    crawler = getattr(mod, cls_name)()
    crawler.CRAWL_BUDGET_SEC = BUDGET_SEC
    crawler.RUN_BUDGET_SEC = BUDGET_SEC

    log.info("%s p%d~p%d 시작", site, start_page, start_page + PAGES_PER_RUN - 1)
    raw = await crawler.crawl()
    if DRY_RUN:
        log.info("%s: %d건 수집 (DRY_RUN — 저장 안 함)", site, len(raw))
        return 0
    if not raw:
        return 0

    from nlp.pipeline import process_voc_list
    saved = 0
    for i in range(0, len(raw), crawler.NLP_CHUNK):
        part = raw[i:i + crawler.NLP_CHUNK]
        processed = await process_voc_list(
            [crawler.normalize(r) for r in part],
            translate_deadline=_nlp_deadline())
        saved += await crawler.save(processed)
    log.info("%s: %d건 수집 → %d건 신규 저장", site, len(raw), saved)
    return saved


async def main():
    state = _load_state()
    sites = _selected()
    log.info("깊이 백필 — 대상 %s, 실행당 %d페이지%s",
             ",".join(sites), PAGES_PER_RUN, " [DRY_RUN]" if DRY_RUN else "")

    total = 0
    for site in sites:
        _, _, _, floor = SITES[site]
        st = state.setdefault(site, {})
        page = int(st.get("page", floor))
        if page < floor:
            page = floor
        t0 = time.monotonic()
        try:
            saved = await _run_site(site, page)
        except Exception as e:
            log.warning("%s 실패 — %s: %s", site, type(e).__name__, str(e)[:120])
            continue                     # 커서를 옮기지 않는다 → 다음 실행이 재시도
        total += saved

        if DRY_RUN:
            continue
        if saved == 0:
            st["empty_rounds"] = int(st.get("empty_rounds", 0)) + 1
            if st["empty_rounds"] >= EMPTY_LIMIT:
                log.info("%s: 신규 0건 %d회 연속 — 바닥으로 보고 p%d 부터 재순회",
                         site, st["empty_rounds"], floor)
                st["page"] = floor
                st["empty_rounds"] = 0
            else:
                st["page"] = page + PAGES_PER_RUN
        else:
            st["empty_rounds"] = 0
            st["page"] = page + PAGES_PER_RUN
        st["last_run"] = int(time.time())
        st["last_saved"] = saved
        _save_state(state)
        log.info("%s 커서 → p%d (%.0fs)", site, st["page"], time.monotonic() - t0)

    log.info("깊이 백필 완료 — 신규 저장 %d건", total)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
