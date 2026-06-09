"""topic_llm_apply main run (Track A R23) — 단위 테스트.

R22 에서 dry-run (52건 / 13 per_topic) 으로 v2 prompt 안정성 (agree 55.8%) 을
확인한 뒤, R23 본런 (500건 / 125 per_topic) 으로 PRESERVE_EXISTING 모드의
multi-label 확장 동작이 다음 두 가지를 모두 만족하는지를 보장한다.

  1. PRESERVE_EXISTING 모드는 *기존 라벨을 절대 덮어쓰지 않는다*
     — db != llm 케이스에서 결과는 항상 [db_first, ..., llm_label] 형태.
  2. apply_updates() 가 *확장 대상만* SQL UPDATE 한다
     — agree/other 행은 SQL 호출에 포함되지 않는다 (불필요 쓰기 방지).

DB / 네트워크는 사용하지 않는다. AsyncMock 으로 세션을 대체한다.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.topic_llm_apply import apply_updates, merge_preserve, summarize


# ---------------------------------------------------------------------------
# 1. PRESERVE_EXISTING — 기존 라벨 덮어쓰기 금지 (R18 폭락 가드)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "db_topics, llm_label",
    [
        (["positive_general"], "comparison"),
        (["experience"], "negative_general"),
        (["comparison"], "experience"),
        (["negative_general"], "experience"),
    ],
)
def test_main_run_preserve_existing_never_overwrites(db_topics, llm_label):
    """본런 모드에서도 PRESERVE_EXISTING 은 db_topics[0] 을 항상 1번째로 보존한다.

    R18 회귀 — LLM 라벨이 db 와 달라도 *기존 라벨을 절대 덮어쓰지 않는다.*
    """
    merged = merge_preserve(db_topics, llm_label)
    # 1) 기존 첫번째 라벨이 결과의 첫번째 그대로 유지 (단일라벨 → multi-label 확장)
    assert merged[0] == db_topics[0]
    # 2) llm_label 이 *추가* 됨
    assert llm_label in merged
    # 3) 결과 길이 = 1 + 1 (확장 1건)
    assert len(merged) == len(db_topics) + 1


# ---------------------------------------------------------------------------
# 2. apply_updates — 확장 대상만 SQL UPDATE
# ---------------------------------------------------------------------------
def test_apply_updates_only_writes_expand_targets(monkeypatch):
    """apply_updates() 는 agree/other 행에 대해 voc_records 를 UPDATE 하지 않는다.

    500건 본런에서 약 220건만 확장 후보일 때, 나머지 280건은 SQL UPDATE 호출이
    *전혀* 발생하지 않아야 한다 (불필요한 DB 쓰기 방지).
    """
    rows: List[Dict[str, Any]] = [
        # agree — UPDATE 없음
        {"id": 1, "db_topics": ["positive_general"], "db_primary": "positive_general",
         "llm_label": "positive_general", "content": "좋아요"},
        # other — UPDATE 없음
        {"id": 2, "db_topics": ["comparison"], "db_primary": "comparison",
         "llm_label": "other", "content": "그냥 글"},
        # expand — UPDATE 1회
        {"id": 3, "db_topics": ["comparison"], "db_primary": "comparison",
         "llm_label": "experience", "content": "한 달 써본 후기"},
        # expand — UPDATE 1회
        {"id": 4, "db_topics": ["experience"], "db_primary": "experience",
         "llm_label": "comparison", "content": "S25 vs S24 비교"},
    ]

    # AsyncSession.execute 를 spy 한다
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    # async with Session() as db: ...  컨텍스트 매니저
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    session_maker = MagicMock(return_value=session_cm)

    engine = MagicMock()
    engine.dispose = AsyncMock()

    with patch("scripts.topic_llm_apply.create_async_engine", return_value=engine), \
         patch("scripts.topic_llm_apply.async_sessionmaker", return_value=session_maker), \
         patch("scripts.topic_llm_apply.DATABASE_URL", "postgresql+asyncpg://x"):
        # R25 트랙 D — apply_updates 가 (n, affected_ids) tuple 반환으로 확장됨
        applied_n, affected_ids = asyncio.run(apply_updates(rows))

    # 결과: 확장 후보 2건만 적용
    assert applied_n == 2
    # R25 트랙 D — 확장 대상 PK 가 affected_ids 에 정확히 누적
    assert sorted(affected_ids) == [3, 4]

    # SQL 호출 분석:
    #   - CREATE TABLE 1회
    #   - INSERT INTO backup_table × 2 (확장 대상별)
    #   - UPDATE voc_records  × 2 (확장 대상별)
    # 총 5회 execute, agree/other 는 호출되지 않음
    assert session.execute.await_count == 5

    # 호출 시 전달된 id 가 확장 대상 (3, 4) 인지 검증
    update_ids: list[int] = []
    for call in session.execute.await_args_list:
        params = call.args[1] if len(call.args) > 1 else None
        if isinstance(params, dict) and "id" in params and "t" in params:
            update_ids.append(params["id"])
    assert sorted(update_ids) == [3, 4]
    # commit 1회
    session.commit.assert_awaited_once()
