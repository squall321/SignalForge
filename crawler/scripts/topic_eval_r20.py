"""topic 분류기 R19 LLM eval — *in-process classify* (Track A, 2026-06-05).

목적: R19 변경 (experience 약신호 제거 + 'switched from' 추가 +
       명시 기간 phrase 부스트) 의 per-topic F1 측정. DB 의 R18
       backfill 라벨이 아닌, *현재 코드* 의 classify_topic 결과를
       사용해 R19 효과만 순수 측정한다.

샘플링:
  v2 와 동일한 quota (topic 당 11 + question 1 = 100) 로
  voc_records 의 R18 라벨 분포에서 균등 추출.
  추출 후 *content 만* 사용해 classify_topic 으로 R19 라벨을 재생성.
  → auto_primary_r19 가 정답이 비어 있으면 'no_match' 로 표기.

LLM 평가:
  v1/v2 와 동일 — qwen2.5:14b.

환경변수:
  DATABASE_URL                postgresql+asyncpg://...
  OLLAMA_BASE_URL             기본 http://127.0.0.1:11434/v1
  OLLAMA_EVAL_MODEL           기본 qwen2.5:14b
  TOPIC_EVAL_PER_TOPIC        topic 당 샘플 수 (기본 11)
  TOPIC_EVAL_EXTRA_TOPIC      추가 1건 topic (기본 question)
  TOPIC_EVAL_SEED             샘플링 seed (기본 20260604) — v2 와 동일
  TOPIC_EVAL_OUT_DIR          출력 디렉토리 (기본 reports/)
  TOPIC_EVAL_OUT_SUFFIX       파일 suffix (기본 _r19)

실행:
  /home/koopark/claude/SignalForge/.venv/bin/python \
    -m scripts.topic_eval_r19
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
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

# v1 의 LLM 호출 / 프롬프트 / 파서 재사용
from scripts.topic_eval import (  # noqa: E402
    LLM_LABELS,
    TOPICS,
    build_prompt,  # noqa: F401
    call_llm,
    parse_llm_label,
)

# 현재 코드의 분류기 — *R19 패치 반영*
from nlp.topic_classifier import classify_topic  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("topic_eval_r20")

DATABASE_URL = os.getenv("DATABASE_URL", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
OLLAMA_EVAL_MODEL = os.getenv("OLLAMA_EVAL_MODEL", "qwen2.5:14b")
PER_TOPIC = int(os.getenv("TOPIC_EVAL_PER_TOPIC", "11"))
EXTRA_TOPIC = os.getenv("TOPIC_EVAL_EXTRA_TOPIC", "question")
SEED = int(os.getenv("TOPIC_EVAL_SEED", "20260604"))
OUT_DIR = os.getenv(
    "TOPIC_EVAL_OUT_DIR", "/home/koopark/claude/SignalForge/reports"
)
OUT_SUFFIX = os.getenv("TOPIC_EVAL_OUT_SUFFIX", "_r20")

# R10/R18 baseline (수동 인덱스)
R10_F1 = {
    "positive_general": 0.533,
    "negative_general": 0.632,
    "question": 0.762,
    "comparison": 0.480,
    "price_purchase": 0.476,
    "service_repair": 0.889,
    "experience": 0.600,
    "expectation": 0.842,
    "emotion_only": 0.952,
}
R10_OVERALL = 0.678

R18_V1_F1 = {
    "positive_general": 0.471,
    "negative_general": 0.600,
    "question": 0.750,
    "comparison": 0.414,
    "price_purchase": 0.526,
    "service_repair": 0.900,
    "experience": 0.417,
    "expectation": 0.857,
    "emotion_only": 0.917,
}
R18_V1_OVERALL = 0.640


def topic_quota(per_topic: int, extra_topic: str) -> Dict[str, int]:
    """topic → 샘플 쿼터. extra_topic 에 +1 (총 100건)."""
    q = {t: per_topic for t in TOPICS}
    if extra_topic in q:
        q[extra_topic] += 1
    return q


async def sample_with_quota(quota: Dict[str, int], seed: int) -> List[Dict]:
    """quota 에 따라 topic 별 균등 추출 (v2 와 동일 SQL)."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL 환경변수가 비어 있습니다")

    eng = create_async_engine(DATABASE_URL)
    rows: List[Dict] = []
    try:
        async with eng.connect() as conn:
            for topic, lim in quota.items():
                if lim <= 0:
                    continue
                stmt = text(
                    """
                    SELECT id, topics,
                           COALESCE(content_translated, content_original) AS content
                    FROM voc_records
                    WHERE topics IS NOT NULL
                      AND cardinality(topics) > 0
                      AND topics[1] = :topic
                      AND COALESCE(content_translated, content_original) IS NOT NULL
                    ORDER BY md5(id::text || :seed)
                    LIMIT :lim
                    """
                )
                r = await conn.execute(
                    stmt, {"topic": topic, "seed": str(seed), "lim": lim}
                )
                for row in r.fetchall():
                    rows.append(
                        {
                            "id": row[0],
                            "auto_topics_db": list(row[1]),
                            "db_primary": topic,
                            "content": row[2],
                        }
                    )
    finally:
        await eng.dispose()

    random.Random(seed).shuffle(rows)
    return rows


def reclassify_with_r19(rows: List[Dict]) -> None:
    """각 row 에 대해 R19 분류기 결과를 부착.

    auto_topics_r19 : List[str] (비면 [])
    auto_primary_r19 : str ('no_match' if 비어있음)
    """
    for r in rows:
        out = classify_topic(r["content"], allow_other=False)
        r["auto_topics_r19"] = out
        r["auto_primary_r19"] = out[0] if out else "no_match"


def confusion_r19(rows: List[Dict]) -> Dict[str, Dict[str, int]]:
    """R19 auto_primary → llm_label 빈도."""
    cm: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        cm[r["auto_primary_r19"]][r["llm_label"]] += 1
    return {k: dict(v) for k, v in cm.items()}


def per_topic_metrics_r19(rows: List[Dict]) -> Dict[str, Dict[str, float]]:
    """R19 분류기 결과 기준 precision/recall/F1."""
    auto_T: Counter = Counter(r["auto_primary_r19"] for r in rows)
    llm_T: Counter = Counter(r["llm_label"] for r in rows)
    correct: Counter = Counter(
        r["auto_primary_r19"] for r in rows if r["auto_primary_r19"] == r["llm_label"]
    )

    out: Dict[str, Dict[str, float]] = {}
    for t in TOPICS:
        a = auto_T.get(t, 0)
        l = llm_T.get(t, 0)
        c = correct.get(t, 0)
        prec = c / a if a else 0.0
        rec = c / l if l else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        out[t] = {
            "support": a,
            "llm_count": l,
            "correct": c,
            "precision": round(prec, 3),
            "recall": round(rec, 3),
            "f1": round(f1, 3),
        }
    return out


def overall_accuracy_r19(rows: List[Dict]) -> float:
    if not rows:
        return 0.0
    ok = sum(1 for r in rows if r["auto_primary_r19"] == r["llm_label"])
    return round(ok / len(rows), 3)


def mismatches_r19(rows: List[Dict], limit: int = 12) -> List[Dict]:
    bad = [r for r in rows if r["auto_primary_r19"] != r["llm_label"]]
    return [
        {
            "id": r["id"],
            "db_primary": r["db_primary"],
            "r19_primary": r["auto_primary_r19"],
            "r19_topics": r["auto_topics_r19"],
            "llm": r["llm_label"],
            "content": (r["content"] or "")[:200],
        }
        for r in bad[:limit]
    ]


def no_match_summary(rows: List[Dict]) -> Dict[str, int]:
    """R19 가 분류 안 한 (no_match) 행이 어떤 db_primary 에서 왔는지 빈도."""
    out: Counter = Counter()
    for r in rows:
        if r["auto_primary_r19"] == "no_match":
            out[r["db_primary"]] += 1
    return dict(out)


def write_report(
    out_md: str,
    out_json: str,
    rows: List[Dict],
    cm: Dict[str, Dict[str, int]],
    metrics: Dict[str, Dict[str, float]],
    overall: float,
    bad: List[Dict],
    nomatch: Dict[str, int],
    quota: Dict[str, int],
    model_name: str,
) -> None:
    n = len(rows)
    lines: List[str] = []
    lines.append(f"# Topic 분류기 R19 LLM Spot-Check ({date.today().isoformat()})\n")
    lines.append(f"- 평가 모델: `{model_name}`")
    lines.append(f"- 샘플: {n}건 (DB R18 라벨 분포 기준 quota, R19 코드로 재분류)")
    lines.append(
        f"- 전체 정확도 (R19 primary vs LLM): **{overall:.3f}** "
        f"(R10 {R10_OVERALL:.3f} / R18 v1 {R18_V1_OVERALL:.3f})\n"
    )

    # F1 회귀 표
    lines.append("## R10 / R18 v1 vs R19 F1\n")
    lines.append("| topic | R10 | R18 v1 | R19 | Δ(R19-R18) |")
    lines.append("|---|---:|---:|---:|---:|")
    for t in TOPICS:
        r10 = R10_F1.get(t, 0.0)
        r18 = R18_V1_F1.get(t, 0.0)
        r19 = metrics[t]["f1"]
        d = r19 - r18
        sign = "+" if d >= 0 else ""
        lines.append(
            f"| {t} | {r10:.3f} | {r18:.3f} | {r19:.3f} | {sign}{d:.3f} |"
        )
    d_all = overall - R18_V1_OVERALL
    sign_all = "+" if d_all >= 0 else ""
    lines.append(
        f"| **overall** | **{R10_OVERALL:.3f}** | **{R18_V1_OVERALL:.3f}** "
        f"| **{overall:.3f}** | **{sign_all}{d_all:.3f}** |"
    )
    lines.append("")

    # per-topic 표
    lines.append("## Per-topic 정확도 (R19)\n")
    lines.append("| topic | support | llm_count | correct | precision | recall | F1 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for t in TOPICS:
        m = metrics[t]
        lines.append(
            f"| {t} | {m['support']} | {m['llm_count']} | {m['correct']} "
            f"| {m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} |"
        )
    lines.append("")

    # confusion matrix (R19 ↦ LLM)
    lines.append("## Confusion Matrix (R19 primary → LLM)\n")
    extra = ["no_match"]
    header_labels = LLM_LABELS + extra
    header = "| R19 \\ LLM | " + " | ".join(LLM_LABELS) + " |"
    sep = "|---|" + "|".join(["---:"] * len(LLM_LABELS)) + "|"
    lines.append(header)
    lines.append(sep)
    for t in TOPICS + extra:
        row = cm.get(t, {})
        cells = [str(row.get(label, 0)) for label in LLM_LABELS]
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    lines.append("")

    # no_match 분석
    if nomatch:
        lines.append("## no_match 분석 (R19 가 분류 못한 행의 DB 라벨 분포)\n")
        for db, c in sorted(nomatch.items(), key=lambda x: -x[1]):
            lines.append(f"- {db}: {c}건")
        lines.append("")

    # mismatches
    lines.append("## 잘못 분류 예시 (R19 ≠ LLM, 최대 12건)\n")
    if not bad:
        lines.append("- (없음)\n")
    else:
        for b in bad:
            lines.append(
                f"- id={b['id']} | db=`{b['db_primary']}` r19=`{b['r19_primary']}` "
                f"({b['r19_topics']}) → llm=`{b['llm']}` | \"{b['content']}\""
            )
        lines.append("")

    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    payload = {
        "date": date.today().isoformat(),
        "round": "R19",
        "model": model_name,
        "n_total": n,
        "per_topic": PER_TOPIC,
        "extra_topic": EXTRA_TOPIC,
        "quota": quota,
        "overall_accuracy_r19": overall,
        "r10_overall": R10_OVERALL,
        "r18_v1_overall": R18_V1_OVERALL,
        "delta_vs_r18": round(overall - R18_V1_OVERALL, 3),
        "per_topic_metrics": metrics,
        "r10_per_topic_f1": R10_F1,
        "r18_v1_per_topic_f1": R18_V1_F1,
        "confusion_matrix": cm,
        "no_match": nomatch,
        "mismatches": bad,
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


async def main() -> int:
    quota = topic_quota(PER_TOPIC, EXTRA_TOPIC)
    total = sum(quota.values())
    log.info(
        "샘플링 시작 — quota %s = 총 %d건", quota, total
    )
    rows = await sample_with_quota(quota, SEED)
    log.info("DB 샘플 %d건 추출 (요청 %d)", len(rows), total)

    # R19 분류기로 재분류 (in-process)
    reclassify_with_r19(rows)
    log.info("R19 재분류 완료")

    # LLM 평가
    from openai import OpenAI

    client = OpenAI(api_key="ollama", base_url=OLLAMA_BASE_URL, timeout=60)
    for i, r in enumerate(rows, 1):
        raw = call_llm(client, r["content"], OLLAMA_EVAL_MODEL)
        r["llm_raw"] = raw or ""
        r["llm_label"] = parse_llm_label(raw or "")
        if i % 10 == 0:
            log.info(
                "[%d/%d] r19=%s llm=%s", i, len(rows),
                r["auto_primary_r19"], r["llm_label"]
            )

    cm = confusion_r19(rows)
    metrics = per_topic_metrics_r19(rows)
    overall = overall_accuracy_r19(rows)
    bad = mismatches_r19(rows, limit=12)
    nm = no_match_summary(rows)

    today = date.today().isoformat()
    out_md = os.path.join(OUT_DIR, f"topic_eval_{today}{OUT_SUFFIX}.md")
    out_json = os.path.join(OUT_DIR, f"topic_eval_{today}{OUT_SUFFIX}.json")
    write_report(
        out_md, out_json, rows, cm, metrics, overall, bad, nm, quota, OLLAMA_EVAL_MODEL
    )
    log.info(
        "완료 — R19 정확도 %.3f (R18 v1 %.3f, Δ%+.3f), 보고서 %s",
        overall, R18_V1_OVERALL, overall - R18_V1_OVERALL, out_md,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
