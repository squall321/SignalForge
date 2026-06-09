"""topic LLM apply — Track A *limited batch* (R21, 2026-06-05).

목적
----
사전 보강 한계 (R10 0.678 → R20 0.500) 를 회복하기 위해 14b 직접 분류 결과를
*기존 단일 라벨 행* 에 *추가* 한다 (multi-label 확장, PRESERVE_EXISTING).

`topic_llm_refine.py` 와의 차이
  - refine: db ≠ llm 행을 `[llm_label]` *로 교체* (drift 케이스)
  - apply : *기존 라벨 유지 + LLM 라벨이 다르면 multi-label 로 확장* (보강)

대상 (기본)
  topics IS NOT NULL AND cardinality(topics) = 1
  AND topics[1] IN TARGETS
  AND content (translated|original) IS NOT NULL AND length >= 20

샘플링
  topic 당 균등 (기본 125건 × 4 topics = 500건).

DRY_RUN (기본 1)
  DB 갱신 없이 보고서만. 일치/확장 후보 카운트 + per-topic 패턴.

APPLY (TOPIC_APPLY_DRY_RUN=0)
  PRESERVE_EXISTING — 기존 topics 를 유지하고, llm_label 이 다르고 `other` 가
  아니면 추가하여 multi-label 화. 백업 테이블 자동 생성.

환경변수
  DATABASE_URL                  postgresql+asyncpg://...
  OLLAMA_BASE_URL               기본 http://127.0.0.1:11434/v1
  OLLAMA_EVAL_MODEL             기본 qwen2.5:14b
  TOPIC_APPLY_PER_TOPIC         기본 125 (4 topic = 500건)
  TOPIC_APPLY_TARGETS           기본 'positive_general,comparison,experience,negative_general'
  TOPIC_APPLY_SEED              기본 20260605
  TOPIC_APPLY_OUT_DIR           기본 reports/
  TOPIC_APPLY_DRY_RUN           '1'=DB 변경 없음 (기본), '0'=실제 적용
  TOPIC_APPLY_BACKUP_TABLE      기본 voc_topics_backup_r21_llm_apply
  TOPIC_APPLY_AUDIT_PATH        기본 reports/backfill_audit.jsonl

실행 (dry-run, 기본):
  DATABASE_URL=... \
    /home/koopark/claude/SignalForge/.venv/bin/python -m scripts.topic_llm_apply

실행 (실제 적용):
  TOPIC_APPLY_DRY_RUN=0 DATABASE_URL=... \
    /home/koopark/claude/SignalForge/.venv/bin/python -m scripts.topic_llm_apply
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    AsyncSession,
    create_async_engine,
)

from scripts.topic_eval import (  # noqa: E402
    call_llm,
    parse_llm_label,
)
from scripts.topic_llm_prompt_v2 import (  # noqa: E402
    build_prompt_v2,
    call_llm_v2,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("topic_llm_apply")

DATABASE_URL = os.getenv("DATABASE_URL", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
OLLAMA_EVAL_MODEL = os.getenv("OLLAMA_EVAL_MODEL", "qwen2.5:14b")
PER_TOPIC = int(os.getenv("TOPIC_APPLY_PER_TOPIC", "125"))
TARGETS = [
    t.strip()
    for t in os.getenv(
        "TOPIC_APPLY_TARGETS",
        "positive_general,comparison,experience,negative_general",
    ).split(",")
    if t.strip()
]
SEED = int(os.getenv("TOPIC_APPLY_SEED", "20260605"))
OUT_DIR = os.getenv(
    "TOPIC_APPLY_OUT_DIR", "/home/koopark/claude/SignalForge/reports"
)
DRY_RUN = os.getenv("TOPIC_APPLY_DRY_RUN", "1") == "1"
BACKUP_TABLE = os.getenv(
    "TOPIC_APPLY_BACKUP_TABLE", "voc_topics_backup_r21_llm_apply"
)
AUDIT_PATH = os.getenv(
    "TOPIC_APPLY_AUDIT_PATH",
    "/home/koopark/claude/SignalForge/reports/backfill_audit.jsonl",
)
# Track A R22 — '1' (기본) = v2 prompt (few-shot + 부정 규칙)
#                '0'         = R21 v1 prompt (호환용)
USE_PROMPT_V2 = os.getenv("TOPIC_APPLY_PROMPT_V2", "1") == "1"


# ---------------------------------------------------------------------------
# 라벨 머지 — PRESERVE_EXISTING
# ---------------------------------------------------------------------------
def merge_preserve(db_topics: List[str], llm_label: str) -> List[str]:
    """기존 db_topics 를 우선 유지하고, llm_label 이 새로운 의미를 추가하는
    경우에만 multi-label 확장.

    규칙:
      - llm_label == 'other'  → 변경 없음 (DB 우선)
      - llm_label in db_topics → 변경 없음 (중복)
      - 그 외 → db_topics + [llm_label]  (확장)
    """
    if not llm_label or llm_label == "other":
        return list(db_topics)
    if llm_label in db_topics:
        return list(db_topics)
    return list(db_topics) + [llm_label]


# ---------------------------------------------------------------------------
# 샘플링
# ---------------------------------------------------------------------------
async def sample(targets: List[str], per_topic: int, seed: int) -> List[Dict]:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL 환경변수가 비어 있습니다")

    eng = create_async_engine(DATABASE_URL)
    rows: List[Dict] = []
    try:
        async with eng.connect() as conn:
            for topic in targets:
                stmt = text(
                    """
                    SELECT id, topics,
                           COALESCE(content_translated, content_original) AS content
                    FROM voc_records
                    WHERE topics IS NOT NULL
                      AND cardinality(topics) = 1
                      AND topics[1] = :topic
                      AND COALESCE(content_translated, content_original) IS NOT NULL
                      AND char_length(COALESCE(content_translated, content_original)) >= 20
                    ORDER BY md5(id::text || :seed)
                    LIMIT :lim
                    """
                )
                r = await conn.execute(
                    stmt,
                    {"topic": topic, "seed": str(seed), "lim": per_topic},
                )
                for row in r.fetchall():
                    rows.append(
                        {
                            "id": row[0],
                            "db_topics": list(row[1]),
                            "db_primary": topic,
                            "content": row[2],
                        }
                    )
    finally:
        await eng.dispose()

    random.Random(seed).shuffle(rows)
    return rows


# ---------------------------------------------------------------------------
# 요약
# ---------------------------------------------------------------------------
def summarize(rows: List[Dict]) -> Dict:
    pair_counts: Dict[str, Counter] = defaultdict(Counter)
    agree = 0
    expand = 0  # llm 새 라벨 추가 후보
    other_n = 0
    for r in rows:
        pair_counts[r["db_primary"]][r["llm_label"]] += 1
        if r["db_primary"] == r["llm_label"]:
            agree += 1
        elif r["llm_label"] == "other":
            other_n += 1
        else:
            expand += 1
    total = len(rows)
    return {
        "total": total,
        "agree": agree,
        "agree_rate": round(agree / total, 3) if total else 0.0,
        "expand_candidates": expand,
        "other_n": other_n,
        "pair_counts": {k: dict(v) for k, v in pair_counts.items()},
    }


# ---------------------------------------------------------------------------
# APPLY (PRESERVE_EXISTING)
# ---------------------------------------------------------------------------
async def apply_updates(rows: List[Dict]) -> tuple[int, list[int]]:
    """확장 적용 결과 반환.

    Returns
    -------
    (n, affected_ids)
        n             — 실제 UPDATE 한 row 수
        affected_ids  — UPDATE 된 voc_records.id list (R25 트랙 D archive 용)
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL 환경변수가 비어 있습니다")

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    targets = []
    for r in rows:
        new_topics = merge_preserve(r["db_topics"], r["llm_label"])
        if new_topics != r["db_topics"]:
            targets.append((r, new_topics))

    n = len(targets)
    if not n:
        log.info("APPLY: 확장 대상 0건 — 종료")
        await engine.dispose()
        return 0, []

    log.info("APPLY: 확장 대상 %d건, 백업 테이블 %s 준비", n, BACKUP_TABLE)
    async with Session() as db:
        await db.execute(
            text(
                f"""
            CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} (
                id BIGINT PRIMARY KEY,
                topics_before TEXT[] NOT NULL,
                topics_after  TEXT[] NOT NULL,
                applied_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """
            )
        )

        affected_ids: list[int] = []
        for r, new_topics in targets:
            await db.execute(
                text(
                    f"""
                INSERT INTO {BACKUP_TABLE} (id, topics_before, topics_after)
                VALUES (:id, :before, :after)
                ON CONFLICT (id) DO UPDATE
                  SET topics_before = EXCLUDED.topics_before,
                      topics_after  = EXCLUDED.topics_after,
                      applied_at    = NOW()
            """
                ),
                {"id": r["id"], "before": r["db_topics"], "after": new_topics},
            )
            await db.execute(
                text("UPDATE voc_records SET topics = :t WHERE id = :id"),
                {"t": new_topics, "id": r["id"]},
            )
            affected_ids.append(int(r["id"]))
        await db.commit()
    await engine.dispose()
    log.info("APPLY 완료 — %d건 확장, 백업 %s", n, BACKUP_TABLE)
    return n, affected_ids


# ---------------------------------------------------------------------------
# 보고서 + audit
# ---------------------------------------------------------------------------
def write_report(
    out_md: str,
    out_json: str,
    rows: List[Dict],
    summary: Dict,
    applied_n: int,
) -> None:
    today = date.today().isoformat()
    n = len(rows)
    lines: List[str] = []
    lines.append(f"# Topic LLM Apply — Track A ({today})\n")
    lines.append(f"- 모델: `{OLLAMA_EVAL_MODEL}` / prompt: `{'v2' if USE_PROMPT_V2 else 'v1'}`")
    lines.append(
        f"- 샘플: {n}건 (대상 {TARGETS}, topic 당 {PER_TOPIC}건)"
    )
    lines.append(
        f"- DB 라벨 == LLM 라벨: {summary['agree']}건 "
        f"({summary['agree_rate']:.1%})"
    )
    lines.append(f"- 확장 후보 (db≠llm, llm≠other): {summary['expand_candidates']}건")
    lines.append(f"- LLM 'other' 응답: {summary['other_n']}건")
    lines.append(f"- DRY_RUN: {'예 (DB 변경 없음)' if DRY_RUN else '아니오 (실제 적용)'}")
    if not DRY_RUN:
        lines.append(f"- DB 확장: {applied_n}건 (백업 → `{BACKUP_TABLE}`)")
    lines.append("")

    lines.append("## db_primary 별 LLM 라벨 분포\n")
    for db_t, dist in summary["pair_counts"].items():
        lines.append(f"### {db_t} (n={sum(dist.values())})\n")
        for llm_t, c in sorted(dist.items(), key=lambda x: -x[1]):
            mark = " ✅" if llm_t == db_t else ""
            lines.append(f"- {llm_t}: {c}{mark}")
        lines.append("")

    lines.append("## 확장 후보 사례 (최대 30건, db≠llm, llm≠other)\n")
    drifts = [
        r
        for r in rows
        if r["db_primary"] != r["llm_label"] and r["llm_label"] != "other"
    ][:30]
    for r in drifts:
        new = merge_preserve(r["db_topics"], r["llm_label"])
        lines.append(
            f"- id={r['id']} | db={r['db_topics']} + llm=`{r['llm_label']}` → {new} | "
            f"\"{(r['content'] or '')[:200]}\""
        )
    lines.append("")

    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    payload = {
        "date": today,
        "model": OLLAMA_EVAL_MODEL,
        "prompt_version": "v2" if USE_PROMPT_V2 else "v1",
        "targets": TARGETS,
        "per_topic": PER_TOPIC,
        "seed": SEED,
        "dry_run": DRY_RUN,
        "applied_n": applied_n,
        "summary": summary,
        "rows": [
            {
                "id": r["id"],
                "db_primary": r["db_primary"],
                "db_topics": r["db_topics"],
                "llm_label": r["llm_label"],
                "merged": merge_preserve(r["db_topics"], r["llm_label"]),
                "content_excerpt": (r["content"] or "")[:300],
            }
            for r in rows
        ],
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_audit(
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    summary: Dict,
    applied_n: int,
    exc_message: Optional[str] = None,
    affected_ids: Optional[list[int]] = None,
) -> None:
    """backfill_audit.jsonl 호환 라인 1개 append.

    R25 트랙 D — ``affected_ids`` 추가:
      - 100 이하: JSONL 한 줄에 inline.
      - 100 초과: reports/archive/<round>/topic_llm_apply_<run_id>.json 별도 저장,
        JSONL 에는 첫 100개 + 총 카운트 + archive 경로만 기록.
    """
    affected_ids = affected_ids or []
    round_label = (os.getenv("ROUND", "").strip() or "unlabeled")
    inline_cap = 100
    archive_path = ""
    if len(affected_ids) > inline_cap:
        # archive 파일 저장
        try:
            archive_dir = os.path.join(
                os.path.dirname(AUDIT_PATH), "archive", round_label
            )
            os.makedirs(archive_dir, exist_ok=True)
            archive_path = os.path.join(
                archive_dir, f"topic_llm_apply_{run_id}.json"
            )
            with open(archive_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "run_id": run_id,
                        "script": "topic_llm_apply",
                        "mode": "dry_run" if DRY_RUN else "apply",
                        "round": round_label,
                        "started_at": started_at.isoformat(),
                        "finished_at": finished_at.isoformat(),
                        "affected_ids": {"topics_updated": affected_ids},
                        "affected_ids_total": {
                            "topics_updated": len(affected_ids)
                        },
                    },
                    f,
                    ensure_ascii=False,
                )
        except Exception as e:  # pragma: no cover
            log.warning("archive write fail: %s", e)
    inline_ids = affected_ids[:inline_cap]
    entry = {
        "run_id": run_id,
        "script": "topic_llm_apply",
        "mode": "dry_run" if DRY_RUN else "apply",
        "env": {
            "DRY_RUN": DRY_RUN,
            "PRESERVE_EXISTING": True,
            "BACKUP_BEFORE": not DRY_RUN,
            "OLLAMA_EVAL_MODEL": OLLAMA_EVAL_MODEL,
            "TOPIC_APPLY_PER_TOPIC": PER_TOPIC,
            "TOPIC_APPLY_TARGETS": TARGETS,
            "TOPIC_APPLY_SEED": SEED,
            # R24 트랙 E — 라운드 라벨 (ROUND env, default 'unlabeled').
            "round": round_label,
        },
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "status": status,
        "exc_message": exc_message,
        "counters": {
            "target_total": summary.get("total", 0),
            "seen": summary.get("total", 0),
            "matched": summary.get("agree", 0),
            "expand_candidates": summary.get("expand_candidates", 0),
            "applied": applied_n,
        },
        "backup_path": BACKUP_TABLE if (not DRY_RUN and applied_n) else None,
        "notes": [
            f"agree_rate={summary.get('agree_rate', 0):.3f}",
            f"targets={','.join(TARGETS)}",
            f"prompt={'v2' if USE_PROMPT_V2 else 'v1'}",
        ],
        # R25 트랙 D — archive 모드
        "affected_ids": {"topics_updated": inline_ids} if inline_ids else {},
        "affected_ids_total": {"topics_updated": len(affected_ids)} if affected_ids else {},
        "archive_paths": {"topics_updated": archive_path} if archive_path else {},
    }
    os.makedirs(os.path.dirname(AUDIT_PATH), exist_ok=True)
    with open(AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
async def main() -> int:
    run_id = uuid.uuid4().hex[:12]
    started_at = datetime.now(timezone.utc)
    log.info(
        "Apply 시작 — run_id=%s 대상=%s topic당=%d DRY_RUN=%s",
        run_id, TARGETS, PER_TOPIC, DRY_RUN,
    )
    status = "ok"
    exc_message: Optional[str] = None
    summary: Dict = {}
    applied_n = 0
    affected_ids: list[int] = []
    rows: List[Dict] = []

    try:
        rows = await sample(TARGETS, PER_TOPIC, SEED)
        log.info("샘플 %d건 추출", len(rows))

        from openai import OpenAI

        client = OpenAI(api_key="ollama", base_url=OLLAMA_BASE_URL, timeout=60)
        llm_caller = call_llm_v2 if USE_PROMPT_V2 else call_llm
        log.info("prompt: %s", "v2" if USE_PROMPT_V2 else "v1")
        for i, r in enumerate(rows, 1):
            raw = llm_caller(client, r["content"], OLLAMA_EVAL_MODEL)
            r["llm_raw"] = raw or ""
            r["llm_label"] = parse_llm_label(raw or "")
            if i % 25 == 0:
                log.info(
                    "[%d/%d] db=%s llm=%s",
                    i, len(rows), r["db_primary"], r["llm_label"],
                )

        summary = summarize(rows)
        if not DRY_RUN:
            applied_n, affected_ids = await apply_updates(rows)
    except Exception as e:  # pragma: no cover
        status = "error"
        exc_message = str(e)
        log.exception("실패: %s", e)
        raise
    finally:
        finished_at = datetime.now(timezone.utc)
        today = date.today().isoformat()
        out_md = os.path.join(OUT_DIR, f"topic_llm_apply_{today}.md")
        out_json = os.path.join(OUT_DIR, f"topic_llm_apply_{today}.json")
        if rows:
            if not summary:
                summary = summarize(rows)
            write_report(out_md, out_json, rows, summary, applied_n)
        write_audit(
            run_id, started_at, finished_at, status, summary, applied_n,
            exc_message, affected_ids=affected_ids,
        )

    log.info(
        "완료 — agree %.1f%% (n=%d), 확장 후보 %d건, applied=%d",
        summary.get("agree_rate", 0) * 100,
        summary.get("total", 0),
        summary.get("expand_candidates", 0),
        applied_n,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
