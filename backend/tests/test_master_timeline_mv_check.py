"""alembic 0014 galaxy_master_timeline MV 검증 — R12 트랙 E3 (2026-06-04).

검증 항목:
  1. MV 객체 존재 (pg_class.relkind='m').
  2. unique index (REFRESH CONCURRENTLY 전제) + series/product 인덱스 존재.
  3. MV 의 컬럼 schema — instructions 의 (product_code, name_ko, released_at,
     series, month, voc_count, sent_avg, neg_rate) 8개 컬럼이 정확히 있음.
  4. 실 데이터 ≥ 1행 (운영 환경에서 products × voc_records 가 비어있지 않다는 가정).
  5. backend/api/_internal regression-baseline 응답의 alembic_head ≥ "0014".

라이브 backend 가 없어도 항목 1-4 는 psql 로 직접 검증.
"""
import os
import subprocess

import httpx
import pytest


PG = {
    "host": os.getenv("POSTGRES_HOST", "127.0.0.1"),
    "port": os.getenv("POSTGRES_PORT", "5434"),
    "user": os.getenv("POSTGRES_USER", "signalforge"),
    "db": os.getenv("POSTGRES_DB", "signalforge"),
    "password": os.getenv("PGPASSWORD", "signalforge_pass"),
}
BACKEND = os.getenv("SF_BACKEND_URL", "http://127.0.0.1:8000")


def _psql(sql: str) -> str:
    cmd = [
        "psql", "-h", PG["host"], "-p", PG["port"], "-U", PG["user"],
        "-d", PG["db"], "-tA", "-v", "ON_ERROR_STOP=1", "-c", sql,
    ]
    env = {**os.environ, "PGPASSWORD": PG["password"]}
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=10)
    assert r.returncode == 0, f"psql failed: {r.stderr}"
    return r.stdout.strip()


def _pg_available() -> bool:
    try:
        return bool(_psql("SELECT 1;"))
    except Exception:
        return False


@pytest.mark.skipif(not _pg_available(), reason="postgres 미가동")
def test_mv_object_exists():
    """galaxy_master_timeline MV 객체가 존재해야."""
    rel = _psql(
        "SELECT relname FROM pg_class "
        "WHERE relkind='m' AND relname='galaxy_master_timeline';"
    )
    assert rel == "galaxy_master_timeline", rel


@pytest.mark.skipif(not _pg_available(), reason="postgres 미가동")
def test_mv_unique_index_present():
    """REFRESH CONCURRENTLY 를 위한 unique index 가 있어야."""
    idx = _psql(
        "SELECT indexname FROM pg_indexes "
        "WHERE tablename='galaxy_master_timeline' AND indexname='ix_gmt_unique';"
    )
    assert idx == "ix_gmt_unique", idx


@pytest.mark.skipif(not _pg_available(), reason="postgres 미가동")
def test_mv_secondary_indexes_present():
    """검색용 series_month / product_month index 둘 다 있어야."""
    out = _psql(
        "SELECT indexname FROM pg_indexes "
        "WHERE tablename='galaxy_master_timeline' "
        "AND indexname IN ('ix_gmt_series_month','ix_gmt_product_month') "
        "ORDER BY indexname;"
    )
    names = set(out.splitlines())
    assert names == {"ix_gmt_product_month", "ix_gmt_series_month"}, names


@pytest.mark.skipif(not _pg_available(), reason="postgres 미가동")
def test_mv_schema_columns():
    """MV 컬럼이 instructions 명세와 일치 (8개)."""
    out = _psql(
        "SELECT attname FROM pg_attribute "
        "WHERE attrelid='galaxy_master_timeline'::regclass "
        "AND attnum > 0 AND NOT attisdropped ORDER BY attnum;"
    )
    cols = out.splitlines()
    expected = [
        "product_code", "name_ko", "released_at", "series",
        "month", "voc_count", "sent_avg", "neg_rate",
    ]
    assert cols == expected, (cols, expected)


@pytest.mark.skipif(not _pg_available(), reason="postgres 미가동")
def test_mv_has_rows():
    """실 운영 데이터에서 MV 가 비어있지 않아야 (products × voc 가 0이 아니면)."""
    n = int(_psql("SELECT count(*) FROM galaxy_master_timeline;"))
    assert n >= 1, f"MV 빈 — REFRESH 누락 가능: {n}"


@pytest.mark.skipif(not _pg_available(), reason="postgres 미가동")
def test_alembic_head_at_or_above_0014():
    """alembic_version 이 0014 (또는 그 이상) 인지."""
    head = _psql("SELECT version_num FROM alembic_version LIMIT 1;")
    # 4자리 zero-padded 문자열 비교
    assert head >= "0014", f"alembic head={head!r} < 0014"


def _backend_alive() -> bool:
    try:
        return httpx.get(f"{BACKEND}/health", timeout=1.5).status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _backend_alive(), reason="backend 미가동")
def test_regression_baseline_reflects_0014():
    """backend regression-baseline 응답이 alembic_head 0014+ 를 보고."""
    r = httpx.get(f"{BACKEND}/api/v1/_internal/regression-baseline", timeout=20.0)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("alembic_head", "") >= "0014", body
    assert body.get("alembic_ok") is True, body
