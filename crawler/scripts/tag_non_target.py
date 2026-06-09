"""Harvest 7 — Track X2: GSMArena/전 플랫폼 경쟁사 mapped voc 의
categories 배열에 'non_target' 태그를 비파괴적으로 추가한다.

배경
----
Discovery 권장 옵션 A 의 의도: '경쟁사 댓글' 을 데이터 손실 없이 분류해
dedup·후속 분석에서 식별 가능하게 만든다. GSMArena NULL 은 실측 0건이므로
대신 *mapped* 경쟁사 (AP=iPhone, PX=Pixel) 시리즈에 카테고리를 부여한다.

PRESERVE 원칙
-------------
- product_id, unmapped_reason, content 일체 변경 금지.
- categories 가 NULL 인 경우 ARRAY['non_target']::varchar[] 로 초기화.
- 'non_target' 이 이미 있으면 skip (idempotent).
- AP/PX 외 시리즈 (Samsung 등) 는 절대 손대지 않음.

DRY_RUN=1 이면 카운트만 출력하고 UPDATE 는 실행하지 않는다.

audit
-----
JSONL 파일 한 줄당 1 이벤트:
  - {"event":"start", ...}
  - {"event":"end",   "tagged": int, "skipped": int, ...}
경로: /home/koopark/claude/SignalForge/reports/audit_non_target_harvest7.jsonl

실행
----
DATABASE_URL=postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge \\
  TAG_NON_TARGET_DRY_RUN=0 \\
  /home/koopark/claude/SignalForge/.venv/bin/python \\
  /home/koopark/claude/SignalForge/crawler/scripts/tag_non_target.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("tag_non_target")

DATABASE_URL = os.getenv("DATABASE_URL", "")
DRY_RUN = os.getenv("TAG_NON_TARGET_DRY_RUN", "1") == "1"
AUDIT_PATH = os.getenv(
    "TAG_NON_TARGET_AUDIT_PATH",
    "/home/koopark/claude/SignalForge/reports/audit_non_target_harvest7.jsonl",
)
NON_TARGET_SERIES = ("AP", "PX")  # Apple iPhone, Google Pixel


# ─────────────────────────────────────────────────────────────────────────
# 핵심 SQL — UPDATE 는 1회만 호출 (배치 불필요, 858건 규모)
# ─────────────────────────────────────────────────────────────────────────
_COUNT_SQL = text(
    """
    SELECT
      COUNT(*) AS total_candidates,
      COUNT(*) FILTER (WHERE 'non_target' = ANY(v.categories)) AS already_tagged,
      COUNT(*) FILTER (WHERE 'non_target' <> ALL(COALESCE(v.categories, '{}'::varchar[]))
                       OR v.categories IS NULL) AS to_tag
    FROM voc_records v
    JOIN products pr ON pr.id = v.product_id
    WHERE pr.series_code = ANY(:series)
    """
)

# UPDATE: 비파괴적 array append. categories NULL→ARRAY['non_target'] / 기존 array→ append.
_UPDATE_SQL = text(
    """
    UPDATE voc_records v
    SET categories = (
      CASE
        WHEN v.categories IS NULL
          THEN ARRAY['non_target']::varchar[]
        ELSE v.categories || 'non_target'::varchar
      END
    )
    FROM products pr
    WHERE v.product_id = pr.id
      AND pr.series_code = ANY(:series)
      AND ('non_target' <> ALL(COALESCE(v.categories, '{}'::varchar[]))
           OR v.categories IS NULL)
    RETURNING v.id
    """
)


def _write_audit(event: dict) -> None:
    os.makedirs(os.path.dirname(AUDIT_PATH), exist_ok=True)
    event.setdefault("ts", datetime.now(timezone.utc).isoformat())
    event.setdefault("round", "harvest7")
    event.setdefault("track", "X2")
    with open(AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


async def run() -> dict:
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL 미설정")

    engine = create_async_engine(DATABASE_URL, future=True)

    started_at = datetime.now(timezone.utc).isoformat()
    _write_audit({
        "event": "start",
        "dry_run": DRY_RUN,
        "series": list(NON_TARGET_SERIES),
        "started_at": started_at,
    })
    log.info("start dry_run=%s series=%s", DRY_RUN, NON_TARGET_SERIES)

    summary: dict = {"dry_run": DRY_RUN, "series": list(NON_TARGET_SERIES)}

    async with engine.begin() as conn:
        # 1) 사전 카운트 (변경 전 사진)
        row = (
            await conn.execute(_COUNT_SQL, {"series": list(NON_TARGET_SERIES)})
        ).mappings().one()
        summary["before"] = dict(row)
        log.info("before: %s", summary["before"])

        # 2) UPDATE
        if DRY_RUN:
            summary["tagged"] = 0
            summary["mode"] = "dry_run"
        else:
            result = await conn.execute(
                _UPDATE_SQL, {"series": list(NON_TARGET_SERIES)}
            )
            tagged_ids = result.fetchall()
            summary["tagged"] = len(tagged_ids)
            summary["mode"] = "applied"
            log.info("tagged %d rows", summary["tagged"])

        # 3) 사후 카운트
        row2 = (
            await conn.execute(_COUNT_SQL, {"series": list(NON_TARGET_SERIES)})
        ).mappings().one()
        summary["after"] = dict(row2)
        log.info("after: %s", summary["after"])

    await engine.dispose()

    finished_at = datetime.now(timezone.utc).isoformat()
    _write_audit({
        "event": "end",
        "dry_run": DRY_RUN,
        "tagged": summary["tagged"],
        "before": summary["before"],
        "after": summary["after"],
        "started_at": started_at,
        "finished_at": finished_at,
    })
    return summary


def main() -> int:
    summary = asyncio.run(run())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
