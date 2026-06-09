"""voc_records.sentiment_* 백필 — 언어 인지 (R19 트랙 E).

대상: ``sentiment_label IS NULL OR sentiment_score IS NULL`` 인 행.
입력: 한국어는 ``content_original``, 그 외는 ``content_translated`` 우선,
      없으면 ``content_original``.

R19 안전장치:
  DRY_RUN=true            (기본 true) — UPDATE 없이 카운트만.
  PRESERVE_EXISTING=true  (기본 true) — 이미 sentiment_label 이 있으면 skip.
                          NEUTRAL 만 재계산하려면 SCOPE=neutral_only.
  BACKUP_BEFORE=true      (기본 false) — UPDATE 전에 reports/sentiment_backup_<ts>.json
                          에 (id, sentiment_score, sentiment_label) snapshot.
  CONFIRM=I_KNOW          (PRESERVE_EXISTING=false + DRY_RUN=false 시 필수)

기타 환경변수:
  DATABASE_URL                (필수)
  SENT_BACKFILL_LIMIT         총 처리 상한 (기본 50000, 0=무제한)
  SENT_BACKFILL_BATCH         배치 크기 (기본 1000)
  SCOPE                       'null_only'(기본) | 'neutral_only' | 'all'

실행:
  DATABASE_URL=postgresql+asyncpg://... DRY_RUN=true PRESERVE_EXISTING=true \\
    /home/koopark/claude/SignalForge/.venv/bin/python -m scripts.sentiment_backfill
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

from nlp.sentiment import analyze  # noqa: E402
from insight.backfill_audit import record_run  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("sentiment_backfill")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


DATABASE_URL = os.getenv("DATABASE_URL", "")
LIMIT = int(os.getenv("SENT_BACKFILL_LIMIT", "50000"))
BATCH = int(os.getenv("SENT_BACKFILL_BATCH", "1000"))
SCOPE = os.getenv("SCOPE", "null_only").strip().lower()  # null_only|neutral_only|all

DRY_RUN = _env_bool("DRY_RUN", True)
PRESERVE_EXISTING = _env_bool("PRESERVE_EXISTING", True)
BACKUP_BEFORE = _env_bool("BACKUP_BEFORE", False)
CONFIRM = os.getenv("CONFIRM", "").strip()


_SCOPE_WHERE = {
    "null_only":    "(sentiment_label IS NULL OR sentiment_score IS NULL)",
    "neutral_only": "sentiment_label = 'neutral'",
    "all":          "TRUE",
}


def _select_sql(scope: str, preserve_existing: bool):
    """모드별 SELECT.  PRESERVE_EXISTING 은 SCOPE=null_only 로 강제."""
    effective_scope = "null_only" if preserve_existing else scope
    where = _SCOPE_WHERE.get(effective_scope, _SCOPE_WHERE["null_only"])
    return text(
        f"""
        SELECT id, sentiment_score, sentiment_label,
               language_detected, content_translated, content_original
        FROM voc_records
        WHERE {where}
          AND content_original IS NOT NULL
          AND id < :cursor
        ORDER BY id DESC
        LIMIT :batch
        """
    ), effective_scope


UPDATE_SQL = text(
    """
    UPDATE voc_records
    SET sentiment_score = :ss, sentiment_label = :sl
    WHERE id = :id
    """
)


def _backup_path() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    base = Path(os.getenv("BACKUP_DIR", str(repo_root / "reports")))
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    return base / f"sentiment_backup_{ts}.json"


async def _write_backup(Session, path: Path) -> int:
    """기존 sentiment_label 이 있는 행만 백업 (NULL 은 복원할 게 없음)."""
    async with Session() as db:
        rows = (await db.execute(text(
            "SELECT id, sentiment_score, sentiment_label "
            "FROM voc_records WHERE sentiment_label IS NOT NULL ORDER BY id"
        ))).all()
    payload = [
        {
            "id": int(r.id),
            "sentiment_score": (float(r.sentiment_score) if r.sentiment_score is not None else None),
            "sentiment_label": r.sentiment_label,
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


def _validate_flags() -> None:
    dangerous = (not PRESERVE_EXISTING) and (not DRY_RUN) and SCOPE in ("all", "neutral_only")
    if dangerous and CONFIRM != "I_KNOW":
        log.error(
            "PRESERVE_EXISTING=false + DRY_RUN=false + SCOPE=%s 는 기존 sentiment 덮어쓰기. "
            "의도가 맞으면 CONFIRM=I_KNOW 추가.", SCOPE,
        )
        sys.exit(3)


def _mode_label() -> str:
    if DRY_RUN:
        return "dry_run"
    if PRESERVE_EXISTING:
        return "preserve_existing"
    return f"scope:{SCOPE}"


async def main() -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL 미설정")
        sys.exit(2)

    _validate_flags()

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    mode = _mode_label()
    SELECT_SQL, effective_scope = _select_sql(SCOPE, PRESERVE_EXISTING)
    env_snapshot = {
        "DRY_RUN": DRY_RUN,
        "PRESERVE_EXISTING": PRESERVE_EXISTING,
        "BACKUP_BEFORE": BACKUP_BEFORE,
        "SCOPE": SCOPE,
        "effective_scope": effective_scope,
        "SENT_BACKFILL_LIMIT": LIMIT,
        "SENT_BACKFILL_BATCH": BATCH,
    }

    with record_run(script="sentiment_backfill", mode=mode, env=env_snapshot) as audit:
        if BACKUP_BEFORE and not DRY_RUN:
            path = _backup_path()
            n = await _write_backup(Session, path)
            audit.set_backup_path(path)
            audit.note(f"backup row_count={n}")
            log.info(f"backup 저장 → {path} ({n:,}행)")
        elif BACKUP_BEFORE and DRY_RUN:
            log.info("DRY_RUN 이라 backup 생략")

        async with Session() as db:
            where = _SCOPE_WHERE[effective_scope]
            total = (await db.execute(text(
                f"SELECT count(*) FROM voc_records WHERE {where} AND content_original IS NOT NULL"
            ))).scalar_one()
        audit.bump("target_total", int(total))
        log.info(
            f"sentiment 백필 대상: {total:,}건 (mode={mode}, scope={effective_scope}, "
            f"LIMIT={LIMIT or '무제한'}, BATCH={BATCH})"
        )

        seen = updated = preserved = pos = neg = neu = 0
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
                    if PRESERVE_EXISTING and r.sentiment_label is not None:
                        preserved += 1
                        continue
                    lang = r.language_detected or "en"
                    if lang == "ko":
                        txt = r.content_original or ""
                    else:
                        txt = r.content_translated or r.content_original or ""
                    score, label = analyze(txt, lang=lang)
                    ups.append({"id": r.id, "ss": float(score), "sl": label})
                    if label == "positive":
                        pos += 1
                    elif label == "negative":
                        neg += 1
                    else:
                        neu += 1

                if ups and not DRY_RUN:
                    await db.execute(UPDATE_SQL, ups)
                    await db.commit()
                    updated += len(ups)

                cursor = rows[-1].id
                log.info(
                    f"  진행 누적 {seen:,} (pos {pos:,} / neg {neg:,} / neu {neu:,} / "
                    f"preserve {preserved:,}), 배치 UPDATE={'DRY' if DRY_RUN else len(ups)}, "
                    f"cursor={cursor}"
                )

            if LIMIT and seen >= LIMIT:
                log.info(f"LIMIT {LIMIT:,} 도달 — 종료")
                break

        audit.bump("seen", seen)
        audit.bump("positive", pos)
        audit.bump("negative", neg)
        audit.bump("neutral", neu)
        audit.bump("preserved", preserved)
        audit.bump("updated", updated)

        log.info(
            f"=== sentiment 백필 완료: 시도 {seen:,} / pos {pos:,} / neg {neg:,} / "
            f"neu {neu:,} / preserve {preserved:,} / UPDATE {updated:,} (mode={mode}) ==="
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
