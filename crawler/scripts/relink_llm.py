"""voc_records.product_id LLM 정규화 — R14 트랙 C.

기존 relink_products.py 의 *정적 사전* 매칭으로 잡히지 않는 본문 중
"Samsung/Galaxy 컨텍스트는 있으나 모델명 추출이 애매한" 경우에 한해
로컬 14b LLM(qwen2.5:14b 기본)으로 *모델명 1개*를 한 줄 답변으로 받아
relink_products.match_product_code() 의 정적 사전을 재시도한다.

대상:
  voc_records WHERE product_id IS NULL
    AND length(content_original) >= 50
    AND content_original ~* '(galaxy|갤럭시|samsung|삼성)'

흐름 (행 1건):
  1. 본문 정규화 → static match (relink_products.match_product_code).
     이미 매칭되면 LLM 호출 없이 그대로 UPDATE.  (안전망)
  2. 정적 매칭 실패 → LLM 호출. system='Samsung Galaxy 모델명 추출기'.
     user=본문 800자 절단.  응답 한 줄(<=40 tokens).
  3. LLM 응답이 'none' / 빈 문자열 / 한글 '없음' / 비정상이면 'no_model' 로 카운트.
  4. LLM 응답 텍스트를 static match 한 번 더 → code 얻으면 product_id UPDATE.
     실패하면 'unmatched_llm_text' 로 카운트하고 raw 응답을 누적 로깅.

환경변수:
  DATABASE_URL          (필수)
  RELINK_LLM_LIMIT      처리할 최대 후보 수 (기본 500)
  RELINK_LLM_BATCH      DB 페치 배치 크기 (기본 100)
  RELINK_LLM_DRY_RUN    '1' → UPDATE 안 함, LLM 만 호출 (기본 '0')
  RELINK_LLM_SKIP_LLM   '1' → LLM 호출 안 함, 정적 사전만 (sanity 모드)
  LLM_MAX_TOKENS        LLM 응답 토큰 한도 (기본 40)
  OLLAMA_BASE_URL       (기본 http://127.0.0.1:11434/v1)
  OPENAI_HIGH_MODEL_SHARED  (기본 qwen2.5:14b — high tier 모델)

실행:
  cd crawler && \\
    DATABASE_URL=postgresql+asyncpg://... \\
    RELINK_LLM_LIMIT=500 \\
    /home/koopark/claude/SignalForge/.venv/bin/python -m scripts.relink_llm
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
from collections import Counter
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession  # noqa: E402

from scripts.relink_products import match_product_code  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("relink_llm")

DATABASE_URL = os.getenv("DATABASE_URL", "")
LIMIT = int(os.getenv("RELINK_LLM_LIMIT", "500"))
BATCH = int(os.getenv("RELINK_LLM_BATCH", "100"))
DRY_RUN = os.getenv("RELINK_LLM_DRY_RUN", "0") == "1"
SKIP_LLM = os.getenv("RELINK_LLM_SKIP_LLM", "0") == "1"
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "40"))

# 본문 너무 길면 LLM context 낭비. 800자 절단.
CONTENT_TRUNC = 800

# LLM system 프롬프트 — 영어/한국어 둘 다 인식, 한 줄 응답 강제.
LLM_SYSTEM = (
    "You extract the exact Samsung Galaxy model name mentioned in a text. "
    "Reply with EXACTLY ONE model name (e.g. 'Galaxy S22 Ultra', 'Note 7', "
    "'Z Fold 5', 'Galaxy Watch 9', 'Galaxy Buds Pro') or 'none' if no specific "
    "Samsung Galaxy model is mentioned. Korean text is OK. Reply on a single "
    "line. No explanations, no markdown, no quotes."
)

# LLM 응답이 'none' 류일 때 — 모델 미언급 카운트.
_NONE_TOKENS = {"none", "n/a", "na", "no", "없음", "no model", "unknown", ""}


def _classify_llm_text(t: str) -> str:
    """LLM 응답을 정규화 — none 류면 'none', 아니면 stripped 텍스트."""
    if not t:
        return "none"
    s = t.strip().strip("'\"`").strip()
    # 한 줄만 사용
    s = s.splitlines()[0].strip() if "\n" in s else s
    if s.lower() in _NONE_TOKENS:
        return "none"
    # 너무 긴 응답(>80자) 은 무시 — 프롬프트 무시한 케이스.
    if len(s) > 80:
        return "none"
    return s


SELECT_SQL = text("""
    SELECT id, content_translated, content_original
    FROM voc_records
    WHERE product_id IS NULL
      AND length(content_original) >= 50
      AND content_original ~* '(galaxy|갤럭시|samsung|삼성)'
      AND id < :cursor
    ORDER BY id DESC
    LIMIT :batch
""")

UPDATE_SQL = text("""
    UPDATE voc_records
    SET product_id = :pid
    WHERE id = :id
""")


async def load_code_to_id(db: AsyncSession) -> dict:
    rows = (await db.execute(text("SELECT id, code FROM products"))).all()
    return {r.code: r.id for r in rows}


def _call_llm(provider, content: str) -> Optional[str]:
    """LLM 1회 호출 — system 프롬프트는 모듈 상수, user 프롬프트는 본문.

    OpenAIProvider/OllamaProvider 가 모두 self._client 를 노출하지만,
    호환성 위해 raw chat.completions 가 아니라 summarize() 를 우회하고
    *직접* low-level _client.chat.completions.create 를 호출한다.
    summarize() 는 SYSTEM_PROMPT_KO (3.4KB) 를 강제 주입하므로 우리 토큰
    예산(40)과 일치하지 않는다. 따라서 lower-level 호출을 사용.
    """
    try:
        client = provider._client  # noqa: SLF001 — 의도된 low-level 접근
        model = provider.model
        resp = client.chat.completions.create(
            model=model,
            max_tokens=LLM_MAX_TOKENS,
            temperature=0.0,
            messages=[
                {"role": "system", "content": LLM_SYSTEM},
                {"role": "user", "content": content[:CONTENT_TRUNC]},
            ],
        )
        choice = resp.choices[0] if resp.choices else None
        if choice is None or choice.message is None:
            return None
        return (choice.message.content or "").strip() or None
    except Exception as e:  # pragma: no cover
        log.warning("LLM 호출 실패: %s", e)
        return None


async def main() -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL 미설정")
        sys.exit(2)

    # LLM provider — high tier 우선 (14b shared ollama).
    provider = None
    if not SKIP_LLM:
        from insight.llm_provider import get_provider
        provider = get_provider(tier="high")
        if provider is None:
            log.error("LLM provider 가용 불가 — SKIP_LLM=1 로만 실행 가능")
            sys.exit(3)
        log.info(
            f"LLM provider={provider.name} tier_label={getattr(provider, 'tier_label', '?')} "
            f"model={getattr(provider, 'model', '?')}"
        )

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        code_to_id = await load_code_to_id(db)
        total = (await db.execute(text("""
            SELECT count(*) FROM voc_records
            WHERE product_id IS NULL
              AND length(content_original) >= 50
              AND content_original ~* '(galaxy|갤럭시|samsung|삼성)'
        """))).scalar_one()

    log.info(
        f"후보 총 {total:,}건 / LIMIT={LIMIT} / BATCH={BATCH} / DRY_RUN={DRY_RUN} "
        f"/ SKIP_LLM={SKIP_LLM} / 등록 code={len(code_to_id)}"
    )

    seen = 0
    static_matched = 0
    llm_called = 0
    llm_none = 0
    llm_matched = 0
    unmatched_llm = 0
    unknown_code = 0
    code_hits: Counter = Counter()
    sample_unmatched: list[tuple[int, str, str]] = []

    cursor = 1 << 62
    t0 = time.time()

    while True:
        async with Session() as db:
            rows = (await db.execute(SELECT_SQL, {"batch": BATCH, "cursor": cursor})).all()
            if not rows:
                log.info("  더 이상 처리할 후보 없음 — 종료")
                break

            ups = []
            for r in rows:
                if seen >= LIMIT:
                    break
                seen += 1
                content = r.content_translated or r.content_original or ""

                # 1) 정적 사전 1차 시도 (안전망 — 후보 추출 SQL 이 정확히 같지 않음)
                code = match_product_code(content)
                if code:
                    pid = code_to_id.get(code)
                    if pid:
                        ups.append({"id": r.id, "pid": pid})
                        code_hits[code] += 1
                        static_matched += 1
                        continue
                    else:
                        unknown_code += 1
                        continue

                if SKIP_LLM:
                    continue

                # 2) LLM 호출
                raw = _call_llm(provider, content)
                llm_called += 1
                norm = _classify_llm_text(raw or "")
                if norm == "none":
                    llm_none += 1
                    continue

                # 3) LLM 응답을 static dict 으로 재매칭
                code2 = match_product_code(norm)
                if not code2:
                    unmatched_llm += 1
                    if len(sample_unmatched) < 30:
                        sample_unmatched.append((r.id, norm[:60], content[:120]))
                    continue
                pid = code_to_id.get(code2)
                if not pid:
                    unknown_code += 1
                    continue
                ups.append({"id": r.id, "pid": pid})
                code_hits[code2] += 1
                llm_matched += 1

            if ups and not DRY_RUN:
                await db.execute(UPDATE_SQL, ups)
                await db.commit()

            cursor = rows[-1].id

            elapsed = time.time() - t0
            log.info(
                f"  진행 seen={seen:,} static={static_matched:,} "
                f"llm_called={llm_called:,} llm_matched={llm_matched:,} "
                f"llm_none={llm_none:,} unmatched_llm={unmatched_llm:,} "
                f"unknown_code={unknown_code:,} "
                f"(UPDATE batch={len(ups)} elapsed={elapsed:.1f}s cursor={cursor})"
            )

        if seen >= LIMIT:
            log.info(f"LIMIT {LIMIT} 도달 — 종료")
            break

    elapsed = time.time() - t0
    matched_total = static_matched + llm_matched
    hit_pct = matched_total * 100.0 / max(seen, 1)
    llm_hit_pct = llm_matched * 100.0 / max(llm_called, 1)
    log.info(
        f"=== relink_llm 완료: seen={seen:,} matched={matched_total:,} "
        f"({hit_pct:.2f}%) elapsed={elapsed:.1f}s ==="
    )
    log.info(
        f"  ├ static_matched : {static_matched:,}"
    )
    log.info(
        f"  ├ llm_matched    : {llm_matched:,} / llm_called {llm_called:,} ({llm_hit_pct:.2f}%)"
    )
    log.info(
        f"  ├ llm_none       : {llm_none:,}"
    )
    log.info(
        f"  ├ unmatched_llm  : {unmatched_llm:,} (LLM 응답이 static 사전과 불일치)"
    )
    log.info(
        f"  └ unknown_code   : {unknown_code:,}"
    )
    log.info("  상위 매칭 code (top 20):")
    for code, n in code_hits.most_common(20):
        log.info(f"    {code:10s} {n:6,}")
    if sample_unmatched:
        log.info("  unmatched_llm 샘플 (id | LLM응답 | 본문):")
        for vid, llm_text, body in sample_unmatched[:15]:
            log.info(f"    {vid:>8d} | {llm_text!r} | {body!r}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
