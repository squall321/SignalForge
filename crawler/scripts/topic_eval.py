"""topic 분류기 정확도 LLM spot-check (Track D, R9, 2026-06-04).

목적: 자동 분류된 topic 라벨을 14b LLM 으로 재검증해 per-topic
precision/recall · confusion matrix · 잘못 분류 예시를 산출.

샘플링:
  voc_records 에서 topics IS NOT NULL & cardinality>0 행 중
  자동 라벨 *single primary topic* 별로 균등 추출 (기본 10건).
  multi-label 인 경우 첫 번째 topic 을 primary 로 간주한다.

LLM 평가:
  qwen2.5:14b (Ollama OpenAI-호환 endpoint).
  프롬프트는 9 카테고리 단일 선택 + 'other'.
  응답은 카테고리 단어 1개 (소문자, 공백 제거).

결과:
  - confusion matrix (auto×llm)
  - per-topic precision/recall/F1
  - 잘못 분류 예시 (auto≠llm) 최대 10건
  - 보고서: reports/topic_eval_<DATE>.md (마크다운)
  - JSON 원본: reports/topic_eval_<DATE>.json

환경변수:
  DATABASE_URL              postgresql+asyncpg://...
  OLLAMA_BASE_URL           기본 http://127.0.0.1:11434/v1
  OLLAMA_EVAL_MODEL         기본 qwen2.5:14b
  TOPIC_EVAL_PER_TOPIC      각 topic 당 샘플 수 (기본 10)
  TOPIC_EVAL_SEED           샘플링 random seed (기본 20260604)
  TOPIC_EVAL_OUT_DIR        출력 디렉토리 (기본 /home/koopark/claude/SignalForge/reports)

실행:
  /home/koopark/claude/SignalForge/.venv/bin/python \
    -m scripts.topic_eval
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
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("topic_eval")

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
TOPICS = [
    "positive_general",
    "negative_general",
    "question",
    "comparison",
    "price_purchase",
    "service_repair",
    "experience",
    "expectation",
    "emotion_only",
]
# LLM 응답에는 'other' 도 허용 (자동 분류기에는 없음 — 누락 시그널)
LLM_LABELS = TOPICS + ["other"]

DATABASE_URL = os.getenv("DATABASE_URL", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
OLLAMA_EVAL_MODEL = os.getenv("OLLAMA_EVAL_MODEL", "qwen2.5:14b")
PER_TOPIC = int(os.getenv("TOPIC_EVAL_PER_TOPIC", "10"))
SEED = int(os.getenv("TOPIC_EVAL_SEED", "20260604"))
OUT_DIR = os.getenv(
    "TOPIC_EVAL_OUT_DIR", "/home/koopark/claude/SignalForge/reports"
)


# ---------------------------------------------------------------------------
# LLM 프롬프트 / 파서
# ---------------------------------------------------------------------------
PROMPT_TEMPLATE = """당신은 한국어/영어 짧은 댓글의 주된 주제(topic)를 분류하는 분류기입니다.

다음 10개 중 *하나* 만 골라 *단어 한 개* 로 답하세요 (소문자, 공백·설명 금지):
- positive_general : 일반적인 긍정 평가 ("좋네요", "최고")
- negative_general : 일반적인 부정 평가 ("별로", "실망")
- question         : 질문 ("어떻게", "되나요?")
- comparison       : 비교/대조/갈아탐
- price_purchase   : 가격/구매/할인/예약/할부
- service_repair   : 수리/서비스센터/리퍼/AS
- experience       : 사용 후기 (기간 언급, "써본 결과")
- expectation      : 출시/루머/기대/유출
- emotion_only     : 감정 표현/이모지만 있는 짧은 글 ("ㅋㅋㅋ", "ㅠㅠ")
- other            : 위 어디에도 맞지 않음

글:
{text}

답:"""


def build_prompt(content: str) -> str:
    # 너무 긴 본문은 앞 800자만 전달 (모델 비용/속도 절감)
    snippet = (content or "").strip()
    if len(snippet) > 800:
        snippet = snippet[:800] + "…"
    return PROMPT_TEMPLATE.format(text=snippet)


def parse_llm_label(raw: str) -> str:
    """LLM 응답에서 topic 라벨 1개만 추출. 매칭 실패 시 'other'."""
    if not raw:
        return "other"
    txt = raw.strip().lower()
    # 첫 줄·첫 단어만 검사 (모델이 가끔 설명을 덧붙임)
    first_line = txt.splitlines()[0]
    # 흔한 prefix 정리
    first_line = first_line.replace("답:", "").replace("답 :", "").strip()
    # 라벨 substring 매칭 — 긴 라벨 우선 (positive_general > positive)
    for label in sorted(LLM_LABELS, key=len, reverse=True):
        if label in first_line:
            return label
    return "other"


def call_llm(client, content: str, model: str) -> Optional[str]:
    """단일 LLM 호출. 실패 시 None."""
    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=20,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": "당신은 한국어/영어 댓글 topic 분류기. 단어 한 개만 답함.",
                },
                {"role": "user", "content": build_prompt(content)},
            ],
        )
        choice = resp.choices[0] if resp.choices else None
        if choice is None or choice.message is None:
            return None
        return (choice.message.content or "").strip()
    except Exception as e:  # pragma: no cover
        log.warning("LLM 호출 실패: %s", e)
        return None


# ---------------------------------------------------------------------------
# 샘플링
# ---------------------------------------------------------------------------
async def sample_per_topic(per_topic: int, seed: int) -> List[Dict]:
    """각 topic 별 per_topic 건씩 균등 무작위 추출.

    primary topic = topics[1] (PostgreSQL 1-based) 기준.
    각 topic 모집단에서 ORDER BY md5(id::text||seed) 로 의사난수 정렬.
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL 환경변수가 비어 있습니다")

    eng = create_async_engine(DATABASE_URL)
    rows: List[Dict] = []
    try:
        async with eng.connect() as conn:
            for topic in TOPICS:
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
                    stmt, {"topic": topic, "seed": str(seed), "lim": per_topic}
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


# ---------------------------------------------------------------------------
# 정확도 집계
# ---------------------------------------------------------------------------
def confusion(rows: List[Dict]) -> Dict[str, Dict[str, int]]:
    """auto_primary → llm_label 빈도."""
    cm: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        cm[r["auto_primary"]][r["llm_label"]] += 1
    return {k: dict(v) for k, v in cm.items()}


def per_topic_metrics(rows: List[Dict]) -> Dict[str, Dict[str, float]]:
    """각 topic 에 대한 precision/recall/F1.

    - precision: auto=T 중 llm=T 비율 (자동 라벨이 LLM 의견과 일치)
    - recall:    llm=T 중 auto=T 비율 (LLM 이 T 라고 본 글 중 자동도 T)
    """
    auto_T: Counter = Counter(r["auto_primary"] for r in rows)
    llm_T: Counter = Counter(r["llm_label"] for r in rows)
    correct: Counter = Counter(
        r["auto_primary"] for r in rows if r["auto_primary"] == r["llm_label"]
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


def overall_accuracy(rows: List[Dict]) -> float:
    if not rows:
        return 0.0
    ok = sum(1 for r in rows if r["auto_primary"] == r["llm_label"])
    return round(ok / len(rows), 3)


def mismatches(rows: List[Dict], limit: int = 10) -> List[Dict]:
    bad = [r for r in rows if r["auto_primary"] != r["llm_label"]]
    return [
        {
            "id": r["id"],
            "auto": r["auto_primary"],
            "llm": r["llm_label"],
            "content": (r["content"] or "")[:200],
        }
        for r in bad[:limit]
    ]


def reinforce_advice(metrics: Dict[str, Dict[str, float]]) -> List[str]:
    """precision < 0.30 인 topic 은 키워드 보강 권고."""
    advice = []
    for t, m in metrics.items():
        if m["support"] > 0 and m["precision"] < 0.30:
            advice.append(
                f"- {t}: precision={m['precision']} (support={m['support']}) — 키워드 사전 보강 권고"
            )
    return advice


# ---------------------------------------------------------------------------
# 보고서
# ---------------------------------------------------------------------------
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
) -> None:
    n = len(rows)
    lines: List[str] = []
    lines.append(f"# Topic 분류기 정확도 LLM Spot-Check ({date.today().isoformat()})\n")
    lines.append(f"- 평가 모델: `{model_name}`")
    lines.append(f"- 샘플: {n}건 (topic 당 {PER_TOPIC}건 균등)")
    lines.append(f"- 전체 정확도: **{overall:.3f}**\n")

    # per-topic 표
    lines.append("## Per-topic 정확도\n")
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
        "model": model_name,
        "n_total": n,
        "per_topic": PER_TOPIC,
        "overall_accuracy": overall,
        "per_topic_metrics": metrics,
        "confusion_matrix": cm,
        "mismatches": bad,
        "advice": advice,
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
async def main() -> int:
    log.info("샘플링 시작 — topic 별 %d건", PER_TOPIC)
    rows = await sample_per_topic(PER_TOPIC, SEED)
    log.info("샘플 %d건 추출", len(rows))

    # LLM 클라이언트 (Ollama OpenAI 호환)
    from openai import OpenAI

    client = OpenAI(api_key="ollama", base_url=OLLAMA_BASE_URL, timeout=60)

    for i, r in enumerate(rows, 1):
        raw = call_llm(client, r["content"], OLLAMA_EVAL_MODEL)
        r["llm_raw"] = raw or ""
        r["llm_label"] = parse_llm_label(raw or "")
        if i % 10 == 0:
            log.info("[%d/%d] auto=%s llm=%s", i, len(rows), r["auto_primary"], r["llm_label"])

    cm = confusion(rows)
    metrics = per_topic_metrics(rows)
    overall = overall_accuracy(rows)
    bad = mismatches(rows, limit=10)
    advice = reinforce_advice(metrics)

    out_md = os.path.join(OUT_DIR, f"topic_eval_{date.today().isoformat()}.md")
    out_json = os.path.join(OUT_DIR, f"topic_eval_{date.today().isoformat()}.json")
    write_report(out_md, out_json, rows, cm, metrics, overall, bad, advice, OLLAMA_EVAL_MODEL)
    log.info(
        "완료 — 정확도 %.3f, 보고서 %s", overall, out_md
    )
    # 임계 체크 — 종료 코드는 항상 0 (보고서가 본분)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
