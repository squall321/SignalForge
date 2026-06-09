"""topic LLM fallback refine — Track A2 (R19, 2026-06-05).

목적: 분류기가 **약신호** 로만 라벨링 했거나 **단일 라벨** 인 행에 대해
LLM 으로 1차 라벨을 다시 받아, R19 분류기 사전 보강 후보를 추출한다.
(DB 를 직접 수정하지 *않는다* — 보고서만 작성, dry-run 모드 기본)

대상 행 선택 기준 (기본 — 사용자 수정 가능):
  - topics IS NOT NULL AND cardinality(topics) = 1
  - topics[1] IN ('positive_general', 'comparison', 'experience')
  - content NOT NULL

샘플링:
  topic 당 균등 (기본 30건씩 = 90건). 14b 한 건 ~24s · 100건 ~40분.

출력:
  reports/topic_llm_refine_<DATE>.json — 케이스 별 (db_label, llm_label, content)
  reports/topic_llm_refine_<DATE>.md   — 변경 후보 패턴 빈도

환경변수:
  DATABASE_URL                postgresql+asyncpg://...
  OLLAMA_BASE_URL             기본 http://127.0.0.1:11434/v1
  OLLAMA_EVAL_MODEL           기본 qwen2.5:14b
  TOPIC_REFINE_PER_TOPIC      topic 당 샘플 수 (기본 30)
  TOPIC_REFINE_TARGETS        대상 topic CSV (기본 'positive_general,comparison,experience')
  TOPIC_REFINE_SEED           샘플링 seed (기본 20260605)
  TOPIC_REFINE_OUT_DIR        출력 dir (기본 reports/)
  TOPIC_REFINE_APPLY          '1'=DB UPDATE 까지 적용 (기본 '0' = dry-run)
  TOPIC_REFINE_BACKUP_TABLE   APPLY 시 백업 테이블명 (기본 voc_topics_backup_r19)

실행 (dry-run):
  DATABASE_URL=... \\
    /home/koopark/claude/SignalForge/.venv/bin/python -m scripts.topic_llm_refine

실행 (실제 UPDATE — 백업 자동 생성):
  TOPIC_REFINE_APPLY=1 ... -m scripts.topic_llm_refine
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import date
from typing import Dict, List

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("topic_llm_refine")

DATABASE_URL = os.getenv("DATABASE_URL", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
OLLAMA_EVAL_MODEL = os.getenv("OLLAMA_EVAL_MODEL", "qwen2.5:14b")
PER_TOPIC = int(os.getenv("TOPIC_REFINE_PER_TOPIC", "30"))
TARGETS = [
    t.strip()
    for t in os.getenv(
        "TOPIC_REFINE_TARGETS", "positive_general,comparison,experience"
    ).split(",")
    if t.strip()
]
SEED = int(os.getenv("TOPIC_REFINE_SEED", "20260605"))
OUT_DIR = os.getenv(
    "TOPIC_REFINE_OUT_DIR", "/home/koopark/claude/SignalForge/reports"
)
APPLY = os.getenv("TOPIC_REFINE_APPLY", "0") == "1"
BACKUP_TABLE = os.getenv("TOPIC_REFINE_BACKUP_TABLE", "voc_topics_backup_r19")


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
                    stmt, {"topic": topic, "seed": str(seed), "lim": per_topic}
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


def summarize(rows: List[Dict]) -> Dict:
    """db_primary → llm_label 매핑 빈도."""
    pair_counts: Dict[str, Counter] = defaultdict(Counter)
    agree = 0
    for r in rows:
        pair_counts[r["db_primary"]][r["llm_label"]] += 1
        if r["db_primary"] == r["llm_label"]:
            agree += 1
    total = len(rows)
    agree_rate = round(agree / total, 3) if total else 0.0
    # 잠재 변경 후보 (db != llm) 그룹별 카운트
    drift_pairs = Counter()
    for r in rows:
        if r["db_primary"] != r["llm_label"]:
            drift_pairs[(r["db_primary"], r["llm_label"])] += 1
    return {
        "total": total,
        "agree": agree,
        "agree_rate": agree_rate,
        "pair_counts": {k: dict(v) for k, v in pair_counts.items()},
        "top_drift_pairs": [
            {"db": db, "llm": llm, "n": n}
            for (db, llm), n in drift_pairs.most_common(20)
        ],
    }


async def apply_updates(rows: List[Dict]) -> int:
    """APPLY 모드 — 백업 테이블 생성 후 db != llm 행만 topics 를 [llm_label] 로 갱신.

    백업: CREATE TABLE IF NOT EXISTS <BACKUP_TABLE> (id BIGINT PRIMARY KEY,
                                                     topics_before TEXT[],
                                                     applied_at TIMESTAMPTZ).
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL 환경변수가 비어 있습니다")

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    drift = [r for r in rows if r["db_primary"] != r["llm_label"]
             and r["llm_label"] != "other"]
    n_drift = len(drift)
    if not n_drift:
        log.info("APPLY: 변경 대상 0건 — 종료")
        await engine.dispose()
        return 0

    log.info("APPLY: 변경 대상 %d건, 백업 테이블 %s 준비", n_drift, BACKUP_TABLE)
    async with Session() as db:
        await db.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} (
                id BIGINT PRIMARY KEY,
                topics_before TEXT[] NOT NULL,
                topics_after  TEXT[] NOT NULL,
                applied_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))

        for r in drift:
            new_topics = [r["llm_label"]]
            await db.execute(text(f"""
                INSERT INTO {BACKUP_TABLE} (id, topics_before, topics_after)
                VALUES (:id, :before, :after)
                ON CONFLICT (id) DO UPDATE
                  SET topics_before = EXCLUDED.topics_before,
                      topics_after  = EXCLUDED.topics_after,
                      applied_at    = NOW()
            """), {
                "id": r["id"],
                "before": r["db_topics"],
                "after": new_topics,
            })
            await db.execute(
                text("UPDATE voc_records SET topics = :t WHERE id = :id"),
                {"t": new_topics, "id": r["id"]},
            )
        await db.commit()
    await engine.dispose()
    log.info("APPLY 완료 — %d건 갱신, 백업 %s", n_drift, BACKUP_TABLE)
    return n_drift


def write_report(
    out_md: str, out_json: str, rows: List[Dict], summary: Dict, applied_n: int
) -> None:
    today = date.today().isoformat()
    n = len(rows)
    lines: List[str] = []
    lines.append(f"# Topic LLM Refine — Track A2 ({today})\n")
    lines.append(f"- 모델: `{OLLAMA_EVAL_MODEL}`")
    lines.append(f"- 샘플: {n}건 (대상 {TARGETS}, topic 당 {PER_TOPIC}건)")
    lines.append(f"- DB 라벨 == LLM 라벨: {summary['agree']}건 ({summary['agree_rate']:.1%})")
    lines.append(f"- APPLY 모드: {'예' if APPLY else '아니오 (dry-run)'}")
    if APPLY:
        lines.append(f"- DB UPDATE: {applied_n}건 (백업 → `{BACKUP_TABLE}`)")
    lines.append("")

    lines.append("## db_primary 별 LLM 라벨 분포\n")
    for db_t, dist in summary["pair_counts"].items():
        lines.append(f"### {db_t} (n={sum(dist.values())})\n")
        for llm_t, c in sorted(dist.items(), key=lambda x: -x[1]):
            mark = " ✅" if llm_t == db_t else ""
            lines.append(f"- {llm_t}: {c}{mark}")
        lines.append("")

    lines.append("## 잠재 변경 후보 — db → llm 빈도 TOP 20\n")
    lines.append("| db_primary | llm_label | n |")
    lines.append("|---|---|---:|")
    for p in summary["top_drift_pairs"]:
        lines.append(f"| {p['db']} | {p['llm']} | {p['n']} |")
    lines.append("")

    lines.append("## 사례 (최대 30건, db≠llm)\n")
    drifts = [r for r in rows if r["db_primary"] != r["llm_label"]][:30]
    for r in drifts:
        lines.append(
            f"- id={r['id']} | db=`{r['db_primary']}` → llm=`{r['llm_label']}` | "
            f"\"{(r['content'] or '')[:200]}\""
        )
    lines.append("")

    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    payload = {
        "date": today,
        "model": OLLAMA_EVAL_MODEL,
        "targets": TARGETS,
        "per_topic": PER_TOPIC,
        "seed": SEED,
        "apply": APPLY,
        "applied_n": applied_n,
        "summary": summary,
        "rows": [
            {
                "id": r["id"],
                "db_primary": r["db_primary"],
                "db_topics": r["db_topics"],
                "llm_label": r["llm_label"],
                "content_excerpt": (r["content"] or "")[:300],
            }
            for r in rows
        ],
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


async def main() -> int:
    log.info(
        "Refine 시작 — 대상 %s, topic 당 %d건, APPLY=%s",
        TARGETS, PER_TOPIC, APPLY,
    )
    rows = await sample(TARGETS, PER_TOPIC, SEED)
    log.info("샘플 %d건 추출", len(rows))

    from openai import OpenAI

    client = OpenAI(api_key="ollama", base_url=OLLAMA_BASE_URL, timeout=60)
    for i, r in enumerate(rows, 1):
        raw = call_llm(client, r["content"], OLLAMA_EVAL_MODEL)
        r["llm_raw"] = raw or ""
        r["llm_label"] = parse_llm_label(raw or "")
        if i % 10 == 0:
            log.info("[%d/%d] db=%s llm=%s", i, len(rows), r["db_primary"], r["llm_label"])

    summary = summarize(rows)
    applied_n = 0
    if APPLY:
        applied_n = await apply_updates(rows)

    today = date.today().isoformat()
    out_md = os.path.join(OUT_DIR, f"topic_llm_refine_{today}.md")
    out_json = os.path.join(OUT_DIR, f"topic_llm_refine_{today}.json")
    write_report(out_md, out_json, rows, summary, applied_n)
    log.info(
        "완료 — agree %.1f%%, drift %d건, 보고서 %s",
        summary["agree_rate"] * 100,
        summary["total"] - summary["agree"],
        out_md,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
