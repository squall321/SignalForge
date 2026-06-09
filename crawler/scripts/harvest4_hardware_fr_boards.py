"""Harvest 4 H6 — Hardware.fr Smartphones/Tablets 보드 추가 1회 수집.

목표
  - CATEGORY_PATHS 6 → 8 (android/telephone 추가) + 갤럭시 키워드 필터 유지.
  - PRESERVE_EXISTING: BaseCrawler.save() 의 INSERT ... ON CONFLICT DO NOTHING
    으로 자동 보장 (4중 안전장치 #1·#3).
  - DRY_RUN=1 이면 fetch/parse 까지만, save() 생략 (#2).
  - record_run 컨텍스트로 audit JSONL 에 round=harvest4, track=H6 라벨 기록 (#4).

사용
  ROUND=harvest4 DRY_RUN=0 \
    HARDWARE_FR_BACKFILL_PAGES=3 \
    HARDWARE_FR_MAX_THREADS=40 \
    python -m scripts.harvest4_hardware_fr_boards

검증 포인트
  - reports/backfill_audit.jsonl 에 start/end 한 줄 round=harvest4, track=H6.
  - INSERT 카운트 = save() 반환 saved (목표 50+).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.backfill_audit import record_run  # noqa: E402
from platforms.hardware_fr import HardwareFRCrawler  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("harvest4_hardware_fr_boards")


def _is_truthy(name: str) -> bool:
    return (os.getenv(name, "") or "").strip().lower() in ("1", "true", "yes", "on")


def _env_snapshot() -> Dict[str, Any]:
    return {
        "DRY_RUN": _is_truthy("DRY_RUN"),
        "PRESERVE_EXISTING": True,
        "HARDWARE_FR_BACKFILL_PAGES": int(
            os.getenv("HARDWARE_FR_BACKFILL_PAGES", "3")
        ),
        "HARDWARE_FR_MAX_THREADS": int(
            os.getenv("HARDWARE_FR_MAX_THREADS", "40")
        ),
        "HARDWARE_FR_THREAD_PAGES": int(
            os.getenv("HARDWARE_FR_THREAD_PAGES", "1")
        ),
        "HARDWARE_FR_MAX_POSTS": int(
            os.getenv("HARDWARE_FR_MAX_POSTS", "600")
        ),
        "round": os.getenv("ROUND", "harvest4").strip() or "harvest4",
        "track": "H6",
    }


async def _execute_crawl(dry: bool) -> Dict[str, int]:
    c = HardwareFRCrawler()
    t0 = time.time()
    if dry:
        raw = await c.crawl()
        elapsed = time.time() - t0
        log.info("DRY_RUN — raw=%d (save 생략) %.1fs", len(raw), elapsed)
        return {
            "fetched_raw": len(raw),
            "inserted": 0,
            "elapsed_s": int(elapsed),
        }
    result = await c.run()
    elapsed = time.time() - t0
    saved = int(result.get("items_collected", 0) or 0)
    log.info("RUN 완료 — saved=%d %.1fs", saved, elapsed)
    return {
        "fetched_raw": -1,
        "inserted": saved,
        "elapsed_s": int(elapsed),
    }


def _psql_count() -> int:
    import subprocess

    cmd = [
        "psql",
        "-h", os.getenv("PGHOST", "127.0.0.1"),
        "-p", os.getenv("PGPORT", "5434"),
        "-U", os.getenv("PGUSER", "signalforge"),
        "-d", os.getenv("PGDATABASE", "signalforge"),
        "-tAc",
        "SELECT COUNT(*) FROM voc_records WHERE platform_id "
        "= (SELECT id FROM platforms WHERE code='hardware_fr');",
    ]
    env = os.environ.copy()
    env.setdefault("PGPASSWORD", "signalforge_pass")
    try:
        out = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=20
        )
        return int((out.stdout or "0").strip() or 0)
    except Exception as e:
        log.warning("psql count 실패: %s", e)
        return -1


def main() -> int:
    env = _env_snapshot()
    dry = bool(env["DRY_RUN"])
    mode = "dry_run" if dry else "preserve"

    before = _psql_count()
    with record_run(
        script="harvest4_hardware_fr_boards",
        mode=mode,
        env=env,
    ) as audit:
        audit.note(f"before voc count: {before}")
        try:
            counters = asyncio.run(_execute_crawl(dry))
        except Exception as e:  # noqa: BLE001
            audit.note(f"crawl error: {e}")
            raise
        for k, v in counters.items():
            audit.bump(k, int(v))
        after = _psql_count()
        delta = (after - before) if (before >= 0 and after >= 0) else -1
        audit.note(f"after voc count: {after} (delta={delta})")
        log.info(
            "harvest4 H6 hardware_fr 완료: before=%d after=%d delta=%d inserted=%d",
            before, after, delta, counters.get("inserted", 0),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
