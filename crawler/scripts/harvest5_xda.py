"""Harvest 5 V1 — XDA news_tag 정식 collector 1회 수집.

목표
  - news_tag 정식 채택 (포럼 차단, news 200 OK 확인 — discovery 완료).
  - 9 카테고리 (samsung/galaxy/fold/z-flip/watch/buds/tab/a/one-ui) × 20 cards
    + RSS fallback (samsung) — Galaxy 키워드 필터 후 50+ 건 목표.
  - 사이트 활성화: platforms.is_active = True (XDA 는 5/16 이후 미수집 상태).

4중 안전장치
  #1 PRESERVE_EXISTING — BaseCrawler.save() 의 INSERT ... ON CONFLICT
     (platform_id, external_id) DO NOTHING + content_hash 본문 중복 사전 차단.
  #2 DRY_RUN=1 이면 fetch/parse 까지만, save() 생략.
  #3 audit JSONL round=harvest5, track=V1 (start/end 매칭).
  #4 self-report drift ±10% — psql before/after count 직접 측정.

사용
  ROUND=harvest5 DRY_RUN=0 python -m scripts.harvest5_xda
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
from platforms.xda import XDACrawler  # noqa: E402

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("harvest5_xda")


def _is_truthy(name: str) -> bool:
    return (os.getenv(name, "") or "").strip().lower() in ("1", "true", "yes", "on")


def _env_snapshot() -> Dict[str, Any]:
    return {
        "DRY_RUN": _is_truthy("DRY_RUN"),
        "PRESERVE_EXISTING": True,
        "round": os.getenv("ROUND", "harvest5").strip() or "harvest5",
        "track": "V1",
    }


async def _execute_crawl(dry: bool) -> Dict[str, int]:
    c = XDACrawler()
    t0 = time.time()
    if dry:
        raw = await c.crawl()
        elapsed = time.time() - t0
        log.info("DRY_RUN — raw=%d (save 생략) %.1fs", len(raw), elapsed)
        return {"fetched_raw": len(raw), "inserted": 0, "elapsed_s": int(elapsed)}
    result = await c.run()
    elapsed = time.time() - t0
    saved = int(result.get("items_collected", 0) or 0)
    log.info("RUN 완료 — saved=%d %.1fs", saved, elapsed)
    return {"fetched_raw": -1, "inserted": saved, "elapsed_s": int(elapsed)}


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
        "= (SELECT id FROM platforms WHERE code='xda');",
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


def _activate_platform() -> bool:
    """platforms.is_active = True 로 재활성화 (XDA 는 현재 False)."""
    import subprocess
    cmd = [
        "psql",
        "-h", os.getenv("PGHOST", "127.0.0.1"),
        "-p", os.getenv("PGPORT", "5434"),
        "-U", os.getenv("PGUSER", "signalforge"),
        "-d", os.getenv("PGDATABASE", "signalforge"),
        "-tAc",
        "UPDATE platforms SET is_active=true WHERE code='xda' "
        "RETURNING id;",
    ]
    env = os.environ.copy()
    env.setdefault("PGPASSWORD", "signalforge_pass")
    try:
        out = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=10
        )
        ok = bool((out.stdout or "").strip())
        log.info("platform xda is_active=True %s", "(ok)" if ok else "(fail)")
        return ok
    except Exception as e:
        log.warning("platform 활성화 실패: %s", e)
        return False


def main() -> int:
    env = _env_snapshot()
    dry = bool(env["DRY_RUN"])
    mode = "dry_run" if dry else "preserve_existing"

    # DRY 가 아니면 비활성 platform 도 활성화 (4번째 safety 와 무관).
    if not dry:
        _activate_platform()

    before = _psql_count()
    with record_run(
        script="harvest5_xda",
        mode=mode,
        env=env,
    ) as audit:
        audit.note(f"before voc count: {before}")
        try:
            counters = asyncio.run(_execute_crawl(dry))
        except Exception as e:
            audit.note(f"crawl error: {e}")
            raise
        for k, v in counters.items():
            audit.bump(k, int(v))
        after = _psql_count()
        delta = (after - before) if (before >= 0 and after >= 0) else -1
        audit.note(f"after voc count: {after} (delta={delta})")
        log.info(
            "harvest5 V1 xda 완료: before=%d after=%d delta=%d inserted=%d",
            before, after, delta, counters.get("inserted", 0),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
