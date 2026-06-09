"""한국 사이트 (clien / ppomppu / dcinside) 백카탈로그 1회 깊이 수집.

기존 rotate_collect.py 는 LIST_PAGES=12 로 최근 페이지만 본다.
이 스크립트는 BACKFILL_PAGES (기본 50) 만큼 깊이 들어가서 옛 글을 적재한다.

원리:
  - LIST_PAGES 가 module-level 상수라 import 전에 env 를 set 해야 한다.
  - main() 진입 시점에 BACKFILL_PAGES 환경변수 -> 각 사이트별
    CLIEN_BACKFILL_PAGES / PPOMPPU_BACKFILL_PAGES / DCINSIDE_BACKFILL_PAGES
    를 함께 set 한 다음 lazy import.
  - 동일 external_id 는 ON CONFLICT DO NOTHING (BaseCrawler.save) → 멱등.

환경변수:
  DATABASE_URL                  (필수)
  BACKFILL_PAGES                기본 50  (사이트별 LIST_PAGES 일괄)
  BACKFILL_MAX_POSTS            기본 600 (사이트별 MAX_POSTS 일괄)
  BACKFILL_ONE_TIME             '1'/'true' 면 1 사이클만 (기본 1 — 옛 글은 한 번만)
  BACKFILL_SITES                쉼표 구분 — 지정 시 그 사이트만
                                (예: BACKFILL_SITES=clien,ppomppu)

실행:
  cd crawler && DATABASE_URL=... ../.venv/bin/python scripts/historical_kr_backfill.py
"""
import asyncio
import logging
import os
import subprocess
import sys
import time

# 사이트별 LIST_PAGES 가 module 상수라 import 전에 env 설정해야 한다.
BACKFILL_PAGES = int(os.getenv("BACKFILL_PAGES", "50"))
BACKFILL_MAX_POSTS = int(os.getenv("BACKFILL_MAX_POSTS", "600"))
for _site in ("CLIEN", "PPOMPPU", "DCINSIDE"):
    os.environ.setdefault(f"{_site}_BACKFILL_PAGES", str(BACKFILL_PAGES))
    os.environ.setdefault(f"{_site}_MAX_POSTS", str(BACKFILL_MAX_POSTS))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms.clien import ClienCrawler  # noqa: E402
from platforms.ppomppu import PpomppuCrawler  # noqa: E402
from platforms.dcinside import DCInsideCrawler  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("kr_backfill")

ALL_SITES = [
    ("clien",    ClienCrawler),
    ("ppomppu",  PpomppuCrawler),
    ("dcinside", DCInsideCrawler),
]


def _selected_sites():
    raw = os.getenv("BACKFILL_SITES", "").strip()
    if not raw:
        return ALL_SITES
    keep = {s.strip().lower() for s in raw.split(",") if s.strip()}
    return [(n, c) for n, c in ALL_SITES if n in keep]


def _psql_scalar(sql: str) -> str:
    try:
        out = subprocess.run(
            ["psql", "-h", "127.0.0.1", "-p", "5434", "-U", "signalforge",
             "-d", "signalforge", "-tA", "-c", sql],
            env={**os.environ, "PGPASSWORD": "signalforge_pass"},
            capture_output=True, text=True, timeout=20,
        )
        return out.stdout.strip() or "?"
    except Exception as e:
        return f"err({e})"


def _site_counts(code: str) -> dict:
    total = _psql_scalar(
        f"SELECT count(*) FROM voc_records v "
        f"JOIN platforms p ON v.platform_id=p.id WHERE p.code='{code}';"
    )
    old = _psql_scalar(
        f"SELECT count(*) FROM voc_records v "
        f"JOIN platforms p ON v.platform_id=p.id "
        f"WHERE p.code='{code}' AND v.published_at < NOW() - INTERVAL '90 days';"
    )
    return {"total": total, "old": old}


async def main():
    if not os.getenv("DATABASE_URL"):
        log.error("DATABASE_URL 미설정")
        sys.exit(2)

    sites = _selected_sites()
    log.info(
        "=== KR 백카탈로그 시작: pages=%d max_posts=%d sites=%s ===",
        BACKFILL_PAGES, BACKFILL_MAX_POSTS, [n for n, _ in sites],
    )

    before = {n: _site_counts(n) for n, _ in sites}
    for n, c in before.items():
        log.info("  [before] %s: total=%s old(>90d)=%s", n, c["total"], c["old"])

    t0 = time.time()
    results = {}
    for name, cls in sites:
        ts = time.time()
        try:
            r = await cls().run()
            saved = r.get("items_collected", 0)
            log.info("  %s 완료: 신규 %d건 (%ds)", name, saved, int(time.time() - ts))
            results[name] = saved
        except Exception as e:
            log.warning("  %s 실패: %s (%ds)", name, e, int(time.time() - ts))
            results[name] = -1

    after = {n: _site_counts(n) for n, _ in sites}
    log.info("=== KR 백카탈로그 종료 (%ds) ===", int(time.time() - t0))
    for n in (n for n, _ in sites):
        b, a = before[n], after[n]
        log.info(
            "  [delta] %s: total %s -> %s | old(>90d) %s -> %s | saved=%s",
            n, b["total"], a["total"], b["old"], a["old"], results.get(n, "?"),
        )


if __name__ == "__main__":
    asyncio.run(main())
