"""classify_unmapped — Track E NULL % 정책 재정의.

product_id IS NULL 인 voc_records 를 본문 패턴으로 분류하여
``unmapped_reason`` 컬럼에 채운다.

분류 우선순위 (위에서 아래):
  1. ``too_short``        : len(content_original) < 10
  2. ``noise``            : 잠금/회원전용/삭제됨 등 운영 노이즈 패턴 매칭
  3. ``non_galaxy``       : iPhone/Pixel/Xiaomi 만 언급 + Samsung/갤럭시 부재
  4. ``no_model_mention`` : 그 외 (정상 후기지만 모델명 없음)

분류기는 오직 product_id IS NULL 행만 본다.  매칭 가능했어야 하는 행
(relink_products 가 실패한 매칭 실수) 는 ``unmapped_reason`` 도 NULL 로 남기는
정책 — 운영 dashboard 가 'unknown' 으로 표시.

환경변수:
  DATABASE_URL          (필수)
  CLASSIFY_LIMIT        총 처리 상한 (기본 200000, 0=무제한)
  CLASSIFY_BATCH        배치 크기 (기본 5000)
  CLASSIFY_DRY_RUN      '1' 이면 UPDATE 안 함 (기본 '0')
  CLASSIFY_RECLASSIFY   '1' 이면 unmapped_reason IS NOT NULL 도 다시 처리 (기본 '0')

실행:
  DATABASE_URL=postgresql+asyncpg://... \\
    /home/koopark/claude/SignalForge/.venv/bin/python \\
    -m scripts.classify_unmapped
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from collections import Counter
from typing import Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("classify_unmapped")

DATABASE_URL = os.getenv("DATABASE_URL", "")
LIMIT = int(os.getenv("CLASSIFY_LIMIT", "200000"))
BATCH = int(os.getenv("CLASSIFY_BATCH", "5000"))
DRY_RUN = os.getenv("CLASSIFY_DRY_RUN", "0") == "1"
RECLASSIFY = os.getenv("CLASSIFY_RECLASSIFY", "0") == "1"


# ── 분류 패턴 ──────────────────────────────────────────────────────────────
_MIN_LEN = 10

# 운영 노이즈 — 잠금/회원전용/삭제됨/페이지네이션 UI 등.
_NOISE_RE = re.compile(
    r"회원만 볼 수|1시간 내 작성|삭제된 글|로그인 후|"
    r"로그인이 필요|admin 권한|페이지를 찾을 수 없|404 not found|"
    r"등록된 글이 없|등록된 게시물이 없",
    re.IGNORECASE,
)

# Samsung / Galaxy 컨텍스트.  있으면 non_galaxy 아님.
_GALAXY_CTX_RE = re.compile(
    r"galaxy|samsung|갤럭시|삼성|갤(럭시)?|sm-[a-z]\d", re.IGNORECASE
)

# 경쟁사 브랜드 / 모델 — 이것만 있고 Samsung 부재 → non_galaxy.
_NON_GALAXY_RE = re.compile(
    r"\b(iphone|pixel|xiaomi|redmi|huawei|oneplus|oppo|vivo|"
    r"infinix|tecno|realme|asus|nokia|sony xperia|motorola)\b|"
    r"아이폰|픽셀|샤오미|화웨이",
    re.IGNORECASE,
)


def classify_reason(content: Optional[str]) -> str:
    """본문을 분류해서 unmapped_reason 문자열 반환.

    우선순위: too_short > noise > non_galaxy > no_model_mention.
    None/빈 문자열도 too_short 로 분류 (length 0).
    """
    s = content or ""
    if len(s.strip()) < _MIN_LEN:
        return "too_short"
    if _NOISE_RE.search(s):
        return "noise"
    if _NON_GALAXY_RE.search(s) and not _GALAXY_CTX_RE.search(s):
        return "non_galaxy"
    return "no_model_mention"


# ── DB 처리 ────────────────────────────────────────────────────────────────
_SELECT_SQL = text(
    """
    SELECT id, content_original
    FROM voc_records
    WHERE product_id IS NULL
      AND (:reclassify OR unmapped_reason IS NULL)
      AND id > :last_id
    ORDER BY id
    LIMIT :batch
    """
)

_UPDATE_SQL = text(
    """
    UPDATE voc_records
       SET unmapped_reason = :reason
     WHERE id = :id
    """
)


async def _run() -> Counter:
    if not DATABASE_URL:
        log.error("DATABASE_URL 미설정")
        return Counter()
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    counts: Counter = Counter()
    total = 0
    last_id = 0
    async with Session() as session:
        while True:
            if LIMIT and total >= LIMIT:
                log.info("LIMIT %d 도달 — 종료", LIMIT)
                break
            rows = (
                await session.execute(
                    _SELECT_SQL,
                    {
                        "reclassify": RECLASSIFY,
                        "last_id": last_id,
                        "batch": BATCH,
                    },
                )
            ).all()
            if not rows:
                break
            for r in rows:
                reason = classify_reason(r.content_original)
                counts[reason] += 1
                last_id = max(last_id, int(r.id))
                if not DRY_RUN:
                    await session.execute(
                        _UPDATE_SQL, {"reason": reason, "id": int(r.id)}
                    )
                total += 1
            if not DRY_RUN:
                await session.commit()
            log.info(
                "batch %d (누적 %d) — too_short=%d noise=%d non_galaxy=%d no_model=%d",
                len(rows), total,
                counts["too_short"], counts["noise"],
                counts["non_galaxy"], counts["no_model_mention"],
            )
    await engine.dispose()
    return counts


def main() -> None:
    counts = asyncio.run(_run())
    log.info("─" * 60)
    log.info("완료 — total=%d  분포: %s", sum(counts.values()), dict(counts))


if __name__ == "__main__":
    main()
