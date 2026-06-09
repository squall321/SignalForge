"""Harvest 3 P3 — Hardware.fr 보드 확장 1회 수집.

목표
  - 카테고리 3 → 6 확장 + BACKFILL_PAGES 2 → 5 + audit round=harvest3p.
  - 기존 BaseCrawler.save() 안의 INSERT ... ON CONFLICT DO NOTHING 으로
    PRESERVE_EXISTING 자동 보장 (4중 안전장치 #1).
  - DRY_RUN=1 이면 fetch/parse 까지만 하고 DB 저장 생략 (#2).
  - record_run 컨텍스트로 audit JSONL start/end + round 라벨 정확 기록 (#4).

사용
  ROUND=harvest3p DRY_RUN=0 \
    HARDWARE_FR_BACKFILL_PAGES=5 \
    HARDWARE_FR_MAX_THREADS=24 \
    python -m scripts.harvest3p_hardware_fr

검증 포인트
  - reports/backfill_audit.jsonl 에 start(=record_run 생성) / end(=context 종료)
    이벤트가 한 줄로 매칭되어야 함 (verify D 의 'audit end event 부재' 재발 방지).
  - INSERT 카운트 = save() 반환 saved.
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
log = logging.getLogger("harvest3p_hardware_fr")


def _is_truthy(name: str) -> bool:
    return (os.getenv(name, "") or "").strip().lower() in ("1", "true", "yes", "on")


def _env_snapshot() -> Dict[str, Any]:
    """audit 에 박을 환경변수 스냅샷 (Harvest 3 P3 의 4중 안전장치 가시화)."""
    return {
        "DRY_RUN": _is_truthy("DRY_RUN"),
        "PRESERVE_EXISTING": True,  # ON CONFLICT 로 자동
        "HARDWARE_FR_BACKFILL_PAGES": int(
            os.getenv("HARDWARE_FR_BACKFILL_PAGES", "5")
        ),
        "HARDWARE_FR_MAX_THREADS": int(
            os.getenv("HARDWARE_FR_MAX_THREADS", "24")
        ),
        "HARDWARE_FR_THREAD_PAGES": int(
            os.getenv("HARDWARE_FR_THREAD_PAGES", "1")
        ),
        "HARDWARE_FR_MAX_POSTS": int(
            os.getenv("HARDWARE_FR_MAX_POSTS", "500")
        ),
        "round": os.getenv("ROUND", "harvest3p").strip() or "harvest3p",
    }


async def _execute_crawl(dry: bool) -> Dict[str, int]:
    """크롤러 1회 실행 → counters dict 반환.

    - dry=True 인 경우: crawl() 까지만 호출, save() 생략 → INSERT 0.
    - dry=False: 정상 run() 호출 → BaseCrawler 의 normalize/process/save 수행.
    """
    c = HardwareFRCrawler()
    t0 = time.time()
    if dry:
        raw = await c.crawl()
        elapsed = time.time() - t0
        log.info(
            "DRY_RUN — raw=%d (save 생략) %.1fs", len(raw), elapsed
        )
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
        "fetched_raw": -1,  # run() 내부에서 자체 로깅
        "inserted": saved,
        "elapsed_s": int(elapsed),
    }


def _psql_count() -> int:
    """sanity check 용 — 현재 hardware_fr voc 총건수."""
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
    mode = "dry_run" if dry else "preserve"  # ON CONFLICT 로 preserve 보장

    before = _psql_count()
    with record_run(
        script="harvest3p_hardware_fr",
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
            "harvest3p hardware_fr 완료: before=%d after=%d delta=%d inserted=%d",
            before, after, delta, counters.get("inserted", 0),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
