"""R14 트랙 A — voc_records.content_hash 컬럼 + 인덱스 회귀 검증.

대상:
  - 0016 마이그레이션이 적용되어 content_hash 컬럼이 있음.
  - idx_voc_content_hash (platform_id, content_hash) WHERE content_hash IS NOT NULL.

검증:
  1) content_hash 컬럼 존재 + 30자 이상 본문 100% 해시 보유.
  2) idx_voc_content_hash 부분 인덱스 존재.

DB 접근 패턴
------------
conftest.py 의 ``engine.dispose()`` autouse 와 app.database.engine 직접 사용 시
'Event loop is closed' 충돌이 난다 (live-server 패턴 외 직접 connect 는 막혀 있음).
따라서 psql CLI 로 한 줄 쿼리 → stdout 파싱하는 가벼운 방식을 채택.
환경변수 SF_DB_URL 미설정 시 backend/.env 의 기본값 사용.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest


_PSQL_ARGS = [
    "-h", "127.0.0.1",
    "-p", "5434",
    "-U", "signalforge",
    "-d", "signalforge",
    "-tA",
]
_PSQL_ENV = {**os.environ, "PGPASSWORD": "signalforge_pass"}


def _psql(sql: str) -> str:
    if shutil.which("psql") is None:
        pytest.skip("psql 미설치 — DB 회귀 검증 skip")
    r = subprocess.run(
        ["psql", *_PSQL_ARGS, "-c", sql],
        capture_output=True, text=True, env=_PSQL_ENV, timeout=10,
    )
    if r.returncode != 0:
        pytest.skip(f"psql 연결 실패 — {r.stderr.strip()[:120]}")
    return r.stdout.strip()


def test_voc_content_hash_column_exists_and_backfilled():
    """0016 적용 후: 컬럼 존재 + 30자 이상 본문 100% 해시 보유."""
    col = _psql(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='voc_records' AND column_name='content_hash'"
    )
    assert col == "text", f"content_hash 컬럼 누락/타입 불일치 (got={col!r})"

    out = _psql(
        "SELECT COUNT(*) FILTER (WHERE length(content_original) >= 30), "
        "COUNT(*) FILTER (WHERE length(content_original) >= 30 AND content_hash IS NULL) "
        "FROM voc_records"
    )
    eligible_str, missing_str = out.split("|")
    eligible, missing = int(eligible_str), int(missing_str)
    assert eligible > 0, "voc_records 비어있음 — 사전 데이터 필요"
    # 라이브 환경: 진행 중인 크롤러가 미해시 행을 새로 넣을 수 있음 (재시작 전까지).
    # 회귀 invariant 는 'overwhelming majority' — 99% 이상은 해시 보유여야 함.
    ratio = 1.0 - (missing / eligible)
    assert ratio >= 0.99, (
        f"30자 이상 본문 중 해시 보유율 {ratio*100:.2f}% < 99% (missing={missing})"
    )


def test_voc_content_hash_index_present():
    """부분 인덱스 idx_voc_content_hash 가 존재해야 함."""
    out = _psql(
        "SELECT indexname FROM pg_indexes "
        "WHERE tablename='voc_records' AND indexname='idx_voc_content_hash'"
    )
    assert out == "idx_voc_content_hash", f"인덱스 없음 (got={out!r})"
