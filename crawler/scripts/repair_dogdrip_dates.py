# 셀렉터가 낡아 날짜·작성자 없이 저장된 dogdrip 댓글을 재파싱해 소급 복구한다.
"""repair_dogdrip_dates — 이미 저장된 dogdrip 댓글의 발행일·작성자 복구.

배경:
  사이트가 `.comment-bar-author` → `.comment-bar` 로 바뀐 것을 크롤러가 못
  따라가, 모든 댓글이 **작성자 '익명' + 발행일 NULL** 로 저장됐다.
  실측(2026-09-15) — dogdrip 10,431행 중 9,211행(88%). 날짜가 없으면 그 글은
  시계열 분석에서 통째로 빠진다.

  크롤러는 고쳤지만 **이미 저장된 행은 그대로**다. save() 는 external_id 중복을
  건너뛸 뿐 갱신하지 않는다. 그래서 별도 복구가 필요하다.

왜 추정하지 않는가:
  NULL 행의 797개 URL 중 789개는 같은 URL에 날짜가 있는 행(본문)이 있어서
  본문 날짜로 갈음할 수도 있다. 하지만 그건 근사치다. 댓글 external_id 는
  `md5(post_url + '#c' + comment_srl)` 로 **결정적**이라 재파싱하면 정확히
  같은 키가 나온다 — 실제 날짜를 가져올 수 있는데 추정할 이유가 없다.

천천히:
  URL 하나씩, 딜레이를 두고, 진행 상태를 남겨 중단돼도 이어서 한다.
  한 실행의 처리량에 상한을 둬 하루에 다 끝내려 하지 않는다.

env:
  REPAIR_LIMIT     한 실행 최대 URL 수 (기본 150)
  REPAIR_DELAY     URL 간 딜레이 초 (기본 2.0)
  REPAIR_STATE     진행 상태 파일
  DRY_RUN=1        갱신하지 않고 몇 건이 맞는지만 센다
"""
import asyncio
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("repair_dogdrip")

LIMIT = int(os.getenv("REPAIR_LIMIT", "150"))
DELAY = float(os.getenv("REPAIR_DELAY", "2.0"))
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
DATABASE_URL = os.getenv("DATABASE_URL", "")
STATE_PATH = os.getenv(
    "REPAIR_STATE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "..", "logs", "repair-dogdrip-state.json"))

SELECT_URLS = text("""
    SELECT DISTINCT v.source_url
    FROM voc_records v JOIN platforms p ON p.id = v.platform_id
    WHERE p.code = 'dogdrip' AND v.published_at IS NULL
      AND v.source_url <> ALL(:done)
    ORDER BY v.source_url
    LIMIT :lim
""")

# 작성자도 함께 고친다 — 같은 셀렉터 실패로 전부 '익명' 이 됐다.
UPDATE_ONE = text("""
    UPDATE voc_records
       SET published_at = :pub,
           author_name = COALESCE(NULLIF(:author, ''), author_name)
     WHERE external_id = :eid AND published_at IS NULL
""")


def _load_done() -> list:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return list(json.load(f).get("done", []))
    except Exception:
        return []


def _save_done(done: list) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(STATE_PATH)), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"done": done}, f, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)
    except Exception as e:
        log.warning("상태 저장 실패: %s", e)


async def main():
    if not DATABASE_URL:
        log.error("DATABASE_URL 미설정")
        return 2

    from base.crawler import RawVOC
    from platforms.dogdrip import DogdripCrawler

    done = _load_done()
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    async with engine.connect() as conn:
        urls = [r[0] for r in (await conn.execute(
            SELECT_URLS, {"done": done or [""], "lim": LIMIT})).fetchall()]

    log.info("dogdrip 날짜 복구 — 대상 %d URL (이미 처리 %d)%s",
             len(urls), len(done), " [DRY_RUN]" if DRY_RUN else "")
    if not urls:
        log.info("남은 URL 없음 — 복구 완료")
        await engine.dispose()
        return 3

    crawler = DogdripCrawler()
    crawler.CRAWL_BUDGET_SEC = 1e9      # 예산 가드가 중간에 끊지 않게
    crawler.RUN_BUDGET_SEC = 1e9
    fixed = seen = failed = 0

    async with crawler._make_httpx_client() as client:
        for url in urls:
            stub = RawVOC(external_id="stub", content="", source_url=url)
            try:
                vocs = await crawler._fetch_post_detail(client, stub)
            except Exception as e:
                failed += 1
                log.warning("  %s 실패 — %s", url[-14:], type(e).__name__)
                await asyncio.sleep(DELAY)
                continue

            dated = [v for v in vocs if v.published_at]
            seen += len(dated)
            if dated and not DRY_RUN:
                async with engine.begin() as conn:
                    for v in dated:
                        res = await conn.execute(UPDATE_ONE, {
                            "pub": v.published_at,
                            "author": (v.author_name or "").strip(),
                            "eid": v.external_id,
                        })
                        fixed += res.rowcount or 0
            done.append(url)
            if not DRY_RUN and len(done) % 10 == 0:
                _save_done(done)
            await asyncio.sleep(DELAY)

    if not DRY_RUN:
        _save_done(done)
    await engine.dispose()
    log.info("완료 — URL %d 처리 / 날짜있는 항목 %d / 실제 갱신 %d / 실패 %d",
             len(urls), seen, fixed, failed)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
