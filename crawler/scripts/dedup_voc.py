"""R14 트랙 A3 — content_hash 기반 voc_records 중복 정리 (R19 안전장치).

전략:
- (platform_id, content_hash) 그룹 중 같은 본문이 2회 이상이면
  *가장 오래된 행(min id) 1건만 유지*, 나머지는 삭제.
- voc_keywords 는 ON DELETE CASCADE 가 걸려 있어 자동 정리.
- content_hash IS NULL (30자 미만) 행은 건드리지 않음.

사용:
    python -m scripts.dedup_voc                     # R19: 기본 DRY (안전).
    python -m scripts.dedup_voc --execute           # 실 삭제 (BACKUP 권장)
    python -m scripts.dedup_voc --execute --backup  # 삭제 전 ID 백업 → JSON
    python -m scripts.dedup_voc --dry               # 명시적 dry-run (legacy 호환)

환경변수:
  DRY_RUN=true       (기본 true) — --execute 가 명시되지 않으면 강제 dry.
  BACKUP_BEFORE=true (기본 false) — --backup 와 동일.
  CONFIRM=I_KNOW     실 삭제 시 환경 기반 자동화 보호 — CI/scheduler 호출 시 필수.
                     대화형 호출에서는 ``--yes`` 로 대체.

산출:
    - groups_with_excess: 중복 그룹 수
    - excess_total:       살릴 행 제외 삭제 대상 행 수
    - deleted:            실제 삭제된 행 수 (dry-run 시 0)
    - backup_path:        --backup 시 reports/dedup_backup_<ts>.json
    - before / after duplicate_rate (%)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.backfill_audit import record_run  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("dedup_voc")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge",
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _backup_path() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    base = Path(os.getenv("BACKUP_DIR", str(repo_root / "reports")))
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    return base / f"dedup_backup_{ts}.json"


async def measure_duplicate_rate(db: AsyncSession) -> Tuple[int, int, float]:
    """현재 (platform_id, content_hash) 기준 중복률 측정.

    Returns (eligible_rows, excess_rows, duplicate_rate_pct).
    """
    row = (await db.execute(text(
        """
        WITH counted AS (
          SELECT platform_id, content_hash, COUNT(*) AS c
          FROM voc_records
          WHERE content_hash IS NOT NULL
          GROUP BY 1, 2
        )
        SELECT
          COALESCE(SUM(c), 0)                                    AS eligible,
          COALESCE(SUM(c - 1) FILTER (WHERE c > 1), 0)            AS excess
        FROM counted
        """
    ))).one()
    eligible = int(row[0] or 0)
    excess = int(row[1] or 0)
    rate = round(100.0 * excess / eligible, 2) if eligible else 0.0
    return eligible, excess, rate


async def _write_delete_backup(db: AsyncSession, path: Path) -> int:
    """삭제 *대상* (id, platform_id, content_hash, content_original)
    snapshot 을 JSON 으로 저장.  실제 삭제 전에 호출."""
    rows = (await db.execute(text(
        """
        WITH grouped AS (
          SELECT platform_id, content_hash, MIN(id) AS keep_id
          FROM voc_records
          WHERE content_hash IS NOT NULL
          GROUP BY 1, 2
          HAVING COUNT(*) > 1
        )
        SELECT v.id, v.platform_id, v.content_hash, v.collected_at,
               substr(coalesce(v.content_original, ''), 1, 200) AS preview
        FROM voc_records v
        JOIN grouped g
          ON g.platform_id = v.platform_id AND g.content_hash = v.content_hash
        WHERE v.id <> g.keep_id
        ORDER BY v.id
        """
    ))).all()
    payload = [
        {
            "id": int(r.id),
            "platform_id": int(r.platform_id) if r.platform_id is not None else None,
            "content_hash": r.content_hash,
            "collected_at": (r.collected_at.isoformat() if r.collected_at else None),
            "preview": r.preview,
        }
        for r in rows
    ]
    path.write_text(
        json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "row_count": len(payload),
            "rows": payload,
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(payload)


async def dedup_run(dry: bool = False, backup: bool = False) -> dict:
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    mode = "dry_run" if dry else ("execute_with_backup" if backup else "execute")
    env_snapshot = {
        "DRY_RUN": dry,
        "BACKUP_BEFORE": backup,
    }

    result: dict = {}
    backup_count = 0
    backup_path: Path | None = None

    with record_run(script="dedup_voc", mode=mode, env=env_snapshot) as audit:
        async with SessionLocal() as db:
            before_elig, before_excess, before_rate = await measure_duplicate_rate(db)
            logger.info(
                "before: eligible=%d excess=%d duplicate_rate=%.2f%%",
                before_elig, before_excess, before_rate,
            )
            audit.bump("eligible_before", before_elig)
            audit.bump("excess_before", before_excess)

            # 삭제 직전 백업 (실 실행 시에만).
            if backup and not dry:
                backup_path = _backup_path()
                backup_count = await _write_delete_backup(db, backup_path)
                audit.set_backup_path(backup_path)
                audit.note(f"delete_backup row_count={backup_count}")
                logger.info("backup 저장 → %s (%d행)", backup_path, backup_count)

            # 살릴 행 = 그룹별 MIN(id). 나머지를 삭제.
            if dry:
                row = (await db.execute(text(
                    """
                    WITH grouped AS (
                      SELECT platform_id, content_hash,
                             MIN(id) AS keep_id, COUNT(*) AS c
                      FROM voc_records
                      WHERE content_hash IS NOT NULL
                      GROUP BY 1, 2
                      HAVING COUNT(*) > 1
                    )
                    SELECT
                      COUNT(*)                       AS groups_with_excess,
                      COALESCE(SUM(c - 1), 0)        AS excess_total
                    FROM grouped
                    """
                ))).one()
                groups = int(row[0] or 0)
                excess_total = int(row[1] or 0)
                deleted = 0
                logger.info(
                    "[dry] groups_with_excess=%d excess_total=%d",
                    groups, excess_total,
                )
            else:
                # R25 트랙 D — DELETE...RETURNING id 로 삭제된 PK 캡처.
                res = await db.execute(text(
                    """
                    WITH grouped AS (
                      SELECT platform_id, content_hash, MIN(id) AS keep_id
                      FROM voc_records
                      WHERE content_hash IS NOT NULL
                      GROUP BY 1, 2
                      HAVING COUNT(*) > 1
                    )
                    DELETE FROM voc_records v
                    USING grouped g
                    WHERE v.platform_id = g.platform_id
                      AND v.content_hash = g.content_hash
                      AND v.id <> g.keep_id
                    RETURNING v.id
                    """
                ))
                deleted_ids = [row[0] for row in res.fetchall()]
                deleted = len(deleted_ids)
                await db.commit()
                # archive: 삭제 PK 보관 (drift cross-check 핵심)
                audit.add_affected_ids("voc_deleted", deleted_ids)
                logger.info("deleted=%d", deleted)
                row = (await db.execute(text(
                    """
                    WITH grouped AS (
                      SELECT 1 AS dummy
                      FROM voc_records
                      WHERE content_hash IS NOT NULL
                      GROUP BY platform_id, content_hash
                      HAVING COUNT(*) > 1
                    )
                    SELECT COUNT(*) FROM grouped
                    """
                ))).one()
                groups = int(row[0] or 0)
                excess_total = deleted

            after_elig, after_excess, after_rate = await measure_duplicate_rate(db)
            logger.info(
                "after: eligible=%d excess=%d duplicate_rate=%.2f%%",
                after_elig, after_excess, after_rate,
            )
            audit.bump("groups_with_excess", groups)
            audit.bump("excess_total", int(excess_total))
            audit.bump("deleted", int(deleted))

        result = {
            "before": {"eligible": before_elig, "excess": before_excess, "rate": before_rate},
            "after": {"eligible": after_elig, "excess": after_excess, "rate": after_rate},
            "groups_with_excess_before": groups if dry else groups,
            "excess_total": excess_total,
            "deleted": deleted,
            "dry_run": dry,
            "backup_path": (str(backup_path) if backup_path else None),
            "backup_count": backup_count,
        }

    await engine.dispose()
    return result


def _resolve_mode(args: argparse.Namespace) -> tuple[bool, bool]:
    """CLI 플래그 + 환경변수를 합성한 (dry, backup) 반환.

    안전 규칙:
      - ``--execute`` 가 *없으면* 무조건 dry (환경변수와 무관).
      - ``--execute`` 가 있어도 CONFIRM=I_KNOW 또는 --yes 가 필요.
    """
    # 명시적 --dry 우선 (legacy 호환).
    if args.dry:
        return True, args.backup or _env_bool("BACKUP_BEFORE", False)

    if not args.execute:
        # 안전 기본값 — dry-run.
        return True, False

    # --execute 경로 — 비대화형 보호.
    if not args.yes and os.getenv("CONFIRM", "").strip() != "I_KNOW":
        logger.error(
            "실 삭제는 --yes 또는 CONFIRM=I_KNOW 필요. dry-run 으로 우선 검토.",
        )
        sys.exit(3)
    return False, args.backup or _env_bool("BACKUP_BEFORE", True)


def main() -> int:
    p = argparse.ArgumentParser(description="voc_records content_hash dedup")
    p.add_argument("--dry", action="store_true", help="삭제 없이 카운트만 (legacy 명시).")
    p.add_argument("--execute", action="store_true", help="실 삭제 수행 (기본 dry).")
    p.add_argument("--backup", action="store_true", help="삭제 전 ID 목록을 JSON 으로 저장.")
    p.add_argument("--yes", action="store_true", help="대화형 확인 생략 (--execute 필수).")
    args = p.parse_args()
    dry, backup = _resolve_mode(args)
    result = asyncio.run(dedup_run(dry=dry, backup=backup))
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
