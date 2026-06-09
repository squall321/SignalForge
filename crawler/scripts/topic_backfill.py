"""voc_records.topics 백필 — topic_classifier (Track B, R8 / R19 안전장치).

대상: topics IS NULL OR cardinality(topics)=0 인 행 (id 내림차순 키셋).
입력: content_translated COALESCE content_original.

환경변수:
  DATABASE_URL            (필수, postgresql+asyncpg://… )
  TOPIC_BACKFILL_LIMIT    총 처리 상한 (기본 50000, 0=무제한)
  TOPIC_BACKFILL_BATCH    배치 크기 (기본 1000)
  TOPIC_ALLOW_OTHER       '1'/'0' (기본 '0' — 매칭 없으면 빈 채로 둠)
  TOPIC_RECLASSIFY_ALL    '1' 시 이미 분류된 행도 *전체 덮어쓰기* (위험)

R19 안전장치 (모든 백필 공통):
  DRY_RUN=true            (기본 true) — UPDATE 안 함, 카운트만
  PRESERVE_EXISTING=true  (기본 true) — 이미 분류된 행은 SQL 단계에서 skip.
                          RECLASSIFY_ALL 과 동시 사용 시 PRESERVE 가 우선 (안전 default).
  BACKUP_BEFORE=true      (기본 false) — UPDATE 전에 reports/topic_backup_<ts>.json
                          에 (id, topics) 전체 snapshot.  RECLASSIFY 모드 권장.
  CONFIRM=I_KNOW          (RECLASSIFY_ALL + PRESERVE_EXISTING=false 시 필수)
                          R18 사고 같은 *전체 덮어쓰기* 를 의도했음을 명시.

실행 (R19 권장 — dry-run 먼저):
  DATABASE_URL=postgresql+asyncpg://... \\
    DRY_RUN=true PRESERVE_EXISTING=true \\
    /home/koopark/claude/SignalForge/.venv/bin/python \\
    -m scripts.topic_backfill
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)

from nlp.topic_classifier import classify_topic  # noqa: E402
from insight.backfill_audit import record_run  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("topic_backfill")


def _env_bool(name: str, default: bool) -> bool:
    """환경변수를 bool 로 — '1'/'true'/'yes' 만 True (대소문자 무시)."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


DATABASE_URL = os.getenv("DATABASE_URL", "")
LIMIT = int(os.getenv("TOPIC_BACKFILL_LIMIT", "50000"))
BATCH = int(os.getenv("TOPIC_BACKFILL_BATCH", "1000"))
ALLOW_OTHER = os.getenv("TOPIC_ALLOW_OTHER", "0") == "1"
RECLASSIFY_ALL = os.getenv("TOPIC_RECLASSIFY_ALL", "0") == "1"

# R19 안전 기본값 — DRY+PRESERVE.
DRY_RUN = _env_bool("DRY_RUN", True)
PRESERVE_EXISTING = _env_bool("PRESERVE_EXISTING", True)
BACKUP_BEFORE = _env_bool("BACKUP_BEFORE", False)
CONFIRM = os.getenv("CONFIRM", "").strip()


def _select_sql(reclassify_all: bool, preserve_existing: bool):
    """모드별 SELECT SQL — PRESERVE_EXISTING 이 우선."""
    if reclassify_all and not preserve_existing:
        return text(
            """
            SELECT id, topics, content_translated, content_original
            FROM voc_records
            WHERE content_original IS NOT NULL
              AND id < :cursor
            ORDER BY id DESC
            LIMIT :batch
            """
        )
    return text(
        """
        SELECT id, topics, content_translated, content_original
        FROM voc_records
        WHERE (topics IS NULL OR cardinality(topics) = 0)
          AND content_original IS NOT NULL
          AND id < :cursor
        ORDER BY id DESC
        LIMIT :batch
        """
    )


UPDATE_SQL = text(
    """
    UPDATE voc_records
    SET topics = :topics
    WHERE id = :id
    """
)


def _backup_path() -> Path:
    """reports/topic_backup_<UTC-ts>.json — 절대 경로."""
    repo_root = Path(__file__).resolve().parents[2]
    base = Path(os.getenv("BACKUP_DIR", str(repo_root / "reports")))
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    return base / f"topic_backup_{ts}.json"


async def _write_backup(Session, path: Path, reclassify_all: bool) -> int:
    """현재 (id, topics) snapshot 을 JSON 으로 저장 → 복원 가능."""
    # RECLASSIFY 모드: 전체 행을 백업해야 진정한 롤백 가능.
    # 일반 모드: topics IS NULL 행은 백업할 게 없으므로 분류 완료된 행만.
    if reclassify_all:
        where = "WHERE topics IS NOT NULL AND cardinality(topics) > 0"
    else:
        where = "WHERE topics IS NOT NULL AND cardinality(topics) > 0"
    async with Session() as db:
        rows = (await db.execute(text(
            f"SELECT id, topics FROM voc_records {where} ORDER BY id"
        ))).all()
    payload = [{"id": int(r.id), "topics": list(r.topics or [])} for r in rows]
    path.write_text(
        json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "reclassify_all": reclassify_all,
            "row_count": len(payload),
            "rows": payload,
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(payload)


def _validate_flags() -> None:
    """전체 덮어쓰기 같은 위험 조합은 CONFIRM 필수."""
    dangerous = RECLASSIFY_ALL and not PRESERVE_EXISTING and not DRY_RUN
    if dangerous and CONFIRM != "I_KNOW":
        log.error(
            "RECLASSIFY_ALL + PRESERVE_EXISTING=false + DRY_RUN=false 는 R18 사고 재현 위험. "
            "의도가 맞으면 CONFIRM=I_KNOW 추가 후 재실행."
        )
        sys.exit(3)


def _mode_label() -> str:
    if DRY_RUN:
        return "dry_run"
    if PRESERVE_EXISTING:
        return "preserve_existing"
    if RECLASSIFY_ALL:
        return "full_reclassify"
    return "live"


async def main() -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL 미설정")
        sys.exit(2)

    _validate_flags()

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    mode = _mode_label()
    env_snapshot = {
        "DRY_RUN": DRY_RUN,
        "PRESERVE_EXISTING": PRESERVE_EXISTING,
        "BACKUP_BEFORE": BACKUP_BEFORE,
        "TOPIC_RECLASSIFY_ALL": RECLASSIFY_ALL,
        "TOPIC_ALLOW_OTHER": ALLOW_OTHER,
        "TOPIC_BACKFILL_LIMIT": LIMIT,
        "TOPIC_BACKFILL_BATCH": BATCH,
    }

    with record_run(script="topic_backfill", mode=mode, env=env_snapshot) as audit:
        # ── 0. 백업 (옵션) ──────────────────────────────────────────────
        if BACKUP_BEFORE and not DRY_RUN:
            path = _backup_path()
            n = await _write_backup(Session, path, RECLASSIFY_ALL)
            audit.set_backup_path(path)
            audit.note(f"backup row_count={n}")
            log.info(f"backup 저장 → {path} ({n:,}행)")
        elif BACKUP_BEFORE and DRY_RUN:
            log.info("DRY_RUN 이라 backup 생략")

        # ── 1. 대상 카운트 ─────────────────────────────────────────────
        async with Session() as db:
            if RECLASSIFY_ALL and not PRESERVE_EXISTING:
                total_sql = """
                    SELECT count(*) FROM voc_records
                    WHERE content_original IS NOT NULL
                """
            else:
                total_sql = """
                    SELECT count(*) FROM voc_records
                    WHERE (topics IS NULL OR cardinality(topics) = 0)
                      AND content_original IS NOT NULL
                """
            total = (await db.execute(text(total_sql))).scalar_one()
        audit.bump("target_total", int(total))
        log.info(
            f"백필 대상: {total:,}건 (mode={mode}, LIMIT={LIMIT or '무제한'}, "
            f"BATCH={BATCH}, allow_other={ALLOW_OTHER})"
        )

        # ── 2. 메인 루프 ───────────────────────────────────────────────
        SELECT_SQL = _select_sql(RECLASSIFY_ALL, PRESERVE_EXISTING)
        seen = matched = others = preserved = updated = 0
        cursor = 1 << 62
        while True:
            async with Session() as db:
                rows = (
                    await db.execute(SELECT_SQL, {"batch": BATCH, "cursor": cursor})
                ).all()
                if not rows:
                    log.info("  더 이상 처리할 행 없음 — 종료")
                    break

                ups = []
                for r in rows:
                    seen += 1
                    # PRESERVE_EXISTING 추가 안전망 — SQL 에서 걸렀어도 한 번 더.
                    if PRESERVE_EXISTING and r.topics and len(r.topics) > 0:
                        preserved += 1
                        continue
                    txt = r.content_translated or r.content_original or ""
                    tps = classify_topic(txt, allow_other=ALLOW_OTHER)
                    if not tps:
                        # RECLASSIFY 모드에서만 빈 라벨로 reset 가능.
                        if RECLASSIFY_ALL and not PRESERVE_EXISTING:
                            ups.append({"id": r.id, "topics": []})
                        continue
                    ups.append({"id": r.id, "topics": tps})
                    if tps == ["other"]:
                        others += 1
                    else:
                        matched += 1

                if ups and not DRY_RUN:
                    await db.execute(UPDATE_SQL, ups)
                    await db.commit()
                    updated += len(ups)
                    # R25 트랙 D — UPDATE 된 voc_records.id 누적.
                    audit.add_affected_ids(
                        "topics_updated", [u["id"] for u in ups]
                    )

                cursor = rows[-1].id
                log.info(
                    f"  진행 누적 {seen:,} / 매치 {matched:,} / other {others:,} / "
                    f"preserve {preserved:,} (배치 UPDATE={'DRY' if DRY_RUN else len(ups)}, "
                    f"cursor={cursor})"
                )

            if LIMIT and seen >= LIMIT:
                log.info(f"LIMIT {LIMIT:,} 도달 — 종료")
                break

        audit.bump("seen", seen)
        audit.bump("matched", matched)
        audit.bump("others", others)
        audit.bump("preserved", preserved)
        audit.bump("updated", updated)

        hit_pct = (matched + others) * 100.0 / max(seen, 1)
        log.info(
            f"=== 백필 완료: 시도 {seen:,} / 매치 {matched:,} / other {others:,} / "
            f"preserve {preserved:,} / UPDATE {updated:,} / hit {hit_pct:.2f}% "
            f"(mode={mode}) ==="
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
