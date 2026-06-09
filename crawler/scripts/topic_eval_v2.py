"""topic 분류기 정확도 LLM spot-check v2 (Track D, R14, 2026-06-04).

목적: R13 의 92.43% 분류율 + R11 컨텍스트 부스트 적용 데이터의
LLM 정확도 재측정. R10 baseline (0.678) 과 비교해 회귀/개선 확인.

v1 와의 차이:
  - 표본 크기 90 → 100 (topic 당 11건 + 'question' 1 추가)
  - 보고서 파일명 suffix _r13 추가 (R13 데이터 기준)
  - R10 vs R13 per-topic F1 회귀 표 자동 작성

샘플링: voc_records 의 단일 primary topic (topics[1]) 기준 균등 추출.

환경변수:
  DATABASE_URL              postgresql+asyncpg://...
  OLLAMA_BASE_URL           기본 http://127.0.0.1:11434/v1
  OLLAMA_EVAL_MODEL         기본 qwen2.5:14b
  TOPIC_EVAL_PER_TOPIC      각 topic 당 샘플 수 (기본 11)
  TOPIC_EVAL_EXTRA_TOPIC    추가 1건을 뽑을 topic (기본 question)
  TOPIC_EVAL_SEED           샘플링 random seed (기본 20260604)
  TOPIC_EVAL_OUT_DIR        출력 디렉토리 (기본 reports/)
  TOPIC_EVAL_OUT_SUFFIX     파일명 suffix (기본 _r13)

실행:
  /home/koopark/claude/SignalForge/.venv/bin/python \
    -m scripts.topic_eval_v2
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
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# v1 의 순수 함수/상수 재사용 — 중복 방지
from scripts.topic_eval import (  # noqa: E402
    TOPICS,
    LLM_LABELS,
    build_prompt,
    parse_llm_label,
    call_llm,
    confusion,
    per_topic_metrics,
    overall_accuracy,
    mismatches,
    reinforce_advice,
)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("topic_eval_v2")

DATABASE_URL = os.getenv("DATABASE_URL", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
OLLAMA_EVAL_MODEL = os.getenv("OLLAMA_EVAL_MODEL", "qwen2.5:14b")
PER_TOPIC = int(os.getenv("TOPIC_EVAL_PER_TOPIC", "11"))
EXTRA_TOPIC = os.getenv("TOPIC_EVAL_EXTRA_TOPIC", "question")
SEED = int(os.getenv("TOPIC_EVAL_SEED", "20260604"))
OUT_DIR = os.getenv(
    "TOPIC_EVAL_OUT_DIR", "/home/koopark/claude/SignalForge/reports"
)
OUT_SUFFIX = os.getenv("TOPIC_EVAL_OUT_SUFFIX", "_r13")

# R10 baseline (reports/topic_eval_2026-06-04_r10.md, 90 samples, 10/topic)
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


def topic_quota(per_topic: int, extra_topic: str) -> Dict[str, int]:
    """topic → 샘플 쿼터. extra_topic 에 +1 (총 100건)."""
    q = {t: per_topic for t in TOPICS}
    if extra_topic in q:
        q[extra_topic] += 1
    return q


async def sample_with_quota(quota: Dict[str, int], seed: int) -> List[Dict]:
    """quota 에 따라 topic 별 균등 추출."""
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
                            "auto_topics": list(row[1]),
                            "auto_primary": topic,
                            "content": row[2],
                        }
                    )
    finally:
        await eng.dispose()

    random.Random(seed).shuffle(rows)
    return rows


def regression_table(metrics: Dict[str, Dict[str, float]], overall_now: float) -> List[str]:
    """R10 vs R13 per-topic F1 비교 표 (마크다운 줄 리스트)."""
    lines: List[str] = []
    lines.append("## R10 vs R13 F1 회귀\n")
    lines.append("| topic | R10 F1 | R13 F1 | Δ |")
    lines.append("|---|---:|---:|---:|")
    for t in TOPICS:
        r10 = R10_F1.get(t, 0.0)
        r13 = metrics[t]["f1"]
        delta = r13 - r10
        sign = "+" if delta >= 0 else ""
        lines.append(f"| {t} | {r10:.3f} | {r13:.3f} | {sign}{delta:.3f} |")
    d_all = overall_now - R10_OVERALL
    sign_all = "+" if d_all >= 0 else ""
    lines.append(
        f"| **overall** | **{R10_OVERALL:.3f}** | **{overall_now:.3f}** | **{sign_all}{d_all:.3f}** |"
    )
    lines.append("")
    return lines


def write_report(
    out_md: str,
    out_json: str,
    rows: List[Dict],
    cm: Dict[str, Dict[str, int]],
    metrics: Dict[str, Dict[str, float]],
    overall: float,
    bad: List[Dict],
    advice: List[str],
    model_name: str,
    quota: Dict[str, int],
) -> None:
    n = len(rows)
    lines: List[str] = []
    lines.append(
        f"# Topic 분류기 정확도 LLM Spot-Check v2 (R13 baseline, {date.today().isoformat()})\n"
    )
    lines.append(f"- 평가 모델: `{model_name}`")
    lines.append(f"- 샘플: {n}건 (topic 당 {PER_TOPIC}건 + `{EXTRA_TOPIC}` 1건 추가)")
    lines.append(f"- 전체 정확도: **{overall:.3f}** (R10: {R10_OVERALL:.3f})")
    lines.append(
        f"- 데이터 기준: R13 (voc_records 168,112 · topics_filled 92.43% · R11 컨텍스트 부스트 적용)\n"
    )

    # R10 vs R13 회귀 표
    lines.extend(regression_table(metrics, overall))

    # per-topic 표
    lines.append("## Per-topic 정확도 (R13)\n")
    lines.append("| topic | support | llm_count | correct | precision | recall | F1 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for t in TOPICS:
        m = metrics[t]
        lines.append(
            f"| {t} | {m['support']} | {m['llm_count']} | {m['correct']} "
            f"| {m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} |"
        )
    lines.append("")

    # confusion matrix
    lines.append("## Confusion Matrix (auto → llm)\n")
    header = "| auto \\ llm | " + " | ".join(LLM_LABELS) + " |"
    sep = "|---|" + "|".join(["---:"] * len(LLM_LABELS)) + "|"
    lines.append(header)
    lines.append(sep)
    for t in TOPICS:
        row = cm.get(t, {})
        cells = [str(row.get(label, 0)) for label in LLM_LABELS]
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    lines.append("")

    # mismatches
    lines.append("## 잘못 분류 예시 (auto ≠ llm, 최대 10건)\n")
    if not bad:
        lines.append("- (없음)\n")
    else:
        for b in bad:
            lines.append(
                f"- id={b['id']} | auto=`{b['auto']}` → llm=`{b['llm']}` | "
                f"\"{b['content']}\""
            )
        lines.append("")

    # 권고
    lines.append("## 사전 보강 권고\n")
    if advice:
        lines.extend(advice)
    else:
        lines.append("- (없음 — 모든 topic 의 precision ≥ 0.30)")
    lines.append("")

    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    payload = {
        "date": date.today().isoformat(),
        "round": "R13",
        "model": model_name,
        "n_total": n,
        "per_topic": PER_TOPIC,
        "extra_topic": EXTRA_TOPIC,
        "quota": quota,
        "overall_accuracy": overall,
        "r10_overall_accuracy": R10_OVERALL,
        "delta_overall": round(overall - R10_OVERALL, 3),
        "per_topic_metrics": metrics,
        "r10_per_topic_f1": R10_F1,
        "confusion_matrix": cm,
        "mismatches": bad,
        "advice": advice,
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


async def main() -> int:
    quota = topic_quota(PER_TOPIC, EXTRA_TOPIC)
    total = sum(quota.values())
    log.info("샘플링 시작 — topic 별 %d건 (+%s 1건) = 총 %d건", PER_TOPIC, EXTRA_TOPIC, total)
    rows = await sample_with_quota(quota, SEED)
    log.info("샘플 %d건 추출 (요청 %d)", len(rows), total)

    from openai import OpenAI

    client = OpenAI(api_key="ollama", base_url=OLLAMA_BASE_URL, timeout=60)

    for i, r in enumerate(rows, 1):
        raw = call_llm(client, r["content"], OLLAMA_EVAL_MODEL)
        r["llm_raw"] = raw or ""
        r["llm_label"] = parse_llm_label(raw or "")
        if i % 10 == 0:
            log.info(
                "[%d/%d] auto=%s llm=%s", i, len(rows), r["auto_primary"], r["llm_label"]
            )

    cm = confusion(rows)
    metrics = per_topic_metrics(rows)
    overall = overall_accuracy(rows)
    bad = mismatches(rows, limit=10)
    advice = reinforce_advice(metrics)

    today = date.today().isoformat()
    out_md = os.path.join(OUT_DIR, f"topic_eval_{today}{OUT_SUFFIX}.md")
    out_json = os.path.join(OUT_DIR, f"topic_eval_{today}{OUT_SUFFIX}.json")
    write_report(out_md, out_json, rows, cm, metrics, overall, bad, advice, OLLAMA_EVAL_MODEL, quota)
    log.info(
        "완료 — 정확도 %.3f (R10 %.3f, Δ%+.3f), 보고서 %s",
        overall, R10_OVERALL, overall - R10_OVERALL, out_md,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
